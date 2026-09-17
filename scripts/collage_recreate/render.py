"""Pillow-only static compositing with explicit text and geometry checks."""
import math
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw, ImageFont

from .core import ToolError, file_hash, load_image, png_bytes, require, relative_path, write_bytes
from .models import Picture


def fit_image(image, size, fit="cover", focus=(0.5, 0.5), crop=None):
    image = image.convert("RGBa")  # Pre-multiply hidden RGB before all resampling.
    if crop:
        box = (round(crop[0] * image.width), round(crop[1] * image.height),
               round(crop[2] * image.width), round(crop[3] * image.height))
        require(box[2] > box[0] and box[3] > box[1], "CROP_EMPTY", "Crop has no source pixels")
        image = image.crop(box)
    width, height = size
    scale = (max if fit == "cover" else min)(width / image.width, height / image.height)
    resized = image.resize(
        (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    if fit == "cover":
        left = round((resized.width - width) * focus[0])
        top = round((resized.height - height) * focus[1])
        return resized.crop((left, top, left + width, top + height)).convert("RGBA")
    tile = Image.new("RGBA", size)
    tile.alpha_composite(resized.convert("RGBA"), ((width - resized.width) // 2, (height - resized.height) // 2))
    return tile


def picture_tile(task, project, element):
    image = load_image(task.asset_path(project.assets[element.asset]))
    tile = fit_image(image, (element.width, element.height), element.fit, element.focus, element.crop)
    mask = None
    if element.mask_asset:
        source = load_image(task.asset_path(project.assets[element.mask_asset]))
        require(source.getchannel("A").getextrema()[0] < 255, "ALPHA_REQUIRED", "An alpha mask needs real transparency")
        mask = source.getchannel("A").resize(tile.size, Image.Resampling.LANCZOS)
    elif element.mask_shape:
        # Supersample deterministic geometric masks; these do not stand in for styled artwork.
        scale = 4
        mask = Image.new("L", (tile.width * scale, tile.height * scale), 0)
        draw = ImageDraw.Draw(mask)
        box = (0, 0, mask.width - 1, mask.height - 1)
        if element.mask_shape == "ellipse":
            draw.ellipse(box, fill=255)
        else:
            draw.rounded_rectangle(box, radius=element.radius * scale, fill=255)
        mask = mask.resize(tile.size, Image.Resampling.LANCZOS)
    if mask is not None:
        tile.putalpha(ImageChops.multiply(tile.getchannel("A"), mask))
    return tile


def load_font(task, asset, size, override=None):
    path = Path(override).resolve() if override else task.asset_path(asset)
    require(path.is_file(), "FONT_MISSING", "Provide the required font using --font ID=PATH")
    try:
        font = ImageFont.truetype(str(path), size, index=asset.font_index)
        with TTFont(str(path), fontNumber=asset.font_index, lazy=True) as collection:
            chars = set((collection.getBestCmap() or {}).keys())
    except (OSError, ValueError, KeyError):
        raise ToolError("FONT_INVALID", "Cannot load the requested font face") from None
    return font, chars


def text_tile(task, project, element, font_overrides):
    font, chars = load_font(
        task, project.assets[element.font], element.font_size, font_overrides.get(element.font)
    )
    value = project.texts[element.text]
    missing = sorted({ord(c) for c in value if not c.isspace() and ord(c) not in chars})
    require(not missing, "GLYPH_MISSING", f"{element.id}: missing glyphs " + ", ".join(f"U+{c:04X}" for c in missing[:12]))
    require("\t" not in value and "\r" not in value, "TEXT_CONTROL", "Use spaces and LF line breaks in text")
    lines = []
    for paragraph in value.split("\n"):
        current = ""
        for char in paragraph:
            if element.wrap and current and font.getlength(current + char) > element.width:
                lines.append(current)
                current = ""
            current += char
        lines.append(current)
    ascent, descent = font.getmetrics()
    step = math.ceil((ascent + descent) * element.line_spacing)
    boxes = [font.getbbox(line, anchor="ls") if line else (0, -ascent, 0, descent) for line in lines]
    widths = [max(font.getlength(line), box[2] - min(0, box[0])) for line, box in zip(lines, boxes)]
    top = min(i * step + box[1] for i, box in enumerate(boxes))
    bottom = max(i * step + box[3] for i, box in enumerate(boxes))
    if not value:
        top = bottom = 0
    require(max(widths, default=0) <= element.width + 0.01 and bottom - top <= element.height,
            "TEXT_OVERFLOW", f"{element.id}: text does not fit its box; edit text, size or box")
    tile = Image.new("RGBA", (element.width, element.height))
    draw = ImageDraw.Draw(tile)
    for i, (line, box, width) in enumerate(zip(lines, boxes, widths)):
        x = 0 if element.align == "left" else (element.width - width) / (2 if element.align == "center" else 1)
        draw.text((x - min(0, box[0]), i * step - top), line, font=font, fill=element.color, anchor="ls")
    return tile


def placement(element, groups, size):
    group = groups.get(element.group)
    x = element.x + (group.dx if group else 0)
    y = element.y + (group.dy if group else 0)
    return round(x + element.width / 2 - size[0] / 2), round(y + element.height / 2 - size[1] / 2)


def compose(task, project=None, *, font_overrides=None):
    project = project or task.load()
    require(bool(project.elements), "EMPTY_LAYOUT", "Add image/text elements with apply before rendering")
    font_overrides = font_overrides or {}
    require(set(font_overrides) <= {k for k, a in project.assets.items() if a.kind == "font"},
            "FONT_UNKNOWN", "A font override names an unknown font ID")
    canvas = Image.new("RGBA", (project.canvas.width, project.canvas.height), project.canvas.background)
    groups = {group.id: group for group in project.groups}
    bounds = {}
    for element in project.elements:
        require(element.width * element.height <= 40_000_000, "IMAGE_LIMIT", "Layer exceeds 40 million pixels")
        tile = picture_tile(task, project, element) if isinstance(element, Picture) else text_tile(task, project, element, font_overrides)
        if element.opacity != 1:
            tile.putalpha(tile.getchannel("A").point(lambda a: round(a * element.opacity)))
        if element.rotation:
            tile = tile.convert("RGBa").rotate(
                -element.rotation, resample=Image.Resampling.BICUBIC, expand=True
            ).convert("RGBA")
        left, top = placement(element, groups, tile.size)
        bounds[element.id] = [left, top, left + tile.width, top + tile.height]
        require(element.allow_clip or (
            left >= 0 and top >= 0 and left + tile.width <= canvas.width and top + tile.height <= canvas.height
        ), "LAYER_OUTSIDE", f"{element.id}: transformed layer exceeds the canvas; use allow_clip for intentional clipping")
        canvas.alpha_composite(tile, (left, top))
    return canvas, bounds


def render(task, *, font_overrides=None, check_only=False):
    import hashlib
    with task.lock():
        project = task.load()
        canvas, bounds = compose(task, project, font_overrides=font_overrides)
        result = {"revision": project.revision, "width": canvas.width, "height": canvas.height,
                  "bounds": bounds, "visual_status": "unreviewed"}
        if not check_only:
            content = png_bytes(canvas)
            sha = hashlib.sha256(content).hexdigest()
            path = relative_path(task.root, f"outputs/{sha[:24]}.png", exists=False)
            cached = path.is_file() and file_hash(path) == sha
            if not cached:
                write_bytes(path, content)
            result.update(output=str(path), sha256=sha, cache_hit=cached)
        return result
