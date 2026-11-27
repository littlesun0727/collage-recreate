"""Match reference lettering against recovered pixels before scene composition."""
import re
import unicodedata

import cv2
import numpy as np
from PIL import Image, ImageColor
from .text_render import render_text
from .timings import log


LONG_TEXT_MIN = 5


def word_count(text):
    text = unicodedata.normalize('NFKC', text or '')
    letters = [c for c in text if c.isalpha()]
    han = sum('CJK UNIFIED IDEOGRAPH' in unicodedata.name(c, '') for c in letters)
    if not letters or any(not c.isascii() and 'CJK UNIFIED IDEOGRAPH' not in
                          unicodedata.name(c, '') for c in letters):
        return 0
    english = re.findall(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*", text)
    return max(han, len(english))


def match_strokes(reference, rgba, fill):
    """Use the original lettering as a spatial template, with small shift tolerance."""
    ref = cv2.cvtColor(np.asarray(reference), cv2.COLOR_RGB2GRAY)
    pixels = np.asarray(rgba)
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGBA2GRAY)
    try:
        ink = ImageColor.getcolor(fill, 'L')
    except (TypeError, ValueError, AttributeError):
        return None
    paper = np.median(ref)
    if abs(ink - paper) < 18 or min(ref.shape) < 4:
        return None
    op = cv2.MORPH_BLACKHAT if ink < paper else cv2.MORPH_TOPHAT
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    def strokes(image):
        return cv2.morphologyEx(image, op, kernel)
    template = np.uint8(strokes(ref) > max(14, abs(ink-paper)*.22))
    if not .012 < template.mean() < .42 or template.sum() < 10:
        return None
    signal = strokes(gray)
    actual = np.uint8((signal > max(12, abs(ink-paper)*.16)) & (pixels[:,:,3] > 224))
    pad = 4
    padded = cv2.copyMakeBorder(actual, pad, pad, pad, pad, cv2.BORDER_CONSTANT)
    scores = cv2.matchTemplate(padded, template, cv2.TM_CCORR_NORMED)
    _, score, _, (x, y) = cv2.minMaxLoc(scores)
    if score < .56:
        return None
    shifted = cv2.warpAffine(template, np.float32([[1,0,x-pad],[0,1,y-pad]]),
                             (ref.shape[1], ref.shape[0]))
    margin = np.ones((3,3), np.uint8)
    if (shifted * cv2.dilate(actual, margin)).sum() < template.sum() * .83:
        return None
    mask = actual & cv2.dilate(shifted, np.ones((5,5), np.uint8))
    return cv2.dilate(mask, margin) * np.uint8(255) * (pixels[:,:,3] > 224)


def sparse_letter_mask(reference, tile, fill):
    """Compare colored ink on transparent Reveal layers with ink in the reference."""
    rgba = np.asarray(tile)
    if np.mean(rgba[:,:,3] < 40) < .2:
        return None
    try:
        ink = np.array(ImageColor.getrgb(fill)[:3], dtype=np.int16)
    except (TypeError, ValueError):
        return None
    ref_distance = np.abs(np.asarray(reference).astype(np.int16)-ink).max(axis=2)
    ref = ref_distance < 75
    if not .015 < ref.mean() < .7:
        return None
    near = np.abs(rgba[:,:,:3].astype(np.int16)-ink).max(axis=2) < 95
    actual = near & (rgba[:,:,3] > 40)
    if actual.sum() < 10:
        return None
    kernel = np.ones((5,5),np.uint8)
    vicinity = cv2.dilate(ref.astype(np.uint8),kernel).astype(bool)
    precision = np.count_nonzero(actual & vicinity)/actual.sum()
    recall = np.count_nonzero(ref & cv2.dilate(actual.astype(np.uint8),kernel).astype(bool))/ref.sum()
    if precision < .78 or recall < .28:
        return None
    mask = cv2.dilate((actual & vicinity).astype(np.uint8),kernel)*255
    return mask, recall >= .95


def handle_carrier(image, reference, carrier, texts, retained=None):
    actions = {}
    bounds = image.getchannel('A').getbbox()
    if not bounds:
        return actions
    for obj, count in texts:
        x, y, r, b = obj['bbox']
        if not overlap_fraction(bounds, obj['bbox']):
            continue
        # Compare against the whole glyph, including missing parts of a sparse carrier.
        box = x, y, r, b
        if box[2]-box[0] < 4 or box[3]-box[1] < 4:
            continue
        tile = image.crop(box)
        alpha_min, alpha_max = tile.getchannel('A').getextrema()
        if not alpha_max or (count >= LONG_TEXT_MIN and alpha_max < 225):
            continue
        ref = reference.crop(box)
        fill = obj.get('style', {}).get('fill', '#000000')
        sparse = sparse_letter_mask(ref, tile, fill)
        mask = sparse[0] if sparse is not None else match_strokes(ref, tile, fill)
        if mask is None and count < LONG_TEXT_MIN and alpha_min < 255:
            # Reveal can encode fine lettering in alpha. Compare its visible
            # strokes on a flat backing without copying texture from the reference.
            paper = int(np.median(np.asarray(ref.convert('L'))))
            backing = Image.new('RGBA', tile.size, (paper, paper, paper, 255))
            visible = Image.alpha_composite(backing, tile)
            mask = match_strokes(ref, visible, fill)
        if mask is None:
            continue
        partial = (sparse is not None and not sparse[1]) or overlap_fraction(bounds, box) < .8
        if count >= LONG_TEXT_MIN or partial:
            rgb = cv2.inpaint(np.asarray(tile.convert('RGB')), mask, 3, cv2.INPAINT_TELEA)
            patch = Image.fromarray(rgb)
            alpha = np.asarray(tile.getchannel('A'))
            if sparse is not None or np.median(alpha) < 32:
                alpha = np.where(mask, 0, alpha).astype(np.uint8)
            patch.putalpha(Image.fromarray(alpha))
            image.paste(patch, box)
            actions[obj['id']] = 'inpaint'
        else:
            actions[obj['id']] = 'embedded'
            if retained is not None:
                retained[(carrier['id'],obj['id'])] = (box,mask,sparse is not None)
    return actions


