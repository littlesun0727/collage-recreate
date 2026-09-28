"""Deterministic local composition; no model calls or asset generation."""
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageOps


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def digest(path):
    with Path(path).open("rb") as stream:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
        return hasher.hexdigest()


def pin(path, expected=None):
    path = Path(path).resolve()
    actual = digest(path)
    if expected and expected != actual:
        raise ValueError(f"Source changed: {path}")
    return {"file": str(path), "sha256": actual}


def numbers(value, count):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError(f"Expected {count} numbers: {value}")
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in value):
        raise ValueError(f"Invalid numbers: {value}")
    return value


def box_size(box):
    x, y, r, b = numbers(box, 4)
    size = (round(r) - round(x), round(b) - round(y))
    if min(size) < 1 or size[0] * size[1] > 40_000_000:
        raise ValueError(f"Invalid or oversized box: {box}")
    return size


def open_pinned(source):
    pin(source["file"], source["sha256"])
    with Image.open(source["file"]) as image:
        return ImageOps.exif_transpose(image).convert("RGBA")


def fitted(image, size, mode, center=(0.5, 0.5)):
    numbers(center, 2)
    if any(n < 0 or n > 1 for n in center):
        raise ValueError("crop_center must be in [0, 1]")
    if mode == "stretch":
        return image.resize(size, Image.Resampling.LANCZOS)
    if mode == "cover":
        return ImageOps.fit(image, size, Image.Resampling.LANCZOS, centering=tuple(center))
    if mode == "contain":
        result = Image.new("RGBA", size)
        scaled = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
        result.alpha_composite(scaled, ((size[0] - scaled.width) // 2, (size[1] - scaled.height) // 2))
        return result
    raise ValueError(f"Unknown fit: {mode}")


def compose(layout):
    width, height = numbers(layout["canvas_size"], 2)
    box_size([0, 0, width, height])
    canvas = Image.new("RGBA", (int(width), int(height)))
    rendered = []
    for layer in sorted(layout["layers"], key=lambda item: (item["layer_index"], item["key"])):
        box = layer["box"]
        if (layer.get("contains_cutout") or layer.get("kind") == "cutout") and layer["fit"] == "stretch":
            raise ValueError("Cutout layers must preserve aspect ratio; use contain or cover")
        source_image = open_pinned(layer["source"])
        if layer.get("source_crop"):
            crop = numbers(layer["source_crop"], 4)
            if any(n < 0 or n > 1 for n in crop) or crop[0] >= crop[2] or crop[1] >= crop[3]:
                raise ValueError("source_crop must be a normalized positive rectangle")
            crop = [round(crop[i] * source_image.size[i % 2]) for i in range(4)]
            box_size(crop)
            source_image = source_image.crop(crop)
        numbers([layer["layer_index"]], 1)
        tile = fitted(source_image, box_size(box), layer["fit"], layer.get("crop_center", [0.5, 0.5]))
        if layer.get("mask"):
            # Explicit mask only: build inspection masks are NOT photo clipping masks.
            mask = layer["mask"]
            mask_image = open_pinned(mask)
            channel = mask.get("channel", "alpha")
            if channel not in ("alpha", "luminance"):
                raise ValueError("mask.channel must be alpha or luminance")
            alpha = mask_image.getchannel("A") if channel == "alpha" else mask_image.convert("L")
            alpha = alpha.resize(tile.size, Image.Resampling.LANCZOS)
            tile.putalpha(ImageChops.multiply(tile.getchannel("A"), alpha))
        mirror_x = layer.get("mirror_x", False)
        if type(mirror_x) is not bool:
            raise ValueError("mirror_x must be a boolean")
        if mirror_x:
            tile = ImageOps.mirror(tile)
        angle = layer.get("rotation", 0)
        numbers([angle], 1)
        if angle:
            tile = tile.rotate(-angle, Image.Resampling.BICUBIC, expand=True)
        x = round((box[0] + box[2] - tile.width) / 2)
        y = round((box[1] + box[3] - tile.height) / 2)
        canvas.alpha_composite(tile, (x, y))
        rendered.append(layer["key"])
    return canvas, rendered
