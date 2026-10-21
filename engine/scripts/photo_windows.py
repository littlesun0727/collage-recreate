"""Reference window plans and deterministic masks shared by still/Live rendering."""
from copy import deepcopy
import math
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter, ImageOps

from common import ROOT, read


SCHEMA = read(ROOT/'schemas/photo-windows.schema.json')
EDGES = ('top', 'bottom', 'left', 'right')


def subject_stroke(spec):
    return bool(spec and spec['stroke']['enabled'] and spec['stroke']['target'] == 'subject')


def needs_subject_mask(photo):
    """Mask inference is independent of whether the photo keeps its background."""
    spec = photo.get('photo_window_spec')
    if spec:
        return spec['subject']['mode'] == 'cutout' or subject_stroke(spec)
    return photo.get('mode') == 'cutout'


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
        if obj['kind'] == 'photo':
            spec = specs.get(oid)
            if (not spec or spec['subject']['mode'] == 'cutout'
                    or spec['shape']['type'] != 'rectangle' or spec['edge_softness']):
                continue
            fade = spec['fade']
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
        box = {'top':(l,t,r,t+h*amount),'bottom':(l,b-h*amount,r,b),
               'left':(l,t,l+w*amount,b),'right':(r-w*amount,t,r,b)}[edge]
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


def check(plan, analysis):
    Draft202012Validator(SCHEMA).validate(plan)
    photos = {p['id']: p for p in analysis['objects'] if p['kind'] == 'photo'}
    ids = [p['photo_id'] for p in plan['photos']]
    if len(ids) != len(set(ids)) or set(ids) != set(photos):
        raise ValueError('photo_windows must cover every photo ID exactly once')
    specs={p['photo_id']:p for p in plan['photos']}
    for spec in plan['photos']:
        oid = spec['photo_id']; shape = spec['shape']; stroke = spec['stroke']
        linked=spec.get('linked_to')
        if linked:
            if linked not in specs or linked==oid or specs[linked].get('linked_to'):
                raise ValueError(oid+': linked_to must name an independent base photo')
            if spec['subject']['mode']!='cutout' or specs[linked]['subject']['mode']!='preserve':
                raise ValueError(oid+': linked pop-out needs cutout foreground and preserved base')
            if analysis['layer_order'].index(linked)>=analysis['layer_order'].index(oid):
                raise ValueError(oid+': pop-out foreground must be above its base')
        seen_edges=set()
        for seam in spec.get('seams',[]):
            order=analysis['layer_order']
            if seam['underlay_id'] not in order[:order.index(oid)] or seam['edge'] in seen_edges:
                raise ValueError(oid+': seam requires a distinct edge and existing lower layer')
            seen_edges.add(seam['edge'])
        allowed = {'type'} | {'rounded_rect': {'corner_radius'},
                             'star': {'star_points', 'inner_ratio'},
                             'polygon': {'points'}}.get(shape['type'], set())
        if set(shape) - allowed:
            raise ValueError(oid + ': parameters do not belong to this shape')
        if shape['type'] == 'polygon':
            points = shape['points']
            if len(set(map(tuple, points))) != len(points):
                raise ValueError(oid + ': polygon vertices must be distinct')
            area = abs(sum(a[0]*b[1]-a[1]*b[0] for a, b in zip(points, points[1:]+points[:1])))/2
            if area < .01:
                raise ValueError(oid + ': polygon has negligible area')
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
                            raise ValueError(oid + ': polygon must not self-intersect')
        if stroke['enabled'] != (stroke['width'] > 0):
            raise ValueError(oid + ': disabled stroke requires width=0; enabled requires width>0')
        if photos[oid].get('background_type') == 'photographic' and spec['subject']['mode'] != 'preserve':
            raise ValueError(oid + ': photographic background must preserve the scene')
        fade = spec['fade']
        if fade['left']+fade['right'] > .9 or fade['top']+fade['bottom'] > .9:
            raise ValueError(oid + ': opposite fades leave no opaque central region')
        if any(fade.values()):
            limits = fade_limits(analysis,oid,specs)
            for edge in EDGES:
                if fade[edge] > limits[edge] + 1e-6:
                    raise ValueError(f'{oid}: {edge} fade has no overlapping lower opaque coverage '
                                     f'for the full strip; maximum={limits[edge]}. '
                                     'Use zero fade on unsupported edges; keep the base opaque.')
    return plan


