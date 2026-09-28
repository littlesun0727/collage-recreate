"""The single customer-cutout entry: local BiRefNet ONNX, reusable alpha and outline."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageColor, ImageOps
from compositor import digest, pin, read_json, write_json

DEFAULT_OUTLINE = '2%'
DEFAULT_COLOR = '#FFFFFF'
ALGORITHM = 'birefnet-native-alpha-v1'


def outline_pixels(value, subject_size):
    """A number/px is in native output pixels; % uses the visible subject's short side."""
    if isinstance(value, bool):
        raise ValueError('outline-width must be nonnegative pixels or a percentage')
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)(px|%)?\s*', str(value))
    if not match:
        raise ValueError('outline-width must be nonnegative pixels (12 or 12px) or a percentage (2%)')
    width = float(match[1])
    if match[2] == '%':
        width *= min(subject_size) / 100
    if not math.isfinite(width) or width > 4096:
        raise ValueError('Resolved outline width must be finite and at most 4096 pixels')
    return width


def outline_rgba(foreground, width_px, color=DEFAULT_COLOR):
    """Pure per-frame operation; same canvas and subject geometry, no segmentation.

    Callers may vary width_px/color for live outlines. Reserve canvas padding
    before calling if a subject reaches the image edge.
    """
    import numpy as np
    import cv2
    width = outline_pixels(width_px, foreground.size)
    rgba = foreground.convert('RGBA')
    ink = ImageColor.getcolor(color, 'RGBA')
    if width == 0:
        return rgba.copy()
    alpha = np.asarray(rgba.getchannel('A'))
    foreground_mask = alpha > 8
    if not foreground_mask.any():
        raise ValueError('Cannot outline an empty foreground')
    outside = (~foreground_mask).astype(np.uint8)
    distance = cv2.distanceTransform(outside, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    border_alpha = np.clip(width + .5 - distance, 0, 1) * ink[3]
    border = Image.new('RGBA', rgba.size, ink)
    border.putalpha(Image.fromarray(np.rint(border_alpha).astype(np.uint8)))
    return Image.alpha_composite(border, rgba)


def make_asset(foreground, width=DEFAULT_OUTLINE, color=DEFAULT_COLOR):
    alpha = foreground.getchannel('A')
    bounds = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
    if not bounds:
        raise ValueError('BiRefNet returned an empty foreground')
    left, top, right, bottom = bounds
    resolved = outline_pixels(width, (right-left, bottom-top))
    padding = math.ceil(resolved) + 2
    size = (right-left+2*padding, bottom-top+2*padding)
    if size[0] * size[1] > 40_000_000:
        raise ValueError('Outlined cutout exceeds 40 million pixels')
    tile = Image.new('RGBA', size)
    tile.alpha_composite(foreground.crop(bounds), (padding, padding))
    return outline_rgba(tile, resolved, color), {
        'visible_bbox': list(bounds), 'outline_width': str(width),
        'outline_width_px': resolved, 'outline_color': color,
        'source_offset': [left-padding, top-padding],
        'mapping': 'source_xy = asset_xy + source_offset; no resize',
    }


def infer_alpha(source, model):
    """Only the model tensor is resized; alpha is restored to oriented source size."""
    import numpy as np
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
    inputs = session.get_inputs()
    if len(inputs) != 1 or inputs[0].type != 'tensor(float)' or len(inputs[0].shape) != 4:
        raise ValueError('Expected BiRefNet FP32 ONNX with one NCHW image input')
    shape = inputs[0].shape
    if isinstance(shape[1], int) and shape[1] != 3:
        raise ValueError('BiRefNet input must have 3 RGB channels')
    height, width = [n if isinstance(n, int) and n > 0 else 1024 for n in shape[2:]]
    rgb = source.convert('RGB').resize((width, height), Image.Resampling.BILINEAR)
    data = np.asarray(rgb, dtype=np.float32) / 255
    data = (data - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
    tensor = np.ascontiguousarray(data.transpose(2, 0, 1)[None])
    output = np.asarray(session.run(None, {inputs[0].name: tensor})[-1]).squeeze()
    if output.ndim != 2 or not np.isfinite(output).all():
        raise ValueError('BiRefNet returned an invalid alpha tensor')
    # BiRefNet exports exist with logits or a final sigmoid. Do not sigmoid twice.
    logits = float(output.min()) < 0 or float(output.max()) > 1
    if logits:
        output = 1 / (1 + np.exp(-np.clip(output, -50, 50)))
    probability = Image.fromarray(np.clip(output, 0, 1).astype(np.float32))
    probability = probability.resize(source.size, Image.Resampling.BILINEAR)
    alpha = np.rint(np.clip(np.asarray(probability), 0, 1) * 255).astype(np.uint8)
    if source.mode == 'RGBA':
        alpha = np.rint(alpha.astype(np.float32) * np.asarray(source.getchannel('A')) / 255).astype(np.uint8)
    return Image.fromarray(alpha), {'backend': 'birefnet-onnx', 'input_size': [width, height],
                                  'output_activation': 'logits' if logits else 'probabilities'}


def extract(source_path, model_path, output, outline_width=DEFAULT_OUTLINE,
            outline_color=DEFAULT_COLOR, cache=None, foreground_path=None):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Cutout output must be a new directory')
    ImageColor.getcolor(outline_color, 'RGBA')
    outline_pixels(outline_width, (100, 100))  # reject malformed parameters before inference
    reused = False
    if foreground_path:
        origin = pin(Path(foreground_path).resolve())
        with Image.open(origin['file']) as im:
            if im.mode != 'RGBA':
                raise ValueError('--foreground must be an RGBA image with the original alpha')
            foreground = ImageOps.exif_transpose(im).copy()
        metadata = {'backend': 'existing-foreground', 'foreground_source': origin}
        reused = True
    else:
        if not source_path or not model_path:
            raise ValueError('BiRefNet cutout requires --source and --model, or --foreground for outline-only changes')
        source_pin, model_pin = pin(Path(source_path).resolve()), pin(Path(model_path).resolve())
        cache_root = Path(cache).resolve() if cache else output.parent / '.cutout-cache'
        key = hashlib.sha256(json.dumps([ALGORITHM, source_pin['sha256'], model_pin['sha256']]).encode()).hexdigest()
        cached = cache_root / key
        manifest = cached / 'segmentation.json'
        if manifest.is_file():
            metadata = read_json(manifest)
            saved = metadata['foreground']
            pin(cached / saved['file'], saved['sha256'])
            with Image.open(cached / saved['file']) as im:
                foreground = im.convert('RGBA')
            metadata = {**metadata, 'source': source_pin, 'model': model_pin}
            reused = True
        else:
            with Image.open(source_pin['file']) as im:
                foreground = ImageOps.exif_transpose(im).convert('RGBA')
            if foreground.width * foreground.height > 40_000_000:
                raise ValueError('Source exceeds 40 million pixels')
            alpha, model_info = infer_alpha(foreground, model_pin['file'])
            foreground.putalpha(alpha)
            if not alpha.point(lambda v: 255 if v > 8 else 0).getbbox():
                raise ValueError('BiRefNet returned an empty foreground')
            cached.mkdir(parents=True, exist_ok=True)
            foreground.save(cached / 'foreground.png')
            alpha.save(cached / 'alpha.png')
            metadata = {**model_info, 'algorithm': ALGORITHM, 'source': source_pin, 'model': model_pin,
                        'source_size': list(foreground.size), 'rgb_source': 'original-customer-pixels',
                        'foreground': {'file': 'foreground.png', 'sha256': digest(cached / 'foreground.png')}}
            write_json(manifest, metadata)
    asset, geometry = make_asset(foreground, outline_width, outline_color)
    output.mkdir(parents=True)
    foreground.save(output / 'foreground.png')
    foreground.getchannel('A').save(output / 'alpha.png')
    asset.save(output / 'asset.png')
    result = {'schema_version': 'renders-cutout-v1', 'segmentation': metadata,
              'segmentation_reused': reused, 'geometry': geometry,
              'foreground': pin(output / 'foreground.png'), 'alpha': pin(output / 'alpha.png'),
              'asset': pin(output / 'asset.png')}
    write_json(output / 'cutout.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--source', type=Path)
    source.add_argument('--foreground', type=Path, help='Existing unoutlined RGBA; reuse its alpha without inference')
    parser.add_argument('--model', type=Path, help='Explicit local BiRefNet FP32 ONNX weights')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--outline-width', default=DEFAULT_OUTLINE)
    parser.add_argument('--outline-color', default=DEFAULT_COLOR)
    args = parser.parse_args()
    try:
        result = extract(args.source, args.model, args.output, args.outline_width,
                         args.outline_color, args.cache, args.foreground)
        print(json.dumps(result, ensure_ascii=True, indent=2))
    except (ValueError, OSError, ImportError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
