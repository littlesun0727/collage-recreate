"""Deterministic local resource drawing. All coordinates are asset-local."""
import math

import numpy as np
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont

from .common import BuildError
from .primitives import EXTRA_SHAPES, shape_path
from .handdraw import BRUSH_VERSION, inherit_stroke, paint_handdraw, resolve_brush

ENGINE_VERSION = 'pillow-recipes-1'


def rgba(value, opacity=1):
    r, g, b, a = ImageColor.getcolor(value, 'RGBA')
    return r, g, b, round(a * opacity)


def draw_shape(canvas, spec, mask=False):
    w, h = canvas.size
    draw = ImageDraw.Draw(canvas)
    fill = 255 if mask else rgba(spec['fill'], spec.get('opacity', 1)) if 'fill' in spec else None
    stroke = None if mask else rgba(spec['stroke'], spec.get('opacity', 1)) if 'stroke' in spec else None
    width = max(1, round(spec.get('stroke_width', .01) * min(w, h)))
    kind = spec['kind']
    if kind in EXTRA_SHAPES:
        points, _, _ = shape_path(spec, canvas.size)
        points = [tuple(point) for point in points]
        if fill is not None:
            draw.polygon(points, fill=fill)
        if stroke is not None:
            draw.line([*points, points[0]], fill=stroke, width=width, joint='curve')
        return
    if 'box' in spec:
        x1, y1, x2, y2 = [v * d for v, d in zip(spec['box'], (w, h, w, h))]
        bounds = (round(x1), round(y1), max(round(x1), round(x2) - 1), max(round(y1), round(y2) - 1))
        if kind == 'rectangle':
            draw.rectangle(bounds, fill=fill, outline=stroke, width=width)
        elif kind == 'rounded_rectangle':
            draw.rounded_rectangle(bounds, radius=spec.get('radius', .05) * min(w, h), fill=fill, outline=stroke, width=width)
        elif kind == 'ellipse':
            draw.ellipse(bounds, fill=fill, outline=stroke, width=width)
    else:
        points = [(x * w, y * h) for x, y in spec['points']]
        if kind == 'polygon':
            draw.polygon(points, fill=fill)
            if stroke:
                draw.line([*points, points[0]], fill=stroke, width=width, joint='curve')
        elif 'dash' in spec:
            on, off = [v * min(w, h) for v in spec['dash']]
            travelled = 0.
            for a, b in zip(points, points[1:]):
                length = math.dist(a, b)
                position = 0.
                while position < length:
                    phase = (travelled + position) % (on + off)
                    step = min((on if phase < on else on + off) - phase, length - position)
                    if phase < on and length:
                        start = tuple(a[i] + (b[i] - a[i]) * position / length for i in range(2))
                        end = tuple(a[i] + (b[i] - a[i]) * (position + step) / length for i in range(2))
                        draw.line([start, end], fill=stroke, width=width)
                    position += max(step, .001)
                travelled += length
        else:
            draw.line(points, fill=stroke, width=width, joint='curve')


def opening_mask(size, opening, supersample=3):
    high = Image.new('L', (size[0] * supersample, size[1] * supersample))
    draw_shape(high, opening['shape'], mask=True)
    return high.resize(size, Image.Resampling.LANCZOS)


def apply_openings(image, openings):
    masks = []
    for opening in openings:
        mask = opening_mask(image.size, opening)
        if mask.getbbox() is None:
            raise BuildError('empty_opening', 'Opening has no pixels')
        image.putalpha(ImageChops.multiply(image.getchannel('A'), ImageChops.invert(mask)))
        masks.append((opening, mask))
    return image, masks