def defaults(analysis):
    """Conservative, explicit conversion for failed VLM calls, not visual inference."""
    specs = []
    for photo in analysis['objects']:
        if photo['kind'] != 'photo':
            continue
        style = photo.get('style', {}); box = photo['bbox']
        short = max(1, min(box[2]-box[0], box[3]-box[1]))
        shape = {'type': 'rectangle'}
        if style.get('corner_radius', 0):
            shape = {'type': 'rounded_rect', 'corner_radius': min(.5, style['corner_radius']/short)}
        cut = photo.get('mode') == 'cutout' and photo.get('background_type') != 'photographic'
        width = min(.1, max(0, style.get('outline_width', 0)/short))
        color = style.get('outline_color', '#FFFFFF')
        if len(color) != 7 or not color.startswith('#'):
            color = '#FFFFFF'
        fade = dict.fromkeys(EDGES, 0)
        specs.append({'photo_id': photo['id'], 'shape': shape,
                      'subject': {'mode': 'cutout' if cut else 'preserve'},
                      'stroke': {'enabled': bool(width), 'width': width, 'color': color,
                                 'target': 'subject' if cut else 'window'},
                      'edge_softness': 0, 'fade': fade,
                      'reason': '保守兜底：仅沿用旧分析中显式参数；未完成独立窗口视觉判断'})
    for spec in specs:
        spec['reason']+='；旧 feather 无逐边依据，未推断渐隐方向或宽度'
        photo = next(p for p in analysis['objects'] if p['id'] == spec['photo_id'])
        if photo.get('mode') == 'stroke':
            spec['reason']+='；stroke 仅为初筛，未凭分类猜测描边目标、颜色或宽度'
    return check({'schema_version': 'collage-photo-windows-v1', 'photos': specs}, analysis)


def recover_fades(plan, analysis):
    """Keep complete visual judgments; only reduce unsupported fade parameters."""
    Draft202012Validator(SCHEMA).validate(plan)
    result = deepcopy(plan)
    # Reject malformed/semantically invalid visual data before considering repairs.
    plain = deepcopy(plan)
    for spec in plain.get('photos', []):
        spec['fade'] = dict.fromkeys(EDGES,0)
    check(plain,analysis)
    specs = {p['photo_id']:p for p in result['photos']}; notes = []
    for oid in analysis['layer_order']:
        if oid not in specs:
            continue
        spec = specs[oid]; limits = fade_limits(analysis,oid,specs)
        changes = []
        for edge in EDGES:
            before = spec['fade'][edge]
            if before > limits[edge]:
                spec['fade'][edge] = limits[edge]
                changes.append(f'{edge} {before:g}→{limits[edge]:g}')
        if changes:
            note = oid+'：下层不透明覆盖约束，'+', '.join(changes)
            spec['reason'] += '；程序修正：'+note+'；保留原形状与描边判断，待视觉复核'
            notes.append(note)
    return check(result,analysis), notes


def configure(photo, spec):
    """The separate window plan owns these effects; legacy objects stay untouched."""
    photo['photo_window_spec'] = deepcopy(spec)
    photo['mode'] = 'cutout' if spec['subject']['mode'] == 'cutout' else 'cover'
    for key in ('corner_radius', 'feather', 'outline_width', 'outline_color', 'card', 'stroke', 'stroke_width'):
        photo['style'].pop(key, None)
    # appearance is retained as reference information, but may not create another card.


def resolve_seams(plan, analysis):
    """Apply explicit seam widths, then report coverage repairs without aborting."""
    value=deepcopy(plan)
    for spec in value['photos']:
        if spec['stroke'].get('dash'):
            spec['stroke'].setdefault('pattern','dash')
        for seam in spec.get('seams',[]):
            spec['fade'][seam['edge']]=seam['width']
    value,notes=recover_fades(value,analysis)
    for spec in value['photos']:
        for seam in spec.get('seams',[]):
            seam['width']=spec['fade'][seam['edge']]
        if not any(spec['fade'].values()) and any(word in spec['reason'] for word in ['柔和混合','无缝混合','渐隐过渡']):
            notes.append(spec['photo_id']+'：描述提到混合但没有可执行渐隐，需复核接缝')
    return value,notes


