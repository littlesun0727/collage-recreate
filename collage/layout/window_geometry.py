"""Turn the points the model gives into the renderer's unrotated box and rotation.

The model never writes an angle or a pre-rotation size. It points at corners (rectangles) or at
two axis points (heart / star / ellipse); the program computes the rest.
"""
import math

import numpy as np

CORNER_NAMES = ('top_left', 'top_right', 'bottom_right', 'bottom_left')
AXIS_SHAPES = ('heart', 'star', 'ellipse')
WARN_RESIDUAL = .05
REJECT_RESIDUAL = .3        # shape_axis (heart / star / ellipse) only
# Corners further than this from a rectangle are drawn as they are instead of being straightened.
# Measured on 135 hand-labelled windows: straightening at .15 median error 2.8% -> 8.6% of the short
# side on the hard layer, because a 15%-off trapezoid is not a rectangle with noisy corners.
FIT_LIMIT = .03


def _finite_point(p):
    return (isinstance(p, (list, tuple)) and len(p) == 2 and all(
        not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in p))


def check_quadrilateral(corners):
    """Corner points -> the four points in order; ValueError if they are not a usable quadrilateral.

    Only this is a hard error. How rectangular the quadrilateral is does not matter here.
    """
    if set(corners) != set(CORNER_NAMES):
        raise ValueError('A quadrilateral needs four named inner corners')
    points = [corners[k] for k in CORNER_NAMES]
    if not all(_finite_point(p) for p in points):
        raise ValueError('Corners must be finite coordinate pairs')
    edges = [(points[(i+1)%4][0]-p[0], points[(i+1)%4][1]-p[1]) for i,p in enumerate(points)]
    if any(math.hypot(*e)<2 for e in edges) or any(
            e[0]*edges[(i+1)%4][1]-e[1]*edges[(i+1)%4][0] <= 0 for i,e in enumerate(edges)):
        raise ValueError('Corners must form a non-degenerate clockwise convex quadrilateral')
    return points, edges


def fit_rectangle(corners, limit=None):
    """Calibrate the corners to the nearest rotated rectangle (right angles); None when they do not fit.

    This only straightens corner estimates that are a little off. A diamond, a strong
    perspective trapezoid or any other shape that no rectangle approximates returns (None, review)
    and the caller draws the four points as they are. `limit` overrides FIT_LIMIT: a window whose
    corners are partly invisible (cut off by the canvas, covered by another photo) is a truncated
    outline, so its residual is large even though the nearest rectangle is right.
    """
    limit = FIT_LIMIT if limit is None else limit
    points, edges = check_quadrilateral(corners)
    hx,hy = ((edges[0][i]-edges[2][i])/2 for i in (0,1))
    vx,vy = ((edges[1][i]-edges[3][i])/2 for i in (0,1))
    angle = math.atan2(hy-vx, hx+vy)
    c,s = math.cos(angle),math.sin(angle)
    width,height = hx*c+hy*s,-vx*s+vy*c
    if min(width,height)<2:
        return None, {'type':'rectangle_corners','input_corners':corners,'relative_corner_error':None,
                      'warnings':['Corners do not fit a rectangle; drawn as a four-point polygon']}
    cx,cy = (sum(p[i] for p in points)/4 for i in (0,1))
    fitted = [[cx+c*x-s*y,cy+s*x+c*y] for x,y in
              [(-width/2,-height/2),(width/2,-height/2),(width/2,height/2),(-width/2,height/2)]]
    residual = max(math.dist(a,b) for a,b in zip(points,fitted))/min(width,height)
    if residual>limit:
        return None, {'type':'rectangle_corners','input_corners':corners,'fitted_corners':fitted,
                      'relative_corner_error':round(residual,6),
                      'warnings':[f'Corners are {residual:.0%} of the short side away from a rectangle; '
                                  'drawn as a four-point polygon']}
    placement = {'bbox':[round(cx-width/2),round(cy-height/2),round(cx+width/2),round(cy+height/2)],
                 'rotation':round(math.degrees(angle),4)}
    review = {'type':'rectangle_corners','input_corners':corners,'fitted_corners':fitted,'placement':placement,
              'relative_corner_error':round(residual,6),'warnings':[]}
    if residual>WARN_RESIDUAL:
        review['warnings'].append('Corner fit differs by more than 5% of the short side; inspect perspective or corner estimates')
    return placement,review


def inscribe_rectangle(box, angle):
    """The rotated rectangle whose four corners lie on the four sides of the horizontal box.

    A rectangle w x h turned by `angle` has the horizontal envelope
    (w|cos| + h|sin|) x (w|sin| + h|cos|). Given that envelope and the angle, w and h follow
    (the 2x2 system is singular only at 45 degrees). Returns (cx, cy, w, h), or None when the box
    cannot hold such a rectangle (for example an envelope that is nearly square at a steep angle).
    """
    left, top, right, bottom = box
    ex, ey = right - left, bottom - top
    c, s = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
    if abs(c*c - s*s) < 1e-3:
        return None
    w = (ex*c - ey*s) / (c*c - s*s)
    h = (ey*c - ex*s) / (c*c - s*s)
    if w < 2 or h < 2:
        return None
    return (left + right) / 2, (top + bottom) / 2, w, h


def snap_to_box(placement, box):
    """Make a fitted rotated rectangle touch its box with all four corners, keeping its angle.

    `placement` is what fit_rectangle returned (unrotated box + rotation). Falls back to the
    placement unchanged when the box and the angle do not describe a rectangle, or when the result
    would be much larger or smaller than the fit, which means the box does not belong to it.
    """
    angle = placement['rotation']
    if abs(angle) < .5:
        return placement
    solved = inscribe_rectangle(box, angle)
    if not solved:
        return placement
    cx, cy, w, h = solved
    l, t, r, b = placement['bbox']
    if not (.8 <= w / max(r - l, 1) <= 1.25 and .8 <= h / max(b - t, 1) <= 1.25):
        return placement
    return {'bbox': [round(cx - w/2), round(cy - h/2), round(cx + w/2), round(cy + h/2)], 'rotation': angle}


