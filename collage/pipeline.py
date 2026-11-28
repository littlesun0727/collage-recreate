"""Return the analysis first, then assemble the scene after parallel provider calls."""
import base64
import io
import math
from contextvars import copy_context
from queue import Queue
from concurrent.futures import Future, ThreadPoolExecutor, wait
from zipfile import ZipFile
from PIL import Image, ImageOps

from . import background, cutout, reveal, vlm
from .object_roles import is_customer_photo
from .timings import log
from .storage import read, save, sha


def decode_image(encoded, path):
    return prepare_image(io.BytesIO(base64.b64decode(encoded, validate=True)), path)


def prepare_image(source_file, path, *, allow_mpo=False):
    with oriented_photo(source_file, allow_mpo=allow_mpo) as im:
        im.save(path)
        return list(im.size)


def oriented_photo(source_file, *, allow_mpo=True):
    with Image.open(source_file, formats=("PNG", "JPEG", "WEBP")) as source:
        if source.width * source.height > 40_000_000:
            raise ValueError(f"Image dimensions {source.width}x{source.height} exceed 40 megapixels")
        if getattr(source, "n_frames", 1) != 1:
            if allow_mpo and source.format == "MPO":
                source.seek(0)
            else:
                raise ValueError(f"Expected a static photo; found {source.n_frames} frames in {source.format}")
        return ImageOps.exif_transpose(source).convert("RGBA")


def first(run, request):
    prepared = run/"prepared"
    prepared.mkdir(parents=True)
    size = decode_image(request["reference"], prepared/"reference.png")
    catalog, seen = [], set()
    for index, asset in enumerate(request["assets"]):
        client_id = asset["asset_id"]
        if not isinstance(client_id, str) or not client_id.strip() or client_id in seen:
            raise ValueError("Client asset IDs must be nonempty unique strings")
        seen.add(client_id)
        ident = f"material_{index:03d}"
        path = prepared/(ident + ".png")
        dims = decode_image(asset["image"], path)
        catalog.append({"id": ident, "asset_id": client_id, "size": dims, "sha256": sha(path)})
    save(run/"catalog.json", catalog)
    analysis = vlm.analyze(run, size)
    save(run/"analysis.json", analysis, indent=2)
    return analysis


def write_png_tile(archive, path, canvas_size):
    """Trim only fully transparent margins, after all canvas-space processing."""
    member = "layers/" + path.name
    with Image.open(path) as image:
        if image.format != "PNG" or list(image.size) != canvas_size:
            raise ValueError("Fixed layers must be canvas-aligned PNGs")
        rgba = image if image.mode == "RGBA" else image.convert("RGBA")
        box = rgba.getchannel("A").getbbox()
        if box is None:
            return None
        left, top, right, bottom = box
        if box == (0, 0, image.width, image.height):
            archive.write(path, member)
        else:
            with archive.open(member, "w") as target:
                rgba.crop(box).save(target, format="PNG")
    return {"type": "png", "file": member, "placement": "tile",
            "offset": [left, top], "size": [right-left, bottom-top]}


# Shared by concurrent benchmark cases and HTTP jobs in this process.
_MASK_WORKERS = ThreadPoolExecutor(max_workers=5, thread_name_prefix="cutout")


def required_assets(analysis, catalog, bindings):
    slots = {o["id"] for o in analysis["objects"] if o.get("mode") in ("cutout", "popout") and is_customer_photo(o)}
    required = {b["asset_id"] for b in bindings if b["slot_id"] in slots and not b.get("skip_popout")}
    return (asset for asset in catalog if asset["id"] in required)


def build_mask(run, asset, originals):
    ident = asset["id"]
    path = originals[asset["asset_id"]] if originals is not None else run/"prepared"/(ident+".png")
    with oriented_photo(path) as image:
        cutout.subject(image, run/"cutouts", ident)
    return run/"cutouts"/(ident+"-mask.png")


