"""Local visual diagnostics for directory-based cases."""
import io
import json
import math
from zipfile import ZipFile

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps

from .layout.window_contract import spec_from_photo
from .layout.window_drawing import apply, sampling_margin
from .layout.window_shapes import window_outline
from .timings import log
from .cutout import subject


def analysis_bbox(run, analysis):
    with Image.open(run/"prepared/reference.png") as image:
        canvas = image.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    labels = []
    for obj in analysis["objects"]:
        box = obj["bbox"]
        draw.rectangle(box, outline="#ffad29", width=2)
        if obj.get("mode") in ("window", "popout"):
            points = list(map(tuple, window_outline(obj).tolist()))
            draw.line(points + points[:1], fill="#00ffe4", width=4)
        if obj.get("popout_bbox"):
            draw.rectangle(obj["popout_bbox"], outline="#ff58aa", width=3)
        labels.append((box, obj["id"]))
    for box, name in labels:
        x, y = max(0, box[0]), max(0, box[1])
        rect = draw.textbbox((x, y), name)
        draw.rectangle(rect, fill="#18202b")
        draw.text((x, y), name, fill="white")
    canvas.save(run/"analysis-bbox.png")
    log("已写入 artifacts/analysis-bbox.png")


def photo(run, obj, layers, originals=None, archive=None, *, foreground=False, masks=None):
    src = obj["source"]
    if originals is not None:
        from .pipeline import oriented_photo
        im = oriented_photo(originals[src["asset_id"]])
    else:
        with Image.open(run/"prepared"/(src["asset_id"]+".png")) as source:
            im = source.convert("RGBA")
    cutout = obj.get("mode") == "cutout"
    base = layers[obj["linked_to"]] if obj.get("linked_to") else obj
    if cutout or foreground:
        if src.get("mask_file"):
            stream = (masks[src["asset_id"]].open("rb") if masks is not None else archive.open(src["mask_file"]))
            with stream, Image.open(stream) as mask:
                if mask.size != im.size:
                    raise ValueError(f"Mask dimensions differ from source for {obj['id']}")
                im.putalpha(mask.convert("L"))
        else:
            im = subject(im, run/"cutouts", src["asset_id"])
        if cutout and obj is base:
            im = im.crop(im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox())
    crop = tuple(n*(im.width if i%2 == 0 else im.height) for i,n in enumerate(src["source_crop"]))
    # Keep the source outside the cover crop for the subject pass.
    original_size = im.size
    crop = tuple(round(n) for n in crop)
    if not foreground:
        im = im.crop(crop)
    if src["mirror_x"]:
        im = ImageOps.mirror(im)
    left, top, right, bottom = base["bbox"]
    size = (right-left, bottom-top)
    spec = spec_from_photo({**obj, "mode": obj.get("mode", "bare")})
    margin = sampling_margin(size, spec)
    sw, sh = crop[2]-crop[0], crop[3]-crop[1]
    ratio = (min if cutout and base is obj else max)(size[0]/sw, size[1]/sh)
    center = src["crop_center"]
    x = (sw-size[0]/ratio)*center[0]-margin/ratio
    y = (sh-size[1]/ratio)*center[1]-margin/ratio
    if foreground:
        # Sample the subject in canvas coordinates using the window's cover transform.
        l, t, r, b = obj["popout_bbox"]
        angle = math.radians(base.get("rotation", 0))
        c, s = math.cos(angle)/ratio, math.sin(angle)/ratio
        dx, dy = l-(left+right)/2, t-(top+bottom)/2
        u = (original_size[0]-crop[2] if src["mirror_x"] else crop[0])+x+(size[0]/2+margin)/ratio+c*dx+s*dy
        v = crop[1]+y+(size[1]/2+margin)/ratio-s*dx+c*dy
        return im.transform((r-l,b-t), Image.Transform.AFFINE, (c,s,u,-s,c,v),
                            Image.Resampling.BICUBIC), (l,t)
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


def compose(size, layers, draw):
    canvas = Image.new("RGBA", tuple(size))
    foregrounds = {}
    positions = {oid: pos for pos, oid in enumerate(layers)}
    for oid, obj in layers.items():
        if obj.get("mode") == "popout":
            above = obj.get("popout_above", oid)
            anchor = above if positions.get(above, -1) > positions[oid] else oid
            foregrounds.setdefault(anchor, []).append(obj)
    for oid, obj in layers.items():
        tile, offset = draw(obj)
        canvas.alpha_composite(tile, offset)
        for front in foregrounds.get(oid, []):
            tile, offset = draw(front, foreground=True)
            canvas.alpha_composite(tile, offset)
    return canvas


def final(run, outputs, originals=None):
    with ZipFile(outputs/"delivery.zip") as archive:
        scene = json.loads(archive.read("scene.json"))
        layers = {obj["id"]: obj for obj in scene["layers"]}

        def draw(obj, foreground=False):
            src = obj["source"]
            log(f"回贴 {obj['id']}，来源 {src.get('asset_id') or src.get('file')}")
            if src["type"] == "png":
                with Image.open(io.BytesIO(archive.read(src["file"]))) as tile:
                    return tile.convert("RGBA"), tuple(src["offset"])
            return photo(run, obj, layers, originals, archive, foreground=foreground)

        canvas = compose(scene["canvas_size"], layers, draw)
    canvas.save(run/"final.png")
    log("已写入 artifacts/final.png")
