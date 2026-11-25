"""Photo window validation and deterministic masks shared by still/Live rendering.

Window parameters are fields of the analysis photo object (see window_contract); there is no
separate plan file. `configure` expands them into the renderer's `photo_window_spec`.
"""
from collage.object_roles import is_customer_photo, is_photo_background
import math

from PIL import ImageColor

from .window_shapes import heart_outline, rounded_quad_outline, unit_outline
from collage.layout.window_contract import spec_from_photo


EDGES = ('top', 'bottom', 'left', 'right')


def lower_opaque_rectangles(analysis, photo_id, specs):
    """Guaranteed rectangular backing, excluding unknown silhouettes/soft edges.

    A bbox intersection alone says nothing about coverage at a fading edge.
    Nonrectangular or rotated backing needs a separate opaque background.
    """
    objects={o['id']:o for o in analysis['objects']}
    order=analysis.get('layer_order',list(objects))
    rectangles = []
    for oid in order[:order.index(photo_id)]:
        obj=objects[oid]
        style = obj.get('style', {})
        if (obj['kind'] not in {'background','photo'} or style.get('opacity',1) != 1
                or obj.get('rotation', 0) % 360):
            continue
        l,t,r,b = obj['bbox']; w,h = r-l,b-t
        if is_customer_photo(obj):
            spec = specs.get(oid)
            if (not spec or spec['subject']['mode'] == 'cutout'
                    or spec['shape']['type'] != 'rectangle'):
                continue
            fade = spec['fade'] if spec.get('fade_direction', 'inside') == 'inside' else dict.fromkeys(EDGES, 0)
            l,r = l+w*fade['left'],r-w*fade['right']
            t,b = t+h*fade['top'],b-h*fade['bottom']
        elif obj.get('background_type') == 'reference_clean' and obj.get('method') == 'extract':
            # The cleanup route admits an opaque residual canvas; failed cleanup is reported as missing.
            pass
        elif (obj.get('method','local') != 'local' or style.get('shape','rectangle') != 'rectangle'
              or style.get('window') or style.get('stroke')
              or ImageColor.getcolor(style.get('fill','#FFFFFF'),'RGBA')[3] != 255):
            continue
        rectangles.append((l,t,r,b))
    return rectangles


def _covered(box, rectangles):
    """Exact union coverage of an axis-aligned strip, including split backing."""
    l,t,r,b = box
    if r <= l or b <= t:
        return True
    xs = sorted({l,r} | {x for rect in rectangles for x in (rect[0],rect[2]) if l < x < r})
    for x0,x1 in zip(xs,xs[1:]):
        intervals = sorted((a[1],a[3]) for a in rectangles if a[0] <= x0 and a[2] >= x1)
        end = t
        for start,stop in intervals:
            if start > end + 1e-7:
                break
            end = max(end,stop)
        if end < b - 1e-7:
            return False
    return True


def fade_limits(analysis, photo_id, specs):
    """Conservative per-edge spans with opaque backing over the entire strip."""
    photo = next(o for o in analysis['objects'] if o['id'] == photo_id)
    l,t,r,b = photo['bbox']; w,h = r-l,b-t
    rects = lower_opaque_rectangles(analysis,photo_id,specs)
    angle = math.radians(photo.get('rotation',0)); c,s = math.cos(angle),math.sin(angle)
    cx,cy = (l+r)/2,(t+b)/2
    rw,rh = analysis['reference_size']
    def supported(edge, amount):
        spec = specs[photo_id]
        if specs[photo_id].get('fade_direction', 'inside') == 'outside':
            # Include corner extensions so a diagonal expansion cannot expose a gap.
            f = specs[photo_id]['fade']
            box = {'top':(l-w*f['left'],t-h*amount,r+w*f['right'],t),
                   'bottom':(l-w*f['left'],b,r+w*f['right'],b+h*amount),
                   'left':(l-w*amount,t-h*f['top'],l,b+h*f['bottom']),
                   'right':(r,t-h*f['top'],r+w*amount,b+h*f['bottom'])}[edge]
        else:
            box = {'top':(l,t,r,t+h*amount),'bottom':(l,b-h*amount,r,b),
                   'left':(l,t,l+w*amount,b),'right':(r-w*amount,t,r,b)}[edge]
        if spec['shape']['type'] != 'rectangle':
            # Curved boundary transitions can occupy any part of the nominal
            # box. Do not infer opaque backing from just its outer four strips.
            f = {**spec['fade'], edge: amount} if spec.get('fade_direction') == 'outside' else dict.fromkeys(EDGES, 0)
            box = (l-w*f['left'], t-h*f['top'], r+w*f['right'], b+h*f['bottom'])
        # A rotated strip's enclosing box is a conservative coverage requirement.
        points = [(cx+c*(x-cx)-s*(y-cy),cy+s*(x-cx)+c*(y-cy))
                  for x in (box[0],box[2]) for y in (box[1],box[3])]
        bounds = (max(0,min(x for x,y in points)),max(0,min(y for x,y in points)),
                  min(rw,max(x for x,y in points)),min(rh,max(y for x,y in points)))
        return _covered(bounds,rects)
    limits = {}
    for edge in EDGES:
        low,high = 0.,.5
        if supported(edge,high):
            limits[edge] = high
            continue
        for _ in range(24):
            mid = (low+high)/2
            if supported(edge,mid): low = mid
            else: high = mid
        limits[edge] = math.floor(low*1_000_000)/1_000_000
    return limits


