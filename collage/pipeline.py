"""Two deliveries: prepared analysis, then a scene bundle built by two independent workers."""
import base64
import io
from concurrent.futures import ThreadPoolExecutor
from zipfile import ZipFile
from PIL import Image, ImageOps

from . import reveal, vlm
from .object_roles import is_customer_photo
from .storage import read, save, sha


def decode_image(encoded, path):
    raw = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(raw), formats=("PNG", "JPEG", "WEBP")) as source:
        if getattr(source, "n_frames", 1) != 1 or source.width * source.height > 40_000_000:
            raise ValueError("Expected a static image of at most 40 megapixels")
        im = ImageOps.exif_transpose(source).convert("RGBA")
        im.save(path)
        return list(im.size)


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


def deliver(run, analysis, catalog, bindings, files):
    sources = {a["id"]: a["asset_id"] for a in catalog}
    photos = {p["slot_id"]: p for p in bindings}
    objects = {o["id"]: o for o in analysis["objects"]}
    lettering = read(run/"lettering.json") if (run/"lettering.json").is_file() else {}
    layers = []
    with ZipFile(run/"delivery.tmp", "w") as archive:
        for oid in analysis["layer_order"]:
            obj = objects[oid]
            if obj["kind"] == "text" and lettering.get(oid, {}).get("action") == "embedded":
                continue
            if is_customer_photo(obj):
                binding = photos[oid]
                source = {"type": "customer_asset", "asset_id": sources[binding["asset_id"]],
                          **{k: binding[k] for k in ("source_crop", "crop_center", "mirror_x")}}
            else:
                if obj["kind"] in ("overlay", "frame") and oid not in files:
                    continue  # Reveal omitted this decoration from its response.
                source = write_png_tile(archive, files[oid], analysis["reference_size"])
                if source is None:
                    continue
            layer = {**obj, "source": source}
            if oid in lettering:
                layer["lettering"] = lettering[oid]
            layers.append(layer)
        scene = {"schema_version": "harmony-collage-scene-v2", "canvas_size": analysis["reference_size"],
                 "layers": layers}
        save(run/"scene.json", scene)
        archive.write(run/"scene.json", "scene.json")
    (run/"delivery.tmp").replace(run/"delivery.zip")


def finish(run):
    try:
        analysis, catalog = read(run/"analysis.json"), read(run/"catalog.json")
        with ThreadPoolExecutor(max_workers=2) as workers:
            binding = workers.submit(vlm.bind, run, analysis, catalog)
            layers = workers.submit(reveal.extract, run, analysis)
            deliver(run, analysis, catalog, binding.result(), layers.result())
        save(run/"status.json", {"status": "ready"})
    except Exception as exc:
        save(run/"status.json", {"status": "failed", "error": str(exc)[:1200]})