def draw_resource(size, recipe):
    raster_scale = recipe.get('raster_scale', 1)
    if type(raster_scale) is not int or not 1 <= raster_scale <= 4:
        raise BuildError('invalid_parameter', 'raster_scale must be an integer in 1..4')
    native_size = tuple(n*raster_scale for n in size)
    # Legacy build inputs allow up to 20 million pixels. Only the new opt-in
    # brush/high-density path introduces the smaller allocation budget.
    pixel_limit = 16000000 if 'brush' in recipe or 'raster_scale' in recipe else 20000000
    if any(type(n) is not int or n < 1 or n > 16384 for n in native_size) or native_size[0]*native_size[1] > pixel_limit:
        raise BuildError('invalid_size', f'Drawing raster exceeds the {pixel_limit}-pixel limit')
    sample = 3 if native_size[0] * native_size[1] <= 4000000 else 1
    high_size = tuple(n * sample for n in native_size)
    brush = resolve_brush(recipe['brush']) if 'brush' in recipe else None
    image = Image.new('RGBA', native_size if brush else high_size)
    for index, operation in enumerate(recipe['ops']):
        layer = Image.new('RGBA', image.size)
        if brush:
            if 'dash' in operation:
                raise BuildError('unsupported_operation', 'Hand-drawn dash is not supported; use a separate clean draw recipe')
            paint_handdraw(layer, inherit_stroke(operation, brush), brush, index, high_size)
        else:
            draw_shape(layer, operation)
        image = Image.alpha_composite(image, layer)
    if not brush:
        image = image.resize(native_size, Image.Resampling.LANCZOS)
    image, masks = apply_openings(image, recipe['openings'])
    meta = {'engine': ENGINE_VERSION, 'coordinate_space': 'asset-local-normalized',
            'target_size': list(size), 'asset_size': list(native_size), 'raster_scale': raster_scale}
    if brush:
        meta.update(brush_engine=BRUSH_VERSION, brush=brush, key_color_used=False)
    return image, masks, meta


