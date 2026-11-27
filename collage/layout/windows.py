"""Photo window parameters: what the model may write and how it compiles onto the photo.

The model writes window parameters directly on the photo object (omitted = default). This
module checks the cross-field rules a JSON schema cannot express, turns the points the model
gives into the renderer's unrotated box and rotation, fills defaults, and creates the pop-out
foreground object from a `popout` box. Nothing is mapped back to a second format.
"""
from copy import deepcopy
import math

import cv2
import numpy as np

from collage.layout import window_geometry as geometry
from .window_shapes import rounded_quad_outline, window_outline
from collage.layout.window_contract import INNER_RATIO_DEFAULT, RADIUS_DEFAULT, STAR_POINTS_DEFAULT

WINDOW_ONLY = ('shape', 'radius', 'star_points', 'points', 'fade')
# The model writes `rectangle` (default; a four-cornered window, tilted or not, with `radius` when its
# corners are rounded) and `other`. The engine contract keeps `rectangle`, `rounded_rect` and `polygon`,
# so the names are translated once, when the photo is compiled: rectangle + radius -> rounded_rect,
# other -> polygon. Corners that no rectangle fits are drawn as the four points they are.
WIRE_SHAPES = ('rectangle', 'circle', 'ellipse', 'heart', 'star', 'other')
RECT_SHAPES = ('rectangle',)


def engine_shape(item):
    """The engine's shape name for a model-written photo window."""
    shape = item.get('shape', 'rectangle')
    if shape == 'rectangle':
        return 'rounded_rect' if 'radius' in item else 'rectangle'
    return 'polygon' if shape == 'other' else shape
EDGES = ('top', 'bottom', 'left', 'right')
# A popout must stick out of the visible window by at least this share of the window size.
POPOUT_MIN_EXTENSION = .03
# A fitted window may poke out of the canvas by this share of its short side (photos that touch
# the canvas edge, corner points a little off); it is clamped back. Further out is a wrong point.
CANVAS_TOLERANCE = .05
# A window whose corner sits this close to the canvas border is cut off by the canvas: the model
# can only see the part inside the image, so its corners are a partial outline, not a rectangle.
CLIP_TOLERANCE = .004
# A window hidden behind another photo by more than this share of its own box has edges the model
# could not see either.
COVER_SHARE = .06


class WindowError(ValueError):
    """A window parameter problem; `fields` names what a field patch may change."""
    def __init__(self, message, fields=()):
        super().__init__(message)
        self.fields = list(fields)


SHAPE_FIELDS = ('mode', 'shape', 'radius', 'star_points', 'points', 'corners', 'axis')


def field_issues(item):
    """Cross-field rules for one model-written photo -> [(message, fields)].

    Mode and shape decide which of the other shape fields may exist, so fixing one conflict can
    require dropping a neighbour (a star that also carries `points` becomes a polygon only if its
    `star_points` goes too). Every conflict about those fields therefore lists all of the shape
    fields the photo has, and a field patch may change them together.
    """
    issues = []
    def bad(message, *fields):
        if set(fields) & set(SHAPE_FIELDS):
            fields = (*fields, 'mode', 'shape', *(k for k in SHAPE_FIELDS if k in item))
        issues.append((message, list(dict.fromkeys(fields))))
    mode = item.get('mode', 'window')
    shape = item.get('shape', 'rectangle')
    present = [k for k in ('corners', 'axis', 'points') if k in item]
    if len(present) > 1:
        bad('corners, axis and points are mutually exclusive: ' + ', '.join(present), *present)
    if mode != 'window':
        for key in (*WINDOW_ONLY, 'corners', 'axis'):
            if key in item:
                bad(f'{key} needs a fixed window (mode=window), not mode={mode}', 'mode', key)
    if mode != 'window' and 'popout' in item:
        bad(f'popout belongs to a base photo that keeps its window, not mode={mode}', 'mode', 'popout')
    if mode == 'window':
        if 'radius' in item and shape != 'rectangle':
            bad('radius only applies to a rectangle (rounded corners)', 'shape', 'radius')
        if 'star_points' in item and shape != 'star':
            bad('star_points only applies to shape=star', 'shape', 'star_points')
        if shape == 'other' and 'points' not in item:
            bad('shape=other requires points', 'points')
        if 'points' in item and shape != 'other':
            bad('points only applies to shape=other', 'shape', 'points')
        if 'corners' in item and shape not in RECT_SHAPES:
            bad('corners only applies to a rectangle', 'shape', 'corners')
        if 'axis' in item and shape not in geometry.AXIS_SHAPES:
            bad('axis only applies to heart, star or ellipse', 'shape', 'axis')
    stroke = item.get('stroke')
    if stroke:
        if mode == 'bare':
            bad('mode=bare has no outline to stroke; use window or cutout', 'mode', 'stroke')
        if mode == 'cutout' and 'placement' in stroke:
            bad('a cutout stroke is always outside; omit stroke.placement', 'stroke')
        if stroke.get('placement') == 'center' and stroke.get('offset'):
            bad('a centered stroke needs offset 0', 'stroke')
    fade = item.get('fade')
    if fade is not None:
        edges = {e: fade.get(e, 0) for e in EDGES}
        if not any(edges.values()):
            bad('fade needs at least one edge above 0', 'fade')
        elif fade.get('direction', 'inside') == 'inside' and (
                edges['left'] + edges['right'] > .9 or edges['top'] + edges['bottom'] > .9):
            bad('opposite inside fades leave no opaque centre; keep each pair under 0.9 in total', 'fade')
    return issues


