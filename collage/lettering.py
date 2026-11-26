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


def handle_carrier(image, reference, carrier, texts):
    actions = {}
    for obj, count in texts:
        x, y, r, b = obj['bbox']
        left, top, right, bottom = carrier['bbox']
        if count >= LONG_TEXT_MIN:
            # A long caption can straddle two backings. Repair each shared region.
            box = max(x,left), max(y,top), min(r,right), min(b,bottom)
        else:
            if x < left-4 or y < top-4 or r > right+4 or b > bottom+4:
                continue
            box = x, y, r, b
        if box[2]-box[0] < 4 or box[3]-box[1] < 4:
            continue
        tile = image.crop(box)
        alpha_min, alpha_max = tile.getchannel('A').getextrema()
        if not alpha_max or (count >= LONG_TEXT_MIN and alpha_max < 225):
            continue
        ref = reference.crop(box)
        fill = obj.get('style', {}).get('fill', '#000000')
        mask = match_strokes(ref, tile, fill)
        if mask is None and count < LONG_TEXT_MIN and alpha_min < 255:
            # Reveal can encode fine lettering in alpha. Compare its visible
            # strokes on a flat backing without copying texture from the reference.
            paper = int(np.median(np.asarray(ref.convert('L'))))
            backing = Image.new('RGBA', tile.size, (paper, paper, paper, 255))
            visible = Image.alpha_composite(backing, tile)
            mask = match_strokes(ref, visible, fill)
        if mask is None:
            continue
        if count >= LONG_TEXT_MIN:
            rgb = cv2.inpaint(np.asarray(tile.convert('RGB')), mask, 3, cv2.INPAINT_TELEA)
            patch = Image.fromarray(rgb)
            patch.putalpha(tile.getchannel('A'))
            image.paste(patch, box)
            actions[obj['id']] = 'inpaint'
        else:
            actions[obj['id']] = 'embedded'
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
    with Image.open(run/'prepared/reference.png') as raw:
        reference = raw.convert('RGB')
    for i, carrier in enumerate(layers):
        oid = carrier['id']
        if oid not in files or (carrier['kind'] == 'background' and carrier.get('background_type') != 'reference_clean'):
            continue
        relevant = [(obj, count) for j, obj, count in texts
                    if j > i and obj.get('text') and overlap_fraction(carrier['bbox'], obj['bbox']) > 0]
        if relevant:
            log(f"检查文字承载层 {oid}，重叠文字 {[o['id'] for o, _ in relevant]}")
            with Image.open(files[oid]) as raw:
                image = raw.convert('RGBA')
            evidence[oid] = handle_carrier(image, reference, carrier, relevant)
            log(f"承载层 {oid} 判定 {evidence[oid]}")
            if 'inpaint' in evidence[oid].values():
                image.save(files[oid])
        else:
            evidence[oid] = {}

    decisions = {}
    for i, obj, count in texts:
        oid = obj['id']
        owner = visible_owner(obj, layers[:i], files, evidence) if obj.get('text') and count < LONG_TEXT_MIN else None
        if owner:
            decisions[oid] = {'action': 'embedded', 'owner': owner}
            log(f"文字 {oid} 保留在 {owner}")
            continue
        canvas = Image.new('RGBA', size)
        if obj.get('text'):
            l, t, r, b = obj['bbox']
            tile, _ = render_text(obj['text'], (r-l, b-t), obj.get('style', {}))
            canvas.paste(tile, (l, t))
            decisions[oid] = {'action': 'rendered', 'inpainted_carriers': [
                k for k, values in evidence.items() if values.get(oid) == 'inpaint']}
        else:
            decisions[oid] = {'action': 'unreadable'}
        store(oid, canvas)
        log(f"文字 {oid} 处理结果 {decisions[oid]}")
    return decisions
