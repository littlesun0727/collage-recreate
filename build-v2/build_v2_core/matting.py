"""Local chroma matting: conservative trimap, closed-form alpha, foreground RGB.

For generated artwork on a contrasting saturated solid screen, not semantic
segmentation. Keeps the source canvas; fitting remains a separate operation.
"""
from functools import lru_cache
import importlib
import importlib.metadata
import re
import time

import numpy as np
from PIL import Image

from .common import BuildError

MATTING_VERSION = 'pymatting-chroma-1'
MAX_PIXELS = 2048 * 2048


def parse_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', value):
        raise BuildError('matting_key_invalid', 'Expected #RRGGBB (or opaque #RRGGBBFF) screen color')
    if len(value) == 9 and value[-2:].lower() != 'ff':
        raise BuildError('matting_key_invalid', 'The generation screen must be opaque')
    key = np.array([int(value[i:i+2], 16) for i in (1, 3, 5)], dtype=np.float64)
    if np.ptp(key) < 64:
        raise BuildError('matting_key_unsupported', 'Use a contrasting saturated screen (green, blue, or magenta), not gray/white/black')
    high = key >= (key.min() + np.ptp(key) * .5)
    return key, high


@lru_cache(maxsize=1)
def _backend():
    try:
        pm = importlib.import_module('pymatting')
        nd = importlib.import_module('scipy.ndimage')
        nb = importlib.import_module('numba')
        if not callable(pm.estimate_alpha_cf) or not callable(pm.estimate_foreground_ml):
            raise AttributeError('Missing alpha/foreground estimator')
        return pm, nd, nb
    except (ImportError, OSError, AttributeError, RuntimeError) as exc:
        raise BuildError('matting_dependency_missing', 'Install build/requirements.txt in the active Python environment; PyMatting alpha and foreground estimators are required') from exc


def require_backend(key_color=None):
    """Run before submitting a paid image request; never downloads a model."""
    if key_color is not None:
        parse_key(key_color)
    return _backend()