def _scale(size, coordinate_mode):
    return (size[0]/1000, size[1]/1000) if coordinate_mode == 'normalized_1000' else (1., 1.)


def to_pixels(box, size, coordinate_mode):
    sx, sy = _scale(size, coordinate_mode)
    return [round(v * (sx if i % 2 == 0 else sy)) for i, v in enumerate(box)]


def _point(p, size, coordinate_mode):
    sx, sy = _scale(size, coordinate_mode)
    return [p[0]*sx, p[1]*sy]


def _clamp_to_canvas(box, size, fields):
    w, h = size
    clamped = [max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])]
    allowed = max(CANVAS_TOLERANCE * min(box[2] - box[0], box[3] - box[1]), 3)
    if max(abs(a-b) for a, b in zip(box, clamped)) > allowed or clamped[0] >= clamped[2] or clamped[1] >= clamped[3]:
        raise WindowError('the unrotated window box falls outside the canvas; check the tilt points', fields)
    return clamped


def resolve(item, size, coordinate_mode, above=()):
    """Compile one model-written photo's geometry and window fields (all in pixels).

    Returns (fields, popout, review): `fields` replace the photo's bbox/rotation and add the
    window parameters with defaults filled; `popout` is (box, visible_bbox) or None.
    `above` lists the visible outlines (pixels) of the photos stacked over this one: a window the canvas or
    another photo cuts off has corners the model could not see, so they are not straightened.
    """
    mode = item.get('mode', 'window')
    shape = engine_shape(item) if mode == 'window' else None
    visible = to_pixels(item['bbox'], size, coordinate_mode)
    placement, review = {'bbox': visible, 'rotation': 0}, None
    local_points = None
    corner_radii = None
    fields = ['bbox']
    try:
        if 'corners' in item:
            corners = {k: _point(p, size, coordinate_mode) for k, p in item['corners'].items()}
            fields = ['corners']
            points, _ = geometry.check_quadrilateral(corners)
            pts = np.asarray(points, dtype=np.float32)
            cut = _clipped_corners(pts, size)
            box = [*pts.min(axis=0), *pts.max(axis=0)]
            touches = _touches_canvas(box, size)
            radius = item.get('radius', 0)
            radii = [0 if clipped else radius for clipped in cut]
            outline = rounded_quad_outline(points, radii) if radius else points
            covering = occluding_outlines(outline, above)
            hidden = ('the window is cut off by the canvas edge' if touches else
                      'another photo covers part of the window' if covering else None)
            # Keep explicit corners. Only use the rotated-rectangle representation
            # when the four points already form one (up to floating-point noise).
            fitted, review = (None, None) if hidden else geometry.fit_rectangle(corners, limit=1e-9)
            if fitted:
                placement = fitted
            else:
                box, local_points = geometry.polygon_local(points)
                placement, shape = {'bbox': box, 'rotation': 0}, 'polygon'
                if radius:
                    # Preserve rounded ends on the visible trapezoid. Intersections with
                    # the canvas or a known covering window are straight clipping edges.
                    for i, point in enumerate(points):
                        if any(cv2.pointPolygonTest(c, tuple(point), False) >= 0 for c in covering):
                            radii[i] = 0
                    corner_radii = radii
                if hidden:
                    review = {'type': 'visible_outline', 'input_corners': corners,
                              'warnings': [f'{hidden.capitalize()}; the visible outline is drawn as given'],
                              'hidden_edges': hidden}
        elif shape == 'polygon':
            fields = ['points']
            absolute = [_point(p, size, coordinate_mode) for p in item['points']]
            box, local_points = geometry.polygon_local(absolute)
            placement = {'bbox': box, 'rotation': 0}
        elif shape in geometry.AXIS_SHAPES and ('axis' in item or shape == 'star'):
            # A star's outline does not fill its unit box, so even an upright star needs its box
            # solved from the visible bounding box.
            fields = ['axis']
            axis = [_point(p, size, coordinate_mode) for p in item['axis']] if 'axis' in item else None
            placement, review = geometry.fit_shape(
                shape, visible, axis, star_points=item.get('star_points', STAR_POINTS_DEFAULT),
                inner_ratio=INNER_RATIO_DEFAULT)
        else:
            fields = ['bbox']
        box = _clamp_to_canvas(placement['bbox'], size, fields)
    except WindowError:
        raise
    except ValueError as error:
        raise WindowError(str(error), fields) from None
    if placement is not None and (min(visible[2], box[2]) <= max(visible[0], box[0])
                                  or min(visible[3], box[3]) <= max(visible[1], box[1])):
        raise WindowError('the fitted window does not overlap the bbox; the tilt points do not match this photo',
                          fields + ['bbox'])
    result = {'bbox': [round(v) for v in box], 'rotation': placement['rotation'], 'mode': mode}
    if mode == 'window':
        result['shape'] = shape
        if shape == 'rounded_rect':
            result['radius'] = item.get('radius', RADIUS_DEFAULT)
        elif shape == 'star':
            result.update(star_points=item.get('star_points', STAR_POINTS_DEFAULT), inner_ratio=INNER_RATIO_DEFAULT)
        elif shape == 'polygon':
            result['points'] = local_points
            if corner_radii is not None:
                result['corner_radii'] = corner_radii
    stroke = item.get('stroke')
    if stroke:
        result['stroke'] = {'width': stroke['width'], 'color': stroke['color'],
                            'pattern': stroke.get('pattern', 'solid'),
                            'placement': 'outside' if mode == 'cutout' else stroke.get('placement', 'outside'),
                            'offset': stroke.get('offset', 0)}
    fade = item.get('fade')
    if fade:
        result['fade'] = {'direction': fade.get('direction', 'inside'), **{e: fade.get(e, 0) for e in EDGES}}
    popout = None
    if 'popout' in item:
        popout = (to_pixels(item['popout'], size, coordinate_mode), visible)
    return result, popout, review