def shape_mask(size, shape):
    w, h = size
    aa = max(1, min(4, int(math.sqrt(16_000_000/max(1,w*h)))))
    mask = Image.new('L', (w*aa, h*aa)); draw = ImageDraw.Draw(mask)
    W, H = mask.size; box = (0, 0, W-1, H-1); kind = shape['type']
    if kind == 'rectangle':
        draw.rectangle(box, fill=255)
    elif kind == 'rounded_rect':
        draw.rounded_rectangle(box, radius=shape['corner_radius']*min(W,H), fill=255)
    elif kind in {'ellipse', 'circle'}:
        if kind == 'circle':
            diameter = min(W,H); x=(W-diameter)/2; y=(H-diameter)/2
            box = (x,y,x+diameter-1,y+diameter-1)
        draw.ellipse(box, fill=255)
    else:
        if kind == 'star':
            count = shape['star_points']; ratio = shape['inner_ratio']
            points = [(.5+.5*(1 if i%2 == 0 else ratio)*math.sin(i*math.pi/count),
                       .5-.5*(1 if i%2 == 0 else ratio)*math.cos(i*math.pi/count)) for i in range(count*2)]
        elif kind == 'heart':
            t = np.linspace(0, 2*math.pi, 240, endpoint=False)
            xs = 16*np.sin(t)**3; ys = 13*np.cos(t)-5*np.cos(2*t)-2*np.cos(3*t)-np.cos(4*t)
            points = list(zip((xs-xs.min())/(xs.max()-xs.min()), (ys.max()-ys)/(ys.max()-ys.min())))
        else:
            points = shape['points']
        draw.polygon([(x*(W-1), y*(H-1)) for x,y in points], fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS) if aa > 1 else mask


def fade_mask(size, fade):
    w,h = size; y,x = np.mgrid[0:h,0:w].astype(np.float32)
    alpha = np.ones((h,w), dtype=np.float32)
    distances = {'left': x+.5, 'right': w-x-.5, 'top': y+.5, 'bottom': h-y-.5}
    for edge in EDGES:
        if fade[edge] > 0:
            span = (w if edge in {'left','right'} else h)*fade[edge]
            v = np.clip(distances[edge]/max(1,span), 0, 1)
            alpha *= v*v*(3-2*v)
    return Image.fromarray(np.rint(alpha*255).astype('uint8'))


def apply_window(im, spec, subject_alpha=None):
    """Window/subject inner strokes fade together; content mask excludes painted stroke."""
    # A cutout's default sticker border belongs outside the subject. Insetting
    # a thick border into a narrow face or limb can erase the customer pixels.
    # Preserve explicit placement and the defaults for ordinary photo windows.
    if (subject_stroke(spec) and spec['subject']['mode'] == 'cutout'
            and 'placement' not in spec['stroke']):
        spec = deepcopy(spec)
        spec['stroke']['placement'] = 'outside'
    if spec['stroke']['enabled'] and (spec['stroke'].get('pattern','dash' if spec['stroke'].get('dash') else 'solid')!='solid'
            or spec['stroke'].get('placement','inside')!='inside' or spec['stroke'].get('offset',0)):
        from window_strokes import apply
        return apply(im,spec,subject_alpha)
    shape = shape_mask(im.size, spec['shape'])
    content = ImageChops.multiply(im.getchannel('A'), shape)
    tile = im.copy(); tile.putalpha(content)
    stroke = spec['stroke']
    if stroke['enabled']:
        width = max(1, round(stroke['width']*min(im.size)))
        preserve_subject = stroke['target'] == 'subject' and spec['subject']['mode'] == 'preserve'
        if preserve_subject:
            if subject_alpha is None or subject_alpha.mode != 'L' or subject_alpha.size != im.size:
                raise ValueError('Preserved subject stroke requires an aligned L subject mask')
            if not subject_alpha.getbbox():
                raise ValueError('Preserved subject stroke has no visible subject')
            boundary = ImageChops.multiply(subject_alpha, im.getchannel('A'))
            # Extend edge values so a subject leaving the crop does not acquire
            # a false closing line along the photo edge. Clip geometry afterwards.
            padded = Image.fromarray(np.pad(np.asarray(boundary), width, mode='edge'))
        else:
            boundary = shape if stroke['target'] == 'window' else content
            padded = ImageOps.expand(boundary, border=width, fill=0)
        inner = padded.filter(ImageFilter.MinFilter(width*2+1)).crop((width,width,width+im.width,width+im.height))
        ring = ImageChops.subtract(boundary, inner)
        if preserve_subject:
            ring = ImageChops.multiply(ring, content)
        color = Image.new('RGBA', im.size, stroke['color']); color.putalpha(ring)
        tile = Image.alpha_composite(tile, color)
        content = ImageChops.multiply(content, ImageChops.invert(ring))
    if spec['edge_softness']:
        radius = spec['edge_softness']*min(im.size); pad = max(1, math.ceil(radius*3))
        # Soften inward only: no customer pixels outside the selected shape.
        boundary = shape if spec['subject']['mode']=='preserve' else tile.getchannel('A').point(lambda v:255 if v>8 else 0)
        soft = ImageOps.expand(boundary, border=pad, fill=0).filter(ImageFilter.GaussianBlur(radius))
        soft = soft.crop((pad,pad,pad+im.width,pad+im.height))
        tile.putalpha(ImageChops.multiply(tile.getchannel('A'), soft))
        content = ImageChops.multiply(content, soft)
    fade = fade_mask(im.size, spec['fade'])
    tile.putalpha(ImageChops.multiply(tile.getchannel('A'), fade))
    return tile, ImageChops.multiply(content, fade)