def batch_masks(run, assets, originals):
    futures = {}
    try:
        for asset in assets:
            if asset["id"] not in futures:
                futures[asset["id"]] = _MASK_WORKERS.submit(copy_context().run, build_mask, run, asset, originals)
        return {ident: future.result() for ident, future in futures.items()}
    finally:
        for future in futures.values():
            future.cancel()
        # Finish any in-flight writes before releasing the case lock.
        if futures:
            wait(futures.values())


def build_masks(run, analysis, catalog, bindings, originals):
    return batch_masks(run, required_assets(analysis, catalog, bindings), originals)


def popout_crop(obj, mask_path, source):
    """Fit the same customer-photo transform to the subject target and the window."""
    with Image.open(mask_path) as mask:
        w, h = mask.size
        subject = mask.point(lambda a: 255 if a > 8 else 0).getbbox()
    if not subject:
        raise ValueError(f"Empty popout mask for {obj['id']}")
    l,t,r,b = obj["bbox"]
    pl,pt,pr,pb = obj["popout_bbox"]
    sl,st,sr,sb = subject
    angle = math.radians(obj.get("rotation", 0))
    c,s = math.cos(angle),math.sin(angle)
    sw,sh = sr-sl,sb-st
    scale = max((r-l)/w, (b-t)/h, min((pr-pl)/(abs(c)*sw+abs(s)*sh),
                                        (pb-pt)/(abs(s)*sw+abs(c)*sh)))
    cw,ch = (r-l)/scale,(b-t)/scale
    dx,dy = (pl+pr-l-r)/2,(pt+pb-t-b)/2
    horizontal = (c*dx+s*dy)/scale
    if source["mirror_x"]:
        horizontal = -horizontal
    x = max(0,min(w-cw,(sl+sr-cw)/2-horizontal))
    y = max(0,min(h-ch,(st+sb-ch)/2-(-s*dx+c*dy)/scale))
    return [x/w,y/h,(x+cw)/w,(y+ch)/h]


def deliver(run, analysis, catalog, bindings, files, outputs=None, masks=None):
    if outputs is None:
        outputs = run
    sources = {a["id"]: a["asset_id"] for a in catalog}
    photos = {p["slot_id"]: p for p in bindings}
    objects = {o["id"]: o for o in analysis["objects"]}
    lettering = read(run/"lettering.json") if (run/"lettering.json").is_file() else {}
    layers = []
    with ZipFile(run/"delivery.tmp", "w") as archive:
        for ident, path in (masks or {}).items():
            archive.write(path, f"masks/{ident}.png")
        # Older Reveal manifests may list retained objects in extraction order.
        manifest = run/"reveal/scene-order.json"
        order = analysis["layer_order"]
        if manifest.is_file():
            saved = read(manifest)
            if isinstance(saved, list):
                retained = set(saved)
                order = [oid for oid in order if oid in retained]
            else:
                order = saved["layer_order"]
        for oid in order:
            obj = objects[oid]
            if obj["kind"] == "text" and lettering.get(oid, {}).get("action") == "embedded":
                log(f"{oid} 文字已保留在承载层，跳过独立 PNG")
                continue
            if is_customer_photo(obj):
                binding = photos[oid]
                if binding.get("skip_popout"):
                    continue
                source = {"type": "customer_asset", "asset_id": sources[binding["asset_id"]],
                          **{k: binding[k] for k in ("source_crop", "crop_center", "mirror_x")}}
                if obj.get("mode") in ("cutout", "popout") and masks is not None:
                    ident = binding["asset_id"]
                    source["mask_file"] = "masks/" + masks[ident].stem.removesuffix("-mask") + ".png"
                    if obj.get("mode") == "popout":
                        source["source_crop"] = popout_crop(obj, masks[ident], source)
            else:
                if obj["kind"] in ("overlay", "frame") and oid not in files:
                    log(f"{oid} 缺少提取结果，跳过交付")
                    continue  # Reveal omitted this decoration from its response.
                log(f"{oid} 裁切透明边缘并打包 {files[oid].name}")
                source = write_png_tile(archive, files[oid], analysis["reference_size"])
                if source is None:
                    log(f"{oid} 全透明，跳过交付")
                    continue
            layer = {**obj, "source": source}
            if oid in lettering:
                layer["lettering"] = lettering[oid]
            layers.append(layer)
        positions = {layer["id"]: i for i, layer in enumerate(layers)}
        for i, layer in enumerate(layers):
            if "popout_above" in layer and positions.get(layer["popout_above"], -1) <= i:
                del layer["popout_above"]
        scene = {"schema_version": "harmony-collage-scene-v2", "canvas_size": analysis["reference_size"],
                 "layers": layers}
        save(run/"scene.tmp", scene, indent=2)
        archive.write(run/"scene.tmp", "scene.json")
    (run/"scene.tmp").replace(outputs/"scene.json")
    (run/"delivery.tmp").replace(outputs/"delivery.zip")
    log(f"已发布 scene.json 和 delivery.zip，共 {len(layers)} 层")