def _overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _touches_canvas(box, size):
    """A box with a side (near) on the image border: the window continues past the canvas."""
    w, h = size
    slack = max(CLIP_TOLERANCE * min(w, h), 2)
    l, t, r, b = box
    return l <= slack or t <= slack or r >= w - slack or b >= h - slack


def _clipped_corners(points, size):
    """A segment lying along the canvas boundary is a cut. Its endpoints stay sharp."""
    pts = np.asarray(points)
    tol = max(CLIP_TOLERANCE * min(size), 2)
    edges = np.zeros(4, dtype=bool)
    for axis, extent in enumerate(size):
        for edge in (0, extent):
            meets = np.abs(pts[:, axis] - edge) <= tol
            edges |= meets & np.roll(meets, -1)
    return edges | np.roll(edges, 1)


def occluding_outlines(outline, above, min_share=COVER_SHARE):
    """Use filled contours for overlap. Bounding boxes only skip disjoint pairs."""
    a = np.asarray(outline, dtype=np.float32)
    lower, upper = a.min(axis=0), a.max(axis=0)
    area = abs(cv2.contourArea(a))
    result = []
    for outline_b in above:
        b = np.asarray(outline_b, dtype=np.float32)
        if b.ndim == 1:  # A legacy caller supplied the extent of an axis-aligned rectangular window.
            l,t,r,bottom = b
            b = np.asarray([(l,t),(r,t),(r,bottom),(l,bottom)], dtype=np.float32)
        if np.any(np.minimum(upper, b.max(axis=0)) <= np.maximum(lower, b.min(axis=0))):
            continue
        if cv2.isContourConvex(a) and cv2.isContourConvex(b):
            intersection, _ = cv2.intersectConvexConvex(a, b)
            share = intersection / max(area, 1.)
        else:
            # Concave stars, hearts and free polygons need their filled silhouette.
            # Work in the lower window's local box, keeping small windows at native resolution.
            scale = min(1., 512 / max(1., max(upper - lower)))
            width, height = np.ceil((upper - lower) * scale).astype(int) + 1
            masks = []
            for contour in (a, b):
                mask = np.zeros((height, width), np.uint8)
                cv2.fillPoly(mask, [np.rint((contour - lower) * scale * 16).astype(np.int32)], 1, shift=4)
                masks.append(mask)
            share = np.count_nonzero(masks[0] & masks[1]) / max(np.count_nonzero(masks[0]), 1)
        if share > min_share:
            result.append(b)
    return result