def overlap_fraction(a, b):
    width = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return width * height / max(1, (b[2]-b[0]) * (b[3]-b[1]))


def visible_owner(text, below, files, evidence):
    """A matching label on a hidden carrier cannot stand in for visible text."""
    for layer in reversed(below):
        oid = layer['id']
        if oid in evidence:
            if evidence[oid].get(text['id']) == 'embedded':
                return oid
            with Image.open(files[oid]) as image:
                if image.crop(text['bbox']).getchannel('A').getextrema()[1] > 32:
                    break
        elif overlap_fraction(layer['bbox'], text['bbox']) > .01:
            break
    return None


def finish_text_layers(run, layers, files, store, size):
    texts = [(i, obj, word_count(obj.get('text'))) for i, obj in enumerate(layers)
             if obj['kind'] == 'text']
    if not texts:
        return {}
    evidence = {}
    retained = {}
    with Image.open(run/'prepared/reference.png') as raw:
        reference = raw.convert('RGB')
    for i, carrier in enumerate(layers):
        oid = carrier['id']
        if carrier['kind'] == 'text' or oid not in files or (carrier['kind'] == 'background' and carrier.get('background_type') != 'reference_clean'):
            continue
        relevant = [(obj, count) for j, obj, count in texts
                    if j > i and obj.get('text')]
        if relevant:
            log(f"检查文字承载层 {oid}，重叠文字 {[o['id'] for o, _ in relevant]}")
            with Image.open(files[oid]) as raw:
                image = raw.convert('RGBA')
            evidence[oid] = handle_carrier(image, reference, carrier, relevant, retained)
            log(f"承载层 {oid} 判定 {evidence[oid]}")
            if 'inpaint' in evidence[oid].values():
                image.save(files[oid])
        else:
            evidence[oid] = {}

    decisions = {}
    for i, obj, count in texts:
        oid = obj['id']
        if not (obj.get('text') or '').strip():
            if oid in files:
                decisions[oid] = {'action': 'extracted_unreadable'}
            else:
                box = tuple(obj['bbox'])
                canvas = Image.new('RGBA', size)
                canvas.paste(reference.crop(box), box)
                store(oid, canvas)
                decisions[oid] = {'action':'reference_patch', 'reason':'Reveal omitted unreadable lettering'}
            continue
        owner = visible_owner(obj, layers[:i], files, evidence) if obj.get('text') and count < LONG_TEXT_MIN else None
        for (carrier, text_id), (box, mask, sparse) in retained.items():
            if text_id != oid or carrier == owner:
                continue
            with Image.open(files[carrier]) as raw:
                image = raw.convert('RGBA')
            tile = image.crop(box)
            if sparse:
                tile.putalpha(Image.fromarray(np.where(mask,0,np.asarray(tile.getchannel('A'))).astype(np.uint8)))
            else:
                rgb = cv2.inpaint(np.asarray(tile.convert('RGB')),mask,3,cv2.INPAINT_TELEA)
                alpha = tile.getchannel('A')
                tile = Image.fromarray(rgb).convert('RGBA')
                tile.putalpha(alpha)
            image.paste(tile,box)
            image.save(files[carrier])
            evidence[carrier][oid] = 'inpaint'
        if owner:
            decisions[oid] = {'action': 'embedded', 'owner': owner}
            log(f"文字 {oid} 保留在 {owner}")
            continue
        canvas = Image.new('RGBA', size)
        if obj.get('text'):
            l, t, r, b = obj['bbox']
            tile, info = render_text(obj['text'], (r-l, b-t), obj.get('style', {}), obj.get('description'))
            canvas.paste(tile, (l, t))
            decisions[oid] = {'action': 'rendered', 'inpainted_carriers': [
                k for k, values in evidence.items() if values.get(oid) == 'inpaint']}
            if info['skipped_glyphs']:
                decisions[oid]['skipped_glyphs'] = info['skipped_glyphs']
                if not tile.getchannel('A').getbbox():
                    decisions[oid]['action'] = 'skipped_missing_glyphs'
        else:
            decisions[oid] = {'action': 'unreadable'}
        store(oid, canvas)
        log(f"文字 {oid} 处理结果 {decisions[oid]}")
    return decisions