def canvas_mask(size, box, rotation, spec):
    """Geometric aperture only: never apply fade a second time in a carrier hole."""
    l,t,r,b = box; tile = shape_mask((max(1,r-l), max(1,b-t)), spec['shape'])
    if rotation:
        tile = tile.rotate(-rotation, Image.Resampling.BICUBIC, expand=True)
    result = Image.new('L', size)
    result.paste(tile, (round((l+r-tile.width)/2), round((t+b-tile.height)/2)))
    return result


def reference_crops(run, analysis):
    run = Path(run); folder = run/'prepared/photo-window-crops'; folder.mkdir(parents=True,exist_ok=True)
    images = []; regions = []
    with Image.open(run/'prepared/reference.png') as raw:
        for p in analysis['objects']:
            if p['kind'] != 'photo':
                continue
            l,t,r,b = p['bbox']; margin = max(12, round(.12*max(r-l,b-t)))
            box = [max(0,l-margin),max(0,t-margin),min(raw.width,r+margin),min(raw.height,b+margin)]
            patch = raw.crop(box); patch.thumbnail((768,768))
            path = folder/(p['id']+'.png'); patch.save(path)
            images.append((f"参考 photo {p['id']} 周边裁片；原图区域 {box}", path))
            regions.append({'photo_id':p['id'], 'crop_bbox':box})
    return images, regions


def previews(run, analysis, plan):
    """Render real alpha parameters; subject silhouette is unknown until binding."""
    check(plan, analysis)
    run = Path(run); folder = run/'previews'; folder.mkdir(parents=True,exist_ok=True)
    specs = {p['photo_id']:p for p in plan['photos']}; cells = []
    with Image.open(run/'prepared/reference.png') as raw:
        full = raw.convert('RGBA')
    reference = full.convert('RGB')
    for p in analysis['objects']:
        if p['kind'] != 'photo':
            continue
        spec = specs[p['id']]; l,t,r,b = p['bbox']
        preview_spec = deepcopy(spec)
        if subject_stroke(spec):
            # No customer mask exists at reference analysis time.
            preview_spec['stroke'].update(enabled=False, width=0)
        tile, _ = apply_window(Image.new('RGBA',(r-l,b-t),'#30BAF0'), preview_spec)
        alpha = tile.getchannel('A')
        # Unknown customer silhouette: geometry and fades only, visibly labelled below.
        tint = Image.new('RGBA',tile.size,'#00A8FF'); tint.putalpha(alpha.point(lambda v:round(v*.4)))
        if p.get('rotation'):
            tint = tint.rotate(-p['rotation'],Image.Resampling.BICUBIC,expand=True)
        full.alpha_composite(tint,(round((l+r-tint.width)/2),round((t+b-tint.height)/2)))
        ImageDraw.Draw(full).text((l+3,t+3),p['id'],fill='#FF0055',stroke_width=1,stroke_fill='white')
        tile.thumbnail((240,180))
        yy,xx = np.mgrid[:180,:240]; colors = np.where(((xx//12+yy//12)%2)[...,None],220,250)
        checker = Image.fromarray(np.repeat(colors,3,axis=2).astype('uint8')).convert('RGBA')
        checker.alpha_composite(tile,((240-tile.width)//2,(180-tile.height)//2))
        cell = Image.new('RGB',(480,240),'white'); cell.paste(checker,(240,36))
        patch = ImageOps.contain(reference.crop((l,t,r,b)),(236,180))
        cell.paste(patch,((236-patch.width)//2,36+(180-patch.height)//2))
        draw = ImageDraw.Draw(cell)
        draw.text((4,4),p['id']+' / '+spec['shape']['type'],fill='black')
        draw.text((4,20),'reference | window alpha; subject='+spec['subject']['mode'],fill='black')
        draw.text((4,218),'fade T/B/L/R: '+ '/'.join(str(spec['fade'][e]) for e in EDGES),fill='black')
        if spec['subject']['mode']=='cutout' or subject_stroke(spec):
            draw.text((245,198),'subject silhouette: UNKNOWN',fill='#B00000')
        cells.append(cell)
    path = folder/'photo-windows.png'; full.convert('RGB').save(path)
    paths = [path]
    for offset in range(0,len(cells),8):
        page = cells[offset:offset+8]; sheet = Image.new('RGB',(960,240*math.ceil(len(page)/2)),'#EFEFEF')
        for i,cell in enumerate(page):
            sheet.paste(cell,((i%2)*480,(i//2)*240))
        path = folder/f'photo-window-masks-{offset//8+1}.png'; sheet.save(path); paths.append(path)
    return paths