def text_resource(size, recipe, font_info):
    scale = 3
    w, h = [v * scale for v in size]
    desired = max(1, round(recipe['font_size'] * h))
    spacing = round(recipe['line_spacing'] * h)
    stroke = round(recipe.get('stroke_width', 0) * min(w, h))
    color = rgba(recipe['color'])
    for font_size in range(desired, 0, -1):
        font = ImageFont.truetype(font_info['path'], font_size, index=font_info['index'])
        probe = ImageDraw.Draw(Image.new('L', (1, 1)))
        bounds = probe.multiline_textbbox((0, 0), recipe['text'], font=font, spacing=spacing, align=recipe['align'], stroke_width=stroke)
        tw, th = bounds[2] - bounds[0], bounds[3] - bounds[1]
        tile = Image.new('RGBA', (max(1, math.ceil(tw + 4 * scale)), max(1, math.ceil(th + 4 * scale))))
        ImageDraw.Draw(tile).multiline_text((2 * scale - bounds[0], 2 * scale - bounds[1]), recipe['text'], font=font, fill=color,
                                         spacing=spacing, align=recipe['align'], stroke_width=stroke, stroke_fill=rgba(recipe.get('stroke_color', recipe['color'])))
        if recipe['angle']:
            tile = tile.rotate(recipe['angle'], Image.Resampling.BICUBIC, expand=True)
        if tile.width <= w and tile.height <= h:
            break
    else:
        raise BuildError('text_overflow', 'Text cannot fit at any supported size')
    image = Image.new('RGBA', (w, h))
    x = 0 if recipe['align'] == 'left' else w - tile.width if recipe['align'] == 'right' else (w - tile.width) // 2
    image.alpha_composite(tile, (x, (h - tile.height) // 2))
    image = image.resize(tuple(size), Image.Resampling.LANCZOS)
    return image, [], {'engine': ENGINE_VERSION, 'font': font_info, 'actual_font_size_px': font_size / scale,
                       'requested_font_size_px': desired / scale, 'text': recipe['text'], 'fit': 'shrink-font-no-stretch',
                       'text_content_checked': True, 'font_appearance_status': 'unreviewed'}


def feather_resource(size, recipe):
    w, h = size
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    if recipe['shape'] == 'rectangle':
        distance = np.minimum.reduce([x + .5, w - x - .5, y + .5, h - y - .5])
        opacity = np.clip(distance / (recipe['edge_width'] * min(w, h)), 0, 1)
    else:
        radius = np.sqrt(((x + .5 - w / 2) / (w / 2)) ** 2 + ((y + .5 - h / 2) / (h / 2)) ** 2)
        opacity = np.clip((1 - radius) / (recipe['edge_width'] * 2), 0, 1)
    opacity = opacity * opacity * (3 - 2 * opacity)
    mask = Image.fromarray(np.rint(opacity * 255).astype('uint8'))
    preview = Image.new('RGBA', tuple(size), 'white'); preview.putalpha(mask)
    return preview, mask, {'engine': ENGINE_VERSION, 'coordinate_space': 'display-window-local', 'mask_kind': 'feather'}


def remove_key(image, key_color):
    data = np.array(image.convert('RGBA')).astype(np.float32)
    key = np.array(ImageColor.getrgb(key_color[:7]), dtype=np.float32)
    requested_key = key.copy()
    # Generated solid backdrops can drift from the requested RGB value. Calibrate
    # only when most border pixels agree and remain close to the explicit key.
    border = np.concatenate((data[0, :, :3], data[-1, :, :3], data[:, 0, :3], data[:, -1, :3]))
    candidate = np.median(border, axis=0)
    support = float(np.mean(np.max(np.abs(border - candidate), axis=1) <= 12))
    calibrated = support >= .6 and float(np.max(np.abs(candidate - key))) <= 96
    if calibrated:
        key = candidate
    distance = np.max(np.abs(data[:, :, :3] - key), axis=2)
    # Keep non-key colors opaque; smooth only the narrow compression fringe.
    alpha = np.clip((distance - 24) / 64, 0, 1)
    old = data[:, :, 3] / 255
    alpha *= old
    soft = (alpha > 0) & (alpha < 1)
    denom = np.maximum(alpha[:, :, None], .03)
    restored = (data[:, :, :3] - (1 - alpha[:, :, None]) * key) / denom
    data[:, :, :3] = np.where(soft[:, :, None], np.clip(restored, 0, 255), data[:, :, :3])
    data[:, :, 3] = alpha * 255
    output = Image.fromarray(np.rint(data).astype('uint8'))
    transparent = float(np.mean(alpha < .02))
    if transparent < .02 or transparent > .995:
        raise BuildError('key_background_failed', f'Generated key extraction failed: transparent fraction={transparent:.3f}')
    return output, {'method': 'explicit-chroma-key', 'key_color': key_color,
                    'effective_key_rgb': [round(float(v)) for v in key], 'border_support': support,
                    'key_calibrated': bool(calibrated and np.any(key != requested_key)), 'transparent_fraction': transparent}


def fit_generated(image, size, opaque=False):
    source = image.convert('RGBA')
    bounds = (0, 0, source.width, source.height) if opaque else source.getchannel('A').point(lambda v: 255 if v > 8 else 0).getbbox()
    if bounds is None:
        raise BuildError('empty_asset', 'Generated image contains no visible object')
    cropped = source.crop(bounds)
    factor = max(size[0] / cropped.width, size[1] / cropped.height) if opaque else min(size[0] / cropped.width, size[1] / cropped.height)
    scaled = cropped.resize((max(1, round(cropped.width * factor)), max(1, round(cropped.height * factor))), Image.Resampling.LANCZOS)
    x, y = (size[0] - scaled.width) // 2, (size[1] - scaled.height) // 2
    canvas = Image.new('RGBA', tuple(size)); canvas.alpha_composite(scaled, (x, y))
    return canvas, {'source_size': list(source.size), 'source_content_bbox': list(bounds), 'scale': factor, 'offset': [x, y],
                    'fit': 'cover-uniform' if opaque else 'contain-uniform', 'coordinate_space': 'asset-local'}


def inspect_resource(image, openings=()):
    alpha = image.convert('RGBA').getchannel('A')
    visible = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
    if visible is None:
        raise BuildError('empty_asset', 'Resource has no visible content')
    opening_checks = []
    data = np.array(alpha)
    for opening, mask in openings:
        core = np.array(mask) >= 250
        if not np.any(core):
            raise BuildError('empty_opening', 'Opening has no opaque mask core')
        maximum = int(data[core].max())
        if maximum > 8:
            raise BuildError('opaque_opening', 'Photo opening is not transparent')
        opening_checks.append({'slot_id': opening['slot_id'], 'max_alpha_in_core': maximum, 'passed': True})
    return {'size': list(image.size), 'content_bbox': list(visible), 'alpha_extrema': list(alpha.getextrema()),
            'openings': opening_checks, 'machine_status': 'passed', 'visual_status': 'unreviewed'}
