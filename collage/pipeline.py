"""Return the analysis first, then assemble the scene after parallel provider calls."""
import base64
import io
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile
from PIL import Image, ImageOps

from . import background, reveal, vlm
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


def deliver(run, analysis, catalog, bindings, files, outputs=None):
    if outputs is None:
        outputs = run
    sources = {a["id"]: a["asset_id"] for a in catalog}
    photos = {p["slot_id"]: p for p in bindings}
    objects = {o["id"]: o for o in analysis["objects"]}
    lettering = read(run/"lettering.json") if (run/"lettering.json").is_file() else {}
    layers = []
    with ZipFile(run/"delivery.tmp", "w") as archive:
        for oid in analysis["layer_order"]:
            obj = objects[oid]
            if obj["kind"] == "text" and lettering.get(oid, {}).get("action") == "embedded":
                log(f"{oid} 文字已保留在承载层，跳过独立 PNG")
                continue
            if is_customer_photo(obj):
                binding = photos[oid]
                source = {"type": "customer_asset", "asset_id": sources[binding["asset_id"]],
                          **{k: binding[k] for k in ("source_crop", "crop_center", "mirror_x")}}
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


def bind_and_save(run, analysis, catalog):
    bindings = vlm.bind(run, analysis, catalog)
    save(run/"bindings.json", bindings, indent=2)
    for row in bindings:
        log(f"选片 {row['slot_id']} → {row['asset_id']}，裁切 {row['source_crop']}")
    return bindings


def extract_and_save(run, analysis, clean):
    files = reveal.extract(run, analysis, clean)
    save(run/"fixed-index.json", {oid: path.relative_to(run).as_posix()
                                  for oid, path in files.items()}, indent=2)
    return files


def assemble(run, outputs, analysis, catalog, timings=None):
    def measured(stage, func, *args):
        if timings is None:
            return run_stage(stage, func, *args)
        with timings.stage(stage):
            return run_stage(stage, func, *args)

    bg = next(obj for obj in analysis["objects"] if obj["id"] == analysis["layer_order"][0])
    if timings:
        clean_plan = "并行清底" if bg.get("background_type") == "reference_clean" else "跳过清底"
        timings.emit("assemble", f"并行启动 binding 和 reveal；背景 {bg.get('background_type')}，{clean_plan}")
    with ThreadPoolExecutor(max_workers=3) as workers:
        clean = None
        if bg.get("background_type") == "reference_clean":
            clean = workers.submit(measured, "reference_clean", background.clean, run, bg, analysis["reference_size"])
        binding = workers.submit(measured, "binding", bind_and_save, run, analysis, catalog)
        layers = workers.submit(measured, "reveal", extract_and_save, run, analysis, clean)
        measured("delivery", deliver, run, analysis, catalog, binding.result(), layers.result(), outputs)


def finish(run):
    try:
        assemble(run, run, read(run/"analysis.json"), read(run/"catalog.json"))
        save(run/"status.json", {"status": "ready"})
    except Exception as exc:
        save(run/"status.json", {"status": "failed", "error": str(exc)[:1200]})
