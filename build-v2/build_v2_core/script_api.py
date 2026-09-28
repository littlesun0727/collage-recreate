"""Small helpers for task code, not a geometry language or agent."""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

from .common import BuildError, number, sha
from .fonts import missing_characters


def pixel(value):
    """Nearest integer, halves away from zero; never truncates raster sizes."""
    return int(math.copysign(math.floor(abs(value) + .5), value))


def reference_to_layout(point, reference_size, canvas_size):
    return [v * c / r for v, r, c in zip(point, reference_size, canvas_size)]


def layout_to_local(point, target_box, raster_size):
    return [(v - origin) * size / span for v, origin, size, span in
            zip(point, target_box[:2], raster_size, (target_box[2]-target_box[0], target_box[3]-target_box[1]))]


def local_to_layout(point, target_box, raster_size):
    return [v * span / size + origin for v, origin, size, span in
            zip(point, target_box[:2], raster_size, (target_box[2]-target_box[0], target_box[3]-target_box[1]))]


def stroke_pixels(layout_width, target_box, raster_size):
    sx = raster_size[0] / (target_box[2]-target_box[0]); sy = raster_size[1] / (target_box[3]-target_box[1])
    if abs(sx-sy) > 1e-6:
        raise BuildError('nonuniform_mapping', 'Stroke requires uniform raster scaling')
    return layout_width * sx


def drawing_canvas(target_box, content_box=None, *, padding=4, scale=1):
    """Allocate before painting; content_box includes stroke/curve/wobble extents.

    Boxes and padding use layout pixels. Return RGBA canvas and delivery mapping.
    Map drawing coordinates with mapping['paint_box'], never the original box.
    """
    for box in (target_box, content_box if content_box is not None else target_box):
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or box[2] <= box[0] or box[3] <= box[1]):
            raise BuildError('invalid_box', 'Expected finite positive layout bounds')
    if type(padding) not in (int, float) or not math.isfinite(padding) or padding < 0:
        raise BuildError('invalid_padding', 'Padding must be finite and nonnegative')
    if type(scale) is not int or scale < 1:
        raise BuildError('invalid_scale', 'Use a positive integer raster scale')
    content = content_box if content_box is not None else target_box
    box = [math.floor(min(target_box[0], content[0])-padding),
           math.floor(min(target_box[1], content[1])-padding),
           math.ceil(max(target_box[2], content[2])+padding),
           math.ceil(max(target_box[3], content[3])+padding)]
    size = [(box[2]-box[0])*scale, (box[3]-box[1])*scale]
    if max(size) > 16384 or size[0]*size[1] > 40000000:
        raise BuildError('invalid_size', 'Drawing canvas exceeds raster limits')
    return Image.new('RGBA', tuple(size)), {'target_box': list(target_box),
                                           'paint_box': box, 'raster_size': size}


def render_text(size, text, font_info, *, font_size_px, color, align='center',
                line_spacing_px=0, padding_px=2, min_font_size_px=1):
    """Measure real glyphs, retain canvas/margins, report baseline and final size."""
    if not text or missing_characters(font_info, text):
        raise BuildError('font_coverage', 'Exact text requires a font covering every glyph')
    if sha(font_info['path']) != font_info['sha256']:
        raise BuildError('font_changed', 'Font digest changed')
    if align not in ('left', 'center', 'right'):
        raise BuildError('text_align', 'Unknown alignment')
    for name, value in [('font_size_px', font_size_px), ('min_font_size_px', min_font_size_px)]:
        number(value, name, 1, 8192)
    number(line_spacing_px, 'line_spacing_px', 0, 8192); number(padding_px, 'padding_px', 0, min(size)/2)
    probe = ImageDraw.Draw(Image.new('L', (1, 1)))
    requested = pixel(font_size_px)
    for actual in range(requested, pixel(min_font_size_px)-1, -1):
        font = ImageFont.truetype(font_info['path'], actual, index=font_info['index'])
        bounds = probe.multiline_textbbox((0, 0), text, font=font, spacing=line_spacing_px, align=align)
        width, height = bounds[2]-bounds[0], bounds[3]-bounds[1]
        if width + 2*padding_px <= size[0] and height + 2*padding_px <= size[1]: break
    else:
        raise BuildError('text_overflow', 'Measured glyphs cannot fit without violating minimum size')
    left = padding_px if align == 'left' else size[0]-padding_px-width if align == 'right' else (size[0]-width)/2
    top = (size[1]-height)/2
    origin = [left-bounds[0], top-bounds[1]]
    image = Image.new('RGBA', tuple(size))
    # JSON serializes RGB(A) tuples to arrays. Preserve JSON-stable metadata
    # while passing Pillow the tuple it requires.
    if isinstance(color, (list, tuple)):
        color = list(color)
    fill = tuple(color) if isinstance(color, list) else color
    ImageDraw.Draw(image).multiline_text(origin, text, font=font, fill=fill, spacing=line_spacing_px, align=align)
    ascent, descent = font.getmetrics()
    return image, {'text': text, 'font': font_info, 'requested_font_size_px': requested, 'actual_font_size_px': actual,
                   'shrunk': actual < requested, 'color': color, 'align': align, 'line_spacing_px': line_spacing_px,
                   'padding_px': padding_px, 'min_font_size_px': min_font_size_px, 'origin_px': origin,
                   'glyph_bbox_px': [left, top, left+width, top+height], 'baseline_px': origin[1]+ascent,
                   'ascent_px': ascent, 'descent_px': descent, 'representation': 'text-parameters-and-preview',
                   'native_editable_layer_delivered': False}


def reproduce_text(size, record):
    return render_text(size, record['text'], record['font'], font_size_px=record['requested_font_size_px'],
                       color=record['color'], align=record['align'], line_spacing_px=record['line_spacing_px'],
                       padding_px=record['padding_px'], min_font_size_px=record['min_font_size_px'])