def photo_specs(analysis):
    """Render specs for every customer photo, keyed by object ID."""
    return {o['id']: spec_from_photo(o) for o in analysis['objects'] if is_customer_photo(o)}


def _polygon_problem(oid, points):
    if len(set(map(tuple, points))) != len(points):
        return oid + ': polygon vertices must be distinct'
    area = abs(sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(points, points[1:]+points[:1])))/2
    if area < .01:
        return oid + ': polygon has negligible area'
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    for i, a in enumerate(points):
        b = points[(i+1) % len(points)]
        for j in range(i+1, len(points)):
            if j == i+1 or (i == 0 and j == len(points)-1):
                continue
            c, d = points[j], points[(j+1) % len(points)]
            if cross(a,b,c)*cross(a,b,d) <= 0 and cross(c,d,a)*cross(c,d,b) <= 0:
                if max(min(a[0],b[0]),min(c[0],d[0])) <= min(max(a[0],b[0]),max(c[0],d[0])) and max(min(a[1],b[1]),min(c[1],d[1])) <= min(max(a[1],b[1]),max(c[1],d[1])):
                    return oid + ': polygon must not self-intersect'
    return None


def check_photos(analysis):
    """Validate the window parameters stored on the analysis photos; return the render specs."""
    photos = {p['id']: p for p in analysis['objects'] if is_customer_photo(p)}
    specs = photo_specs(analysis)
    for oid, spec in specs.items():
        photo = photos[oid]; shape = spec['shape']
        if is_photo_background(photo) and (
                spec['subject']['mode'] != 'preserve' or spec['window_enabled']
                or any(spec['fade'].values()) or spec.get('linked_to')):
            raise ValueError(oid + ': photo background must preserve the full canvas without a mask, fade or linked_to')
        linked = spec.get('linked_to')
        if linked:
            if linked not in specs or linked == oid or specs[linked].get('linked_to'):
                raise ValueError(oid + ': linked_to must name an independent base photo')
            if spec['subject']['mode'] != 'cutout' or specs[linked]['subject']['mode'] != 'preserve':
                raise ValueError(oid + ': linked pop-out needs cutout foreground and preserved base')
        if shape['type'] == 'polygon':
            problem = _polygon_problem(oid, shape['points'])
            if problem:
                raise ValueError(problem)
        if photo.get('background_type') == 'photographic' and spec['subject']['mode'] != 'preserve':
            raise ValueError(oid + ': photographic background must preserve the scene')
        fade = spec['fade']
        if (spec.get('fade_direction', 'inside') == 'inside' and
                (fade['left']+fade['right'] > .9 or fade['top']+fade['bottom'] > .9)):
            raise ValueError(oid + ': opposite fades leave no opaque central region')
        if any(fade.values()):
            limits = fade_limits(analysis, oid, specs)
            for edge in EDGES:
                if fade[edge] > limits[edge] + 1e-6:
                    raise ValueError(f'{oid}: {edge} fade has no overlapping lower opaque coverage '
                                     f'for the full strip; maximum={limits[edge]}. '
                                     'Use zero fade on unsupported edges; keep the base opaque.')
    return specs


def resolve_seams(analysis):
    """Clamp unsupported fades on the analysis photos (bottom to top) and report each change.

    The model only judges where a fade exists; whether the layers below support it is a
    geometric fact the program settles. Everything else the model wrote is left alone.
    """
    photos = {p['id']: p for p in analysis['objects'] if is_customer_photo(p)}
    specs = photo_specs(analysis)
    notes = []
    for oid in analysis['layer_order']:
        if oid not in specs or not any(specs[oid]['fade'].values()):
            continue
        spec, photo = specs[oid], photos[oid]
        limits = fade_limits(analysis, oid, specs)
        changes = []
        for edge in EDGES:
            before = spec['fade'][edge]
            if before > limits[edge]:
                spec['fade'][edge] = photo['fade'][edge] = limits[edge]
                changes.append(f'{edge} {before:g}→{limits[edge]:g}')
        if changes:
            notes.append(oid+'：下层不透明覆盖约束，'+', '.join(changes))
            if not any(spec['fade'].values()):
                del photo['fade']
    return notes


    # appearance is retained as reference information, but may not create another card.


def _heart_outline(steps=80):
    """Compatibility entry point for a heart in unit coordinates."""
    return heart_outline(1, 1, steps)