def popout_box(base, popout, visible):
    """Keep the subject's bounds when it crosses a window edge."""
    l, t, r, b = visible
    w, h = r-l, b-t
    pl, pt, pr, pb = popout
    if min(r, pr) <= max(l, pl) or min(b, pb) <= max(t, pt):
        return None, f'{base}: popout does not overlap the photo window'
    sticks = max((l-pl)/w, (t-pt)/h, (pr-r)/w, (pb-b)/h)
    if sticks < POPOUT_MIN_EXTENSION:
        return None, f'{base}: popout sticks out of the window by less than {POPOUT_MIN_EXTENSION:.0%}'
    return list(popout), None


# Photos laid out together share one tilt. A group is at least this many tilted rectangles whose
# angles sit within GROUP_ANGLE of their median; their angles are then set to that median.
GROUP_MIN = 4
GROUP_ANGLE = 4.0
GROUP_MIN_TILT = 3.0


def unify_group_tilt(objects, notes):
    """Give photos that were laid out as one tilted group the same angle (the median of the group).

    Each photo's angle comes from four hand-placed corners and carries a few degrees of noise; a grid
    of photos turned together should not wobble. Only plain tilted rectangle windows take part, and a
    photo whose angle is far from the median is left alone: it is a different object, not noise.
    The window box keeps its centre and its horizontal envelope, so every photo still touches its
    own bbox with all four corners.
    """
    members = [o for o in objects if o.get('kind') == 'photo' and o.get('mode') == 'window'
               and o.get('shape') == 'rectangle' and abs(o.get('rotation') or 0) >= GROUP_MIN_TILT]
    if len(members) < GROUP_MIN:
        return
    angles = sorted(o['rotation'] for o in members)
    median = angles[len(angles) // 2] if len(angles) % 2 else (angles[len(angles) // 2 - 1] + angles[len(angles) // 2]) / 2
    group = [o for o in members if abs(o['rotation'] - median) <= GROUP_ANGLE]
    if len(group) < GROUP_MIN:
        return
    changed = 0
    for o in group:
        if abs(o['rotation'] - median) < .05:
            continue
        envelope = tilted_envelope(o['bbox'], o['rotation'])
        solved = geometry.inscribe_rectangle(envelope, median)
        if not solved:
            continue
        cx, cy, w, h = solved
        o['bbox'] = [round(cx - w/2), round(cy - h/2), round(cx + w/2), round(cy + h/2)]
        o['rotation'] = round(median, 4)
        changed += 1
    if changed:
        notes.append({'code': 'tilt_unified',
                      'message': f'{changed} of {len(group)} tilted photos set to the group angle {median:.1f} degrees'})


def tilted_envelope(box, rotation):
    """Horizontal envelope of an unrotated box turned by `rotation` degrees about its centre."""
    import math
    l, t, r, b = box
    c, s = abs(math.cos(math.radians(rotation))), abs(math.sin(math.radians(rotation)))
    w, h = (r - l) * c + (b - t) * s, (r - l) * s + (b - t) * c
    cx, cy = (l + r) / 2, (t + b) / 2
    return [cx - w/2, cy - h/2, cx + w/2, cy + h/2]


def add_popouts(objects, order, popouts, notes):
    """Store the subject extent and foreground paint order on the window itself."""
    by_id = {o['id']: o for o in objects}
    order = list(order)
    for base_id in [oid for oid in order if oid in popouts]:
        base = by_id[base_id]
        box, problem = (None, base_id + ': popout needs mode=window') if base.get('mode') != 'window' \
            else popout_box(base_id, *popouts[base_id])
        if problem:
            notes.append({'code': 'popout_ignored', 'message': problem})
            continue
        base['mode'] = 'popout'
        base['popout_bbox'] = [round(v) for v in box]
        top = max((i for i, oid in enumerate(order)
                   if by_id[oid]['kind'] in {'photo', 'frame'} and _overlap(by_id[oid]['bbox'], box)),
                  default=order.index(base_id))
        if top > order.index(base_id):
            base['popout_above'] = order[top]
    return objects, order
