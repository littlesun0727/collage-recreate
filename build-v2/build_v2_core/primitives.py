"""Small reusable vector paths, in raster pixel coordinates; no random state."""
import math

import numpy as np


EXTRA_SHAPES = {'circle', 'heart', 'star'}


def shape_path(spec, size):
    """Return (points, closed, pin_vertices). Curves are sampled, not model code."""
    w, h = size
    kind = spec['kind']
    if kind in ('polygon', 'polyline'):
        return np.asarray(spec['points'], dtype=float) * [w, h], kind == 'polygon', True
    x1, y1, x2, y2 = np.asarray(spec['box'], dtype=float) * [w, h, w, h]
    cx, cy = (x1+x2)/2, (y1+y2)/2
    rx, ry = (x2-x1)/2, (y2-y1)/2
    if kind == 'rectangle':
        return np.array([[x1,y1], [x2,y1], [x2,y2], [x1,y2]]), True, True
    if kind == 'rounded_rectangle':
        radius = min(spec.get('radius', .05)*min(w,h), rx, ry)
        if radius <= 0:
            return shape_path(dict(spec, kind='rectangle'), size)
        points = []
        for center, start in (((x2-radius,y1+radius), -90), ((x2-radius,y2-radius), 0),
                              ((x1+radius,y2-radius), 90), ((x1+radius,y1+radius), 180)):
            angles = np.deg2rad(np.linspace(start, start+90, 25))
            points.extend(np.column_stack((np.cos(angles), np.sin(angles)))*radius + center)
        return np.asarray(points), True, False
    if kind in ('circle', 'ellipse'):
        if kind == 'circle':
            rx = ry = min(rx, ry)  # Inscribed true circle, even in a non-square box.
        t = np.linspace(0, 2*math.pi, 193, endpoint=False)
        return np.column_stack((cx+rx*np.cos(t), cy+ry*np.sin(t))), True, False
    if kind == 'star':
        t = np.arange(10)*math.pi/5 - math.pi/2
        radii = np.where(np.arange(10)%2 == 0, 1., spec.get('inner_ratio', .45))*min(rx,ry)
        return np.column_stack((cx+radii*np.cos(t), cy+radii*np.sin(t))), True, True
    if kind == 'heart':
        t = np.linspace(0, 2*math.pi, 193, endpoint=False)
        points = np.column_stack((16*np.sin(t)**3,
                                  -(13*np.cos(t)-5*np.cos(2*t)-2*np.cos(3*t)-np.cos(4*t))))
        points = (points-points.min(axis=0))/np.ptp(points, axis=0)
        return points*[x2-x1,y2-y1]+[x1,y1], True, False
    raise ValueError('Unsupported primitive: ' + kind)
