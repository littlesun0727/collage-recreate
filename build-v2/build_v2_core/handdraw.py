"""Deterministic hand-drawn strokes: geometry + bounded, smooth variation.

Wobble is measured in stroke widths, not whole-canvas units. The shape and
anchor positions stay under program control. No key color, erosion or matting.
"""
import hashlib

import numpy as np
from PIL import Image, ImageColor, ImageDraw

from .common import BuildError, fields, number
from .primitives import shape_path

BRUSH_VERSION = 'smooth-handdraw-1'
DEFAULT_BRUSH = {'color': '#244A96', 'stroke_width': .012, 'wobble': .4,
                 'width_variation': .15, 'seed': 0}


def validate_brush(value):
    fields(value, {'seed'}, set(DEFAULT_BRUSH)-{'seed'}, 'brush')
    if type(value['seed']) is not int or not 0 <= value['seed'] <= 2**32-1:
        raise BuildError('invalid_parameter', 'brush.seed must be an integer in 0..4294967295')
    for name, low, high in (('stroke_width', .0001, .1), ('wobble', 0, 1.5), ('width_variation', 0, .45)):
        if name in value:
            number(value[name], 'brush.'+name, low, high)
    if 'color' in value:
        import re
        if not isinstance(value['color'], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', value['color']):
            raise BuildError('invalid_parameter', 'brush.color requires #RRGGBB or #RRGGBBAA')


def resolve_brush(value):
    validate_brush(value)
    return {**DEFAULT_BRUSH, **value}


def inherit_stroke(spec, brush):
    """No implicit border around explicit fill-only shapes."""
    operation = dict(spec)
    if 'fill' not in operation and 'stroke' not in operation:
        operation['stroke'] = brush['color']
    if 'stroke' in operation:
        operation.setdefault('stroke_width', brush['stroke_width'])
    return operation


def operation_seed(seed, index):
    return int.from_bytes(hashlib.sha256(f'{seed}:{index}'.encode('ascii')).digest()[:8], 'big')


def wobbly_path(points, width, seed, wobble=.4, width_variation=.15, closed=False, pin_vertices=True):
    """Resample by arc length; perturb normals smoothly; pin open ends/corners."""
    points = np.asarray(points, dtype=float)
    keep = np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-9]
    points = points[keep]
    if closed and len(points) > 1 and np.linalg.norm(points[0]-points[-1]) > 1e-9:
        points = np.vstack((points, points[0]))
    if len(points) < 2:
        return points, np.full(len(points), width)
    segments = np.diff(points, axis=0)
    lengths = np.linalg.norm(segments, axis=1)
    distance = np.r_[0., np.cumsum(lengths)]
    total = distance[-1]
    # Bounded work even for high-resolution canvases and long paths.
    count = min(4096, max(8, int(total/max(1., width*.45))+1))
    positions = np.linspace(0, total, count)
    if pin_vertices:
        positions = np.unique(np.r_[positions, distance])
    indices = np.minimum(np.searchsorted(distance, positions, side='right')-1, len(lengths)-1)
    fraction = (positions-distance[indices])/lengths[indices]
    base = points[indices] + segments[indices]*fraction[:,None]
    normals = np.column_stack((-segments[indices,1], segments[indices,0]))/lengths[indices,None]
    rng = np.random.default_rng(seed)
    knots = min(512, max(4, int(total/max(8., width*7))+1))

    def smooth_noise():
        values = rng.uniform(-1., 1., knots)
        # Circular values join smoothly for closed curves; no global RNG used.
        if closed:
            values[-1] = values[0]
        u = np.clip((positions/total)*(knots-1), 0, knots-1)
        left = np.minimum(u.astype(int), knots-2)
        blend = u-left
        blend = blend*blend*(3-2*blend)
        return values[left]*(1-blend) + values[left+1]*blend

    fade = np.ones(len(positions))
    if pin_vertices:
        # Nearest anchor distance without allocating a large point-pair matrix.
        right = np.minimum(np.searchsorted(distance, positions), len(distance)-1)
        left = np.maximum(right-1, 0)
        nearest = np.minimum(np.abs(positions-distance[left]), np.abs(positions-distance[right]))
        fade = np.minimum(1., nearest/max(width*2, 1.))
    elif not closed:
        fade = np.minimum(1., np.minimum(positions,total-positions)/max(width*2,1.))
    displacement = smooth_noise()*width*wobble*fade
    widths = width*(1+smooth_noise()*width_variation)
    result = base + normals*displacement[:,None]
    if closed:
        result[-1], widths[-1] = result[0], widths[0]
    return result, widths


def paint_handdraw(canvas, spec, brush, index=0, geometry_size=None):
    geometry_size = geometry_size or canvas.size
    points, closed, pin_vertices = shape_path(spec, geometry_size)
    width = max(.5, spec.get('stroke_width', brush['stroke_width'])*min(geometry_size))
    path, widths = wobbly_path(points, width, operation_seed(brush['seed'], index),
                              brush['wobble'], brush['width_variation'], closed, pin_vertices)
    if not len(path):
        return
    tuples = [tuple(point) for point in path]
    opacity = spec.get('opacity', 1)

    def composite(mask, color):
        # Antialias alpha first, then apply the exact ink RGB. Resampling a
        # colored RGBA layer can introduce RGB ringing near transparent edges.
        if mask.size != canvas.size:
            mask = mask.resize(canvas.size, Image.Resampling.LANCZOS)
        r,g,b,a = ImageColor.getcolor(color, 'RGBA')
        layer = Image.new('RGBA', canvas.size, (r,g,b,0))
        layer.putalpha(mask.point(lambda value: round(value*a/255*opacity)))
        canvas.alpha_composite(layer)

    if closed and 'fill' in spec:
        mask = Image.new('L', geometry_size)
        ImageDraw.Draw(mask).polygon(tuples, fill=255)
        composite(mask, spec['fill'])
    if 'stroke' in spec:
        mask = Image.new('L', geometry_size)
        draw = ImageDraw.Draw(mask)
        # One alpha mask avoids dark overlaps at every sampled joint.
        for i in range(len(path)-1):
            draw.line([tuples[i], tuples[i+1]], fill=255, width=max(1, round((widths[i]+widths[i+1])/2)))
        for (x,y), local_width in zip(path, widths):
            radius = local_width/2
            draw.ellipse((x-radius,y-radius,x+radius,y+radius), fill=255)
        composite(mask, spec['stroke'])