def make_trimap(rgba, key_color, background_tolerance=8):
    key, high = parse_key(key_color)
    _, nd, _ = require_backend()
    rgb = np.asarray(rgba, dtype=np.float64)[:, :, :3]
    source_alpha = np.asarray(rgba)[:, :, 3]
    h, w = source_alpha.shape
    margin = max(1, min(8, min(h, w) // 8))
    border = np.zeros((h, w), dtype=bool)
    border[:margin] = border[-margin:] = True
    border[:, :margin] = border[:, -margin:] = True
    visible_border = border & (source_alpha >= 16)
    # Channel groups derive from the requested screen, not from a yellow subject.
    screen_excess = np.min(rgb[:, :, high], axis=2) - np.max(rgb[:, :, ~high], axis=2)
    eligible = visible_border & (screen_excess > 32) & (np.max(np.abs(rgb-key), axis=2) <= 96)
    samples = rgb[eligible]
    if len(samples) < 16:
        raise BuildError('matting_background_uncertain', 'No reliable border matching the requested screen color')
    bins, counts = np.unique(np.floor(samples / 8).astype(int), axis=0, return_counts=True)
    mode = (bins[counts.argmax()] + .5) * 8
    cluster = samples[np.max(np.abs(samples-mode), axis=1) < 20]
    support = len(cluster) / max(1, int(visible_border.sum()))
    if support < .5:
        raise BuildError('matting_background_uncertain', 'Screen background is not sufficiently uniform/dominant at the border')
    background = np.median(cluster, axis=0)
    noise = np.quantile(np.max(np.abs(cluster-background), axis=1), [.5, .95, .99])
    if noise[1] > 12:
        raise BuildError('matting_background_uncertain', 'Background varies too much for automatic chroma matting')
    distance = np.max(np.abs(rgb-background), axis=2)
    candidate = (screen_excess <= 8) & (distance > 100) & (source_alpha > 0)
    inset = min(3, max(1, round(min(h, w) / 192)))
    foreground = nd.binary_erosion(candidate, iterations=inset)
    # Do not lose thin lines/small isolated pieces solely because seeds erode.
    labels, count = nd.label(candidate)
    retained = np.zeros(count+1, dtype=bool)
    retained[np.unique(labels[foreground])] = True
    missing = candidate & ~retained[labels]
    foreground |= missing
    if not foreground.any():
        raise BuildError('matting_foreground_uncertain', 'No confident non-screen foreground; change the key color or provide an explicit cutout')
    background_mask = ((distance <= background_tolerance) | (source_alpha == 0)) & ~foreground
    if not background_mask.any():
        raise BuildError('matting_background_uncertain', 'No confident transparent background')
    trimap = np.full((h, w), .5, dtype=np.float64)
    trimap[background_mask] = 0
    trimap[foreground] = 1
    unknown = int(np.count_nonzero(trimap == .5))
    if unknown > min(750000, .75*h*w):
        raise BuildError('matting_trimap_uncertain', 'Too much uncertain area for bounded local matting; inspect the generated image')
    return trimap, {
        'effective_key_rgb': [round(float(v)) for v in background],
        'key_calibrated': bool(np.any(background != key)), 'border_support': support,
        'background_noise_quantiles': noise.tolist(),
        'background_tolerance': background_tolerance,
        'foreground_rule': 'screen-channel excess <= 8; max RGB distance from background > 100',
        'foreground_seed_inset_px': inset, 'thin_foreground_seed_pixels': int(missing.sum()),
        'background_pixels': int(background_mask.sum()), 'foreground_pixels': int(foreground.sum()),
        'unknown_pixels': unknown,
    }


def remove_key(image, key_color, *, background_tolerance=8):
    started = time.perf_counter()
    if type(background_tolerance) not in (int, float) or not np.isfinite(background_tolerance) or not 1 <= background_tolerance <= 16:
        raise BuildError('matting_settings', 'background_tolerance must be 1..16 RGB levels')
    rgba = image.convert('RGBA')
    if min(rgba.size) < 5 or rgba.width * rgba.height > MAX_PIXELS:
        raise BuildError('matting_size_limit', 'Matting requires dimensions >= 5 and at most 2048*2048 pixels; no implicit resizing')
    pm, _, nb = require_backend(key_color)
    trimap, details = make_trimap(rgba, key_color, background_tolerance)
    original = np.array(rgba)
    rgb = np.ascontiguousarray(original[:, :, :3], dtype=np.float64) / 255
    previous_threads = nb.get_num_threads()
    alpha_seconds = foreground_seconds = 0.
    try:
        nb.set_num_threads(min(4, previous_threads))
        if details['unknown_pixels']:
            tick = time.perf_counter()
            alpha = pm.estimate_alpha_cf(rgb, trimap, laplacian_kwargs={'epsilon': 1e-7}, cg_kwargs={'maxiter': 2000, 'rtol': 1e-7})
            alpha_seconds = time.perf_counter()-tick
        else:
            alpha = trimap.copy()
        if not np.isfinite(alpha).all():
            raise ValueError('Non-finite alpha')
        if np.any((alpha > 0) & (alpha < 1)):
            tick = time.perf_counter()
            foreground = pm.estimate_foreground_ml(rgb, alpha)
            foreground_seconds = time.perf_counter()-tick
        else:
            foreground = rgb.copy()
        if not np.isfinite(foreground).all():
            raise ValueError('Non-finite foreground')
    except Exception as exc:
        raise BuildError('matting_processing_failed', 'Local alpha/foreground estimation failed; keep the raw image for offline recovery (no automatic fallback or regeneration)') from exc
    finally:
        nb.set_num_threads(previous_threads)
    foreground[trimap == 1] = rgb[trimap == 1]
    data = np.empty_like(original)
    data[:, :, :3] = np.rint(np.clip(foreground, 0, 1)*255).astype(np.uint8)
    # The solver sees screen-composited RGB; existing source alpha is applied ONCE.
    data[:, :, 3] = np.rint(np.clip(alpha, 0, 1)*original[:, :, 3]).astype(np.uint8)
    data[data[:, :, 3] == 0, :3] = 0
    result = Image.fromarray(data)
    visible = data[:, :, 3] > 8
    transparent = float(np.mean(data[:, :, 3] <= 5))
    if transparent < .01 or not visible.any():
        raise BuildError('key_background_failed', 'Matting did not leave both transparent background and visible foreground')
    details.update({
        'method': MATTING_VERSION, 'alpha_method': 'estimate_alpha_cf', 'foreground_method': 'estimate_foreground_ml',
        'backend': {name: importlib.metadata.version(name) for name in ('pymatting', 'numpy', 'scipy', 'numba')},
        'key_color': key_color, 'settings': {'epsilon': 1e-7, 'cg_maxiter': 2000, 'cg_rtol': 1e-7},
        'size': list(result.size), 'canvas_preserved': True, 'source_offset': [0, 0],
        'coordinate_space': 'input-image-pixels', 'alpha_eroded': False,
        'source_alpha_preserved': bool(np.all(data[:, :, 3] <= original[:, :, 3])),
        'core_rgb_changed_pixels': int(np.count_nonzero((trimap == 1) & np.any(data[:, :, :3] != original[:, :, :3], axis=2))),
        'transparent_fraction': transparent, 'visible_fraction': float(visible.mean()),
        'soft_alpha_pixels': int(np.count_nonzero((data[:, :, 3] > 0) & (data[:, :, 3] < 255))),
        'content_bbox': result.getchannel('A').point(lambda v: 255 if v > 8 else 0).getbbox(),
        'timing': {'alpha_seconds': round(alpha_seconds, 4), 'foreground_seconds': round(foreground_seconds, 4)},
        'elapsed_seconds': round(time.perf_counter()-started, 4),
        'visual_status': 'unreviewed',
        'limitations': 'Automatic chroma trimap, not semantic segmentation. Same-screen-color foreground and fully translucent objects may be ambiguous.',
    })
    return result, details