def _wrap(angle):
    """Degrees into [-180, 180)."""
    return (angle + 180) % 360 - 180


def axis_angle(shape, axis):
    """Clockwise rotation (degrees, y down) implied by the two axis points; 0 = unrotated.

    heart: notch -> tip points down when unrotated. star: center -> top tip points up.
    ellipse: only the long-axis direction matters, folded into [-45, 45).
    """
    if len(axis) != 2 or not all(_finite_point(p) for p in axis):
        raise ValueError('axis needs two finite coordinate pairs')
    (x0, y0), (x1, y1) = axis
    dx, dy = x1 - x0, y1 - y0
    if math.hypot(dx, dy) < 2:
        raise ValueError('axis points are too close to define a direction')
    angle = math.degrees(math.atan2(dy, dx))
    if shape == 'heart':
        return _wrap(angle - 90)
    if shape == 'star':
        return _wrap(angle + 90)
    if shape == 'ellipse':
        return (angle + 45) % 90 - 45
    raise ValueError('axis does not apply to shape ' + str(shape))


def _solve_box(outline, angle, box, fixed=None):
    """Find (cx, cy, w, h) whose rotated outline has the given horizontal bounding box.

    Each side of the bounding box is set by one outline vertex, so four linear equations in
    (cx, cy, w, h) hold once the extreme vertices are known. Those vertices depend only weakly on
    (w, h); iterate until they stop changing. `fixed` pins one dimension (ellipse long axis)
    where the box alone cannot separate w from h.
    """
    pts = np.asarray(outline, float) - .5
    u, v = pts[:, 0], pts[:, 1]
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    left, top, right, bottom = box
    w, h = max(right-left, 2.), max(bottom-top, 2.)
    last = None
    for _ in range(60):
        x = c*w*u - s*h*v
        y = s*w*u + c*h*v
        ia, ib, ic, idx = int(x.argmin()), int(x.argmax()), int(y.argmin()), int(y.argmax())
        if (ia, ib, ic, idx) == last:
            break
        last = (ia, ib, ic, idx)
        rows = [[c*(u[ib]-u[ia]), -s*(v[ib]-v[ia])], [s*(u[idx]-u[ic]), c*(v[idx]-v[ic])]]
        rhs = [right-left, bottom-top]
        if fixed:
            rows.append([1., 0.] if fixed[0] == 'w' else [0., 1.])
            rhs.append(fixed[1])
        solution = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
        w, h = float(solution[0]), float(solution[1])
        if w < 2 or h < 2:
            raise ValueError('Shape axis and bounding box do not describe a usable shape')
    x = c*w*u - s*h*v
    y = s*w*u + c*h*v
    cx = (left - x.min() + right - x.max())/2
    cy = (top - y.min() + bottom - y.max())/2
    fitted = [cx + x.min(), cy + y.min(), cx + x.max(), cy + y.max()]
    short = max(min(right-left, bottom-top), 1.)
    residual = max(abs(a-b) for a, b in zip(fitted, box))/short
    return (cx, cy, w, h), residual


def fit_shape(shape, bbox, axis=None, *, star_points=5, inner_ratio=.5):
    """Pre-rotation box and rotation for a heart / star / ellipse, from its visible horizontal bbox.

    Without an axis the shape is not tilted, but heart and star outlines still do not fill their
    unit box, so the box is solved with angle 0 as well.
    """
    from .window_shapes import unit_outline
    angle = axis_angle(shape, axis) if axis else 0.
    if abs(angle) < .5:
        angle = 0.
    outline = unit_outline(shape, star_points, inner_ratio)
    fixed = None
    if shape == 'ellipse' and axis:
        major = math.dist(*axis)
        (x0, y0), (x1, y1) = axis
        along = math.degrees(math.atan2(y1-y0, x1-x0))
        # The long axis lies along local x when it matches the folded angle (mod 180).
        fixed = ('w' if abs(_wrap(2*(along-angle))) < 90 else 'h', major)
    (cx, cy, w, h), residual = _solve_box(outline, angle, bbox, fixed)
    if residual > REJECT_RESIDUAL:
        raise ValueError('Shape axis cannot be reconciled with its bounding box')
    placement = {'bbox': [round(cx-w/2), round(cy-h/2), round(cx+w/2), round(cy+h/2)],
                 'rotation': round(angle, 4)}
    review = {'type': 'shape_axis', 'shape': shape, 'axis': axis, 'input_bbox': list(bbox),
              'placement': placement, 'relative_box_error': round(residual, 6), 'warnings': []}
    if residual > WARN_RESIDUAL:
        review['warnings'].append('Axis fit differs from the bounding box by more than 5% of its short side')
    return placement, review


def polygon_local(points):
    """Whole-image vertices -> (bbox, vertices in the bbox's local 0-1 coordinates)."""
    if (not isinstance(points, list) or not 3 <= len(points) <= 32
            or not all(_finite_point(p) for p in points)):
        raise ValueError('polygon needs 3 to 32 finite vertices')
    left, right = min(p[0] for p in points), max(p[0] for p in points)
    top, bottom = min(p[1] for p in points), max(p[1] for p in points)
    if right - left < 2 or bottom - top < 2:
        raise ValueError('polygon has no usable area')
    local = [[round((x-left)/(right-left), 6), round((y-top)/(bottom-top), 6)] for x, y in points]
    return [round(left), round(top), round(right), round(bottom)], local