class StageError(RuntimeError):
    def __init__(self, stage, cause):
        self.stage = stage
        super().__init__(f"{stage}: {cause}")


def run_stage(stage, func, *args):
    try:
        return func(*args)
    except StageError:
        raise
    except Exception as exc:
        raise StageError(stage, exc) from exc


def bind_and_save(run, analysis, catalog, on_cutout=None):
    if isinstance(catalog, Future):
        from .light_binding import match
        bindings = match(analysis, catalog.result(), on_cutout=on_cutout)
    else:
        bindings = vlm.bind(run, analysis, catalog)
        if on_cutout is not None:
            for asset in required_assets(analysis, catalog, bindings):
                on_cutout(asset)
    save(run/"bindings.json", bindings, indent=2)
    for row in bindings:
        log(f"选片 {row['slot_id']} → {row['asset_id']}，裁切 {row['source_crop']}")
    return bindings


def extract_and_save(run, analysis, clean):
    files = reveal.extract(run, analysis, clean)
    save(run/"fixed-index.json", {oid: path.relative_to(run).as_posix()
                                  for oid, path in files.items()}, indent=2)
    return files


def assemble(run, outputs, analysis, catalog, timings=None, originals=None):
    def measured(stage, func, *args):
        if timings is None:
            return run_stage(stage, func, *args)
        with timings.stage(stage):
            return run_stage(stage, func, *args)

    bg = next(obj for obj in analysis["objects"] if obj["id"] == analysis["layer_order"][0])
    if timings:
        clean_plan = "并行清底" if bg.get("background_type") == "reference_clean" else "跳过清底"
        timings.emit("assemble", f"并行启动 binding、cutout_masks 和 reveal；背景 {bg.get('background_type')}，{clean_plan}")
    assets = Queue()

    def bind():
        try:
            return bind_and_save(run, analysis, catalog, assets.put)
        finally:
            assets.put(None)

    with ThreadPoolExecutor(max_workers=4) as workers:
        clean = None
        if bg.get("background_type") == "reference_clean":
            clean = workers.submit(measured, "reference_clean", background.clean, run, bg, analysis["reference_size"])
        masks = workers.submit(measured, "cutout_masks", batch_masks, run, iter(assets.get, None), originals)
        binding = workers.submit(measured, "binding", bind)
        layers = workers.submit(measured, "reveal", extract_and_save, run, analysis, clean)
        bindings = binding.result()
        materials = catalog.result() if isinstance(catalog, Future) else catalog
        measured("delivery", deliver, run, analysis, materials, bindings, layers.result(), outputs, masks.result())


def finish(run):
    try:
        assemble(run, run, read(run/"analysis.json"), read(run/"catalog.json"))
        save(run/"status.json", {"status": "ready"})
    except Exception as exc:
        save(run/"status.json", {"status": "failed", "error": str(exc)[:1200]})
