"""Render a deterministic basic_shape overlay to a transparent PNG."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from .analysis import validate_draft
from .core import file_hash, png_bytes, require, write_bytes


def _segments(points, dash, gap):
    """Yield dashed segments while preserving phase across a polyline."""
    if dash is None:
        yield from zip(points, points[1:])
        return
    drawing = True
    remaining = float(dash)
    for start, end in zip(points, points[1:]):
        x1, y1 = start
        x2, y2 = end
        length = math.hypot(x2 - x1, y2 - y1)
        walked = 0.0
        while walked < length:
            step = min(remaining, length - walked)
            if drawing and step:
                a, b = walked / length, (walked + step) / length
                yield ((x1 + (x2-x1)*a, y1 + (y2-y1)*a),
                       (x1 + (x2-x1)*b, y1 + (y2-y1)*b))
            walked += step
            remaining -= step
            if remaining <= 1e-9:
                drawing = not drawing
                remaining = float(dash if drawing else gap)


def render_basic_shape(draft_path, reference, overlay_id, output):
    draft, _ = validate_draft(draft_path, reference)
    matches = [item for item in draft.overlays if item.id == overlay_id]
    require(len(matches) == 1, "OVERLAY_UNKNOWN", "Choose one overlay ID from the checked draft")
    overlay = matches[0]
    require(overlay.action == "basic_shape" and overlay.shape is not None,
            "OVERLAY_NOT_DETERMINISTIC", "Only basic_shape overlays use this deterministic renderer")
    output = Path(output).resolve()
    require(output.suffix.lower() == ".png", "OUTPUT_INVALID", "Output must be a new PNG file")
    require(not output.exists(), "OUTPUT_EXISTS", "Refusing to overwrite an existing output")
    width, height = overlay.source_rect[2:]
    scale = 4
    image = Image.new("RGBA", (width * scale, height * scale))
    draw = ImageDraw.Draw(image)
    shape = overlay.shape
    box = (0, 0, image.width - 1, image.height - 1)
    stroke = shape.width * scale
    if shape.kind == "rectangle":
        draw.rectangle(box, fill=shape.fill, outline=shape.outline, width=stroke)
    elif shape.kind == "rounded_rectangle":
        draw.rounded_rectangle(box, radius=round(shape.radius * scale), fill=shape.fill,
                               outline=shape.outline, width=stroke)
    elif shape.kind == "ellipse":
        draw.ellipse(box, fill=shape.fill, outline=shape.outline, width=stroke)
    elif shape.kind == "dashed_rectangle":
        points = [(0, 0), (box[2], 0), (box[2], box[3]), (0, box[3]), (0, 0)]
        for a, b in _segments(points, shape.dash * scale, shape.gap * scale):
            draw.line((a, b), fill=shape.outline, width=stroke)
    else:
        points = [(round(p[0] * (image.width-1) / 1000), round(p[1] * (image.height-1) / 1000))
                  for p in shape.points]
        dash = shape.dash * scale if shape.dash is not None else None
        gap = shape.gap * scale if shape.gap is not None else None
        for a, b in _segments(points, dash, gap):
            draw.line((a, b), fill=shape.outline, width=stroke)
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    content = png_bytes(image)
    write_bytes(output, content)
    return {"ok": True, "overlay_id": overlay.id, "output": str(output),
            "sha256": file_hash(output), "width": width, "height": height,
            "alpha_range": list(image.getchannel("A").getextrema()),
            "visual_status": "unreviewed"}
