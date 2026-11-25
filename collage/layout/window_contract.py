"""Photo window parameters live on the analysis photo object; expand them for rendering.

The model-facing fields (mode/shape/stroke/fade ...) are the stored fields. This module
only fills defaults and produces the renderer's internal `photo_window_spec`; there is no
second file and no round trip back to a model format.
"""
from copy import deepcopy

from collage.object_roles import is_photo_background

EDGES = ('top', 'bottom', 'left', 'right')
MODES = ('window', 'cutout', 'bare')
SHAPES = ('rectangle', 'rounded_rect', 'ellipse', 'circle', 'heart', 'star', 'polygon')
# Renderer defaults for what the model never writes (line caps and dash lengths). Dash lengths
# scale with the line width: 114 historical dashed strokes had a median segment of 1.7x and a gap
# of 1.5x the line width. A dot is a round cap with almost no length plus a gap of about 3x width.
DASH_UNITS = {'dash': (1.7, 1.5), 'dot': (.12, 3.)}
RADIUS_DEFAULT = 0.06
STAR_POINTS_DEFAULT = 5
INNER_RATIO_DEFAULT = 0.5


def photo_mode(photo):
    """A customer-photo canvas background has no model-written mode: it is always `bare`.

    A stored mode that says otherwise is a contradiction to report, not something to override.
    """
    if is_photo_background(photo):
        if photo.get('mode', 'bare') != 'bare':
            raise ValueError(photo['id'] + ': photo background must preserve the full canvas without a mask, fade or linked_to')
        return 'bare'
    return photo.get('mode', 'window')


def empty_stroke():
    return dict(enabled=False, width=0, color='#FFFFFF', pattern='solid',
                line_cap='butt', placement='inside', offset=0)


def expand_stroke(stroke, *, subject=False):
    """`{width,color[,pattern,placement,offset]}` -> the renderer's complete stroke."""
    pattern = stroke.get('pattern', 'solid')
    result = dict(enabled=True, width=stroke['width'], color=stroke['color'], pattern=pattern,
                  line_cap='round',  # 80% of 584 historical solid strokes were round; butt renders thinner
                  placement='outside' if subject else stroke.get('placement', 'outside'),
                  offset=stroke.get('offset', 0))
    if pattern != 'solid':
        on, off = DASH_UNITS[pattern]
        result['dash'] = [round(min(.5, max(.001, stroke['width']*on)), 5),
                          round(min(.5, max(.001, stroke['width']*off)), 5)]
    return result


def expand_fade(fade):
    """Only edges that fade are written; the rest are zero."""
    return ({edge: fade.get(edge, 0) for edge in EDGES}, fade.get('direction', 'inside'))


def shape_spec(photo):
    kind = photo.get('shape', 'rectangle')
    shape = {'type': kind}
    if kind == 'rounded_rect':
        shape['corner_radius'] = photo.get('radius', RADIUS_DEFAULT)
    elif kind == 'star':
        shape.update(star_points=photo.get('star_points', STAR_POINTS_DEFAULT),
                     inner_ratio=photo.get('inner_ratio', INNER_RATIO_DEFAULT))
    elif kind == 'polygon':
        shape['points'] = deepcopy(photo['points'])
        if 'corner_radii' in photo:
            if len(shape['points']) != 4 or len(photo['corner_radii']) != 4:
                raise ValueError('Per-corner radii require a four-point window')
            shape['corner_radii'] = list(photo['corner_radii'])
    return shape


def spec_from_photo(photo):
    """Expand one analysis photo (or customer-photo background) into the render spec."""
    mode = photo_mode(photo)
    if mode not in MODES:
        raise ValueError(photo['id'] + ': unknown photo mode ' + str(mode))
    spec = {'photo_id': photo['id'], 'shape': {'type': 'rectangle'}, 'stroke': empty_stroke(),
            'subject': {'mode': 'cutout' if mode == 'cutout' else 'preserve'},
            'subject_stroke': empty_stroke(), 'fade': dict.fromkeys(EDGES, 0),
            'fade_direction': 'inside', 'window_enabled': mode == 'window'}
    if mode == 'window':
        spec['shape'] = shape_spec(photo)
    stroke = photo.get('stroke')
    if stroke:
        if mode == 'window':
            spec['stroke'] = expand_stroke(stroke)
        elif mode == 'cutout':
            spec['subject_stroke'] = expand_stroke(stroke, subject=True)
        else:
            raise ValueError(photo['id'] + ': stroke needs a window outline or a cutout subject')
    if photo.get('fade'):
        spec['fade'], spec['fade_direction'] = expand_fade(photo['fade'])
    if photo.get('linked_to'):
        spec['linked_to'] = photo['linked_to']
    validate_drawing(spec)
    return spec


def validate_drawing(spec):
    """Cross-field rules the renderer genuinely depends on."""
    name = spec['photo_id']
    if any(spec['fade'].values()) and not spec['window_enabled']:
        raise ValueError(name + ': edge fade requires a fixed window')
    for stroke in (spec['stroke'], spec['subject_stroke']):
        if stroke['enabled'] != (stroke['width'] > 0):
            raise ValueError(name + ': stroke width must be greater than zero')
        if stroke['pattern'] in {'dash', 'dot'} and 'dash' not in stroke:
            raise ValueError(name + ': dashed/dotted stroke requires dash lengths')
        if stroke['pattern'] == 'solid' and 'dash' in stroke:
            raise ValueError(name + ': solid stroke must omit dash lengths')
        if stroke['pattern'] == 'dot' and stroke['line_cap'] != 'round':
            raise ValueError(name + ': dotted stroke requires round caps')
        if stroke['placement'] == 'center' and stroke['offset']:
            raise ValueError(name + ': centered stroke requires offset=0')
