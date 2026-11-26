"""Local visual diagnostics for directory-based cases."""
import io
import json
from zipfile import ZipFile

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps

from .layout.window_contract import spec_from_photo
from .layout.window_drawing import apply, sampling_margin
from .layout.window_shapes import window_outline
from .timings import log


def analysis_bbox(run, analysis):
    with Image.open(run/"prepared/reference.png") as image:
        canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    labels = []
    for obj in analysis["objects"]:
        box = obj["bbox"]
        draw.rectangle(box, outline="#ffad29", width=2)
        if obj.get("mode") == "window":
            points = list(map(tuple, window_outline(obj).tolist()))
            draw.line(points + points[:1], fill="#00ffe4", width=4)
        labels.append((box, obj["id"]))
    for box, name in labels:
        x, y = max(0, box[0]), max(0, box[1])
        rect = draw.textbbox((x, y), name)
        draw.rectangle(rect, fill="#18202b")
        draw.text((x, y), name, fill="white")
    canvas.save(run/"analysis-bbox.png")
    log("已写入 artifacts/analysis-bbox.png")


def subject(im):
    # Preserve supplied transparency. For opaque photos estimate a local preview mask.
    if im.getchannel("A").getextrema()[0] < 255:
        return im
    small = im.convert("RGB")
    small.thumbnail((600, 600))
    w, h = small.size
    if min(w, h) < 4:
        raise ValueError("Photo too small for cutout preview")
    mask = np.zeros((h, w), np.uint8)
    cv2.grabCut(cv2.cvtColor(np.asarray(small), cv2.COLOR_RGB2BGR), mask,
                (1, 1, w-2, h-2), None, None, 4, cv2.GC_INIT_WITH_RECT)
    alpha = Image.fromarray(((mask % 2)*255).astype("uint8")).resize(im.size, Image.Resampling.BILINEAR)
    if not alpha.getbbox():
        raise ValueError("No foreground found for cutout preview")
    im.putalpha(alpha)
    return im


def photo(run, obj, layers, originals=None):
    src = obj["source"]
    if originals is not None:
        from .pipeline import oriented_photo
        im = oriented_photo(originals[src["asset_id"]])
    else:
        with Image.open(run/"prepared"/(src["asset_id"]+".png")) as source:
            im = source.convert("RGBA")
    cutout = obj.get("mode") == "cutout"
    base = layers[obj["linked_to"]] if obj.get("linked_to") else obj
    if cutout:
        im = subject(im)
        if obj is base:
            im = im.crop(im.getchannel("A").getbbox())
    crop = tuple(n*(im.width if i%2 == 0 else im.height) for i,n in enumerate(src["source_crop"]))
    im = im.crop(crop)
    if src["mirror_x"]:
        im = ImageOps.mirror(im)
    left, top, right, bottom = base["bbox"]
    size = (right-left, bottom-top)
    spec = spec_from_photo({**obj, "mode": obj.get("mode", "bare")})
    margin = sampling_margin(size, spec)
    ratio = (min if cutout and base is obj else max)(size[0]/im.width, size[1]/im.height)
    center = src["crop_center"]
    x = (im.width-size[0]/ratio)*center[0]-margin/ratio
    y = (im.height-size[1]/ratio)*center[1]-margin/ratio
    tile = im.transform((size[0]+2*margin, size[1]+2*margin), Image.Transform.AFFINE,
                        (1/ratio, 0, x, 0, 1/ratio, y), Image.Resampling.BICUBIC)
    mask = None
    if spec["window_enabled"] and obj.get("shape", "rectangle") != "rectangle":
        points = window_outline({**obj, "bbox": [0, 0, *size], "rotation": 0})
        pixels = np.zeros(size[::-1], np.uint8)
        cv2.fillPoly(pixels, [np.rint(points*16).astype(np.int32)], 255, cv2.LINE_AA, shift=4)
        mask = Image.fromarray(pixels)
    tile, _ = apply(tile, spec, source_margin=margin, shape_override=mask)
    tile = tile.rotate(-base.get("rotation", 0), Image.Resampling.BICUBIC, expand=True)
    return tile, (round((left+right-tile.width)/2), round((top+bottom-tile.height)/2))


def final(run, outputs, originals=None):
    with ZipFile(outputs/"delivery.zip") as archive:
        scene = json.loads(archive.read("scene.json"))
        canvas = Image.new("RGBA", tuple(scene["canvas_size"]))
        layers = {obj["id"]: obj for obj in scene["layers"]}
        for obj in scene["layers"]:
            src = obj["source"]
            log(f"回贴 {obj['id']}，来源 {src.get('asset_id') or src.get('file')}")
            if src["type"] == "png":
                with Image.open(io.BytesIO(archive.read(src["file"]))) as tile:
                    canvas.alpha_composite(tile.convert("RGBA"), tuple(src["offset"]))
            else:
                tile, offset = photo(run, obj, layers, originals)
                canvas.alpha_composite(tile, offset)
    canvas.save(run/"final.png")
    log("已写入 artifacts/final.png")
