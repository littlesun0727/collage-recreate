"""Bounded, deterministic local geometry refinement. No model or network calls."""
from __future__ import annotations

from collections import Counter
import html
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image, ImageColor, ImageDraw

from .analysis import SlotBackground, preview_draft, validate_draft
from .core import digest, file_hash, require, write_json

ALGORITHM = "local-geometry-1"
# OpenCV reductions are single-threaded to keep repeated runs comparable.
cv2.setNumThreads(1)


def expanded(rect, size, fraction):
    x, y, w, h = rect
    px, py = max(8, round(w * fraction)), max(8, round(h * fraction))
    l, t = max(0, x - px), max(0, y - py)
    r, b = min(size[0], x + w + px), min(size[1], y + h + py)
    return [l, t, r - l, b - t]


def bounds(mask, origin=(0, 0), padding=1):
    yy, xx = np.nonzero(mask)
    if not len(xx):
        return None
    l, t = max(0, int(xx.min()) - padding), max(0, int(yy.min()) - padding)
    r, b = min(mask.shape[1], int(xx.max()) + 1 + padding), min(mask.shape[0], int(yy.max()) + 1 + padding)
    return [l + origin[0], t + origin[1], r - l, b - t]


def edges(rect):
    x, y, w, h = rect
    return np.array([x, y, x + w, y + h], dtype=float)


def delta(a, b):
    return float(np.max(np.abs(edges(a) - edges(b))))


def clean_mask(mask, seed_rect=None):
    # Keep dispersed strokes; a compound overlay is not the largest component.
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    minimum = max(2, round(mask.size * .00003))
    keep = np.flatnonzero(stats[:, cv2.CC_STAT_AREA] >= minimum)
    keep = keep[keep != 0]
    if seed_rect:
        x, y, w, h = seed_rect
        seeded = np.unique(labels[y:y+h, x:x+w])
        keep = keep[np.isin(keep, seeded)]
    return np.isin(labels, keep).astype(np.uint8), len(keep)


def ink_candidate(rgb, rect, colors, fraction, tolerance):
    roi = expanded(rect, (rgb.shape[1], rgb.shape[0]), fraction)
    x, y, w, h = roi
    pixels = rgb[y:y+h, x:x+w]
    lab = cv2.cvtColor(pixels, cv2.COLOR_RGB2LAB).astype(np.float32)
    if colors:
        examples = np.array([[ImageColor.getrgb(c)[:3] for c in colors]], dtype=np.uint8)
        targets = cv2.cvtColor(examples, cv2.COLOR_RGB2LAB).astype(np.float32)[0]
        distance = np.min(np.stack([np.linalg.norm(lab - c, axis=2) for c in targets]), axis=0)
        mask = distance <= tolerance
        background_spread = None
    else:
        border = np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])
        background = np.median(border, axis=0)
        border_distance = np.linalg.norm(border - background, axis=1)
        background_spread = float(np.median(border_distance))
        # Automatic contrast only on reasonably uniform surrounding backgrounds.
        threshold = max(tolerance, background_spread + 3 * float(np.median(np.abs(border_distance - background_spread))) + 16)
        mask = np.linalg.norm(lab - background, axis=2) > threshold
    mask, components = clean_mask(mask, [rect[0]-x, rect[1]-y, rect[2], rect[3]])
    foreground_ab = lab[mask > 0, 1:]
    chroma_spread = (float(np.percentile(np.linalg.norm(foreground_ab - np.median(foreground_ab, axis=0), axis=1), 90))
                     if len(foreground_ab) else 0)
    rect_out = bounds(mask, (x, y))
    touches = bool(np.any(mask[0]) or np.any(mask[-1]) or np.any(mask[:, 0]) or np.any(mask[:, -1]))
    return {"candidate_rect": rect_out, "search_rect": roi, "touches_search_edge": touches,
            "components": components, "foreground_fraction": round(float(mask.mean()), 4),
            "background_spread": background_spread, "foreground_chroma_spread":chroma_spread}, mask


def refine_ink(rgb, item, category):
    rect, colors = item.source_rect, item.locate_colors
    result = None
    for attempt, fraction in enumerate((.12, .24, .40)):
        result, mask = ink_candidate(rgb, rect, colors, fraction, 42 if category == "text" and colors else 28)
        if not result["touches_search_edge"]:
            break
    result["expansions"] = attempt
    result["method"] = "color_components" if colors else "local_contrast_components"
    reasons = []
    candidate = result["candidate_rect"]
    if candidate is None:
        reasons.append("no_foreground")
    else:
        if result["touches_search_edge"]:
            reasons.append("foreground_touches_search_edge")
        if result["foreground_fraction"] > .65:
            reasons.append("foreground_fills_search_area")
        if result["background_spread"] is not None and result["background_spread"] > 18:
            reasons.append("textured_background_requires_color_hint")
        # Sensitivity is measured from independent masks, not a claimed probability.
        variations = [ink_candidate(rgb, rect, colors, fraction, t)[0]["candidate_rect"]
                      for t in ((35, 49) if category == "text" and colors else ((22, 34) if colors else (23, 33)))]
        sensitivity = max((delta(candidate, other) if other else float(max(rgb.shape[:2])))
                          for other in variations)
        result["sensitivity_pixels"] = sensitivity
        limit = max(3, .12 * min(rect[2:])) if category == "text" else max(4, .015 * min(rect[2:]))
        if sensitivity > limit:
            reasons.append("unstable_boundary")
        if candidate[2] * candidate[3] < rect[2] * rect[3] * .18:
            reasons.append("candidate_covers_only_small_part")
        if category == "text" and len(colors) <= 1 and result["foreground_chroma_spread"] > 25:
            reasons.append("mixed_foreground_colors")
        if category == "text" and colors:
            contrast, _ = ink_candidate(rgb, rect, [], fraction, 28)
            if (contrast["candidate_rect"] and contrast["background_spread"] <= 18
                    and delta(candidate, contrast["candidate_rect"]) > max(3, rect[3] * .12)):
                reasons.append("color_hint_misses_contrasting_strokes")
        if category == "text" and candidate[3] > rect[3] * 1.75:
            reasons.append("possible_neighbouring_text")
    result["reasons"] = reasons
    result["status"] = "needs_review" if reasons else "refined"
    return result, mask


def refine_photo(rgb, item):
    """Snap each rough side to a supported near-axis image discontinuity."""
    rect = item.source_rect
    x, y, w, h = rect
    roi = expanded(rect, (rgb.shape[1], rgb.shape[0]), .20)
    l, t, rw, rh = roi
    pixels = cv2.cvtColor(rgb[t:t+rh, l:l+rw], cv2.COLOR_RGB2LAB).astype(np.float32)
    vx = pixels[:, 1:] - pixels[:, :-1]
    vy = pixels[1:] - pixels[:-1]
    gx = np.linalg.norm(vx, axis=2)
    gy = np.linalg.norm(vy, axis=2)
    selections, supports, ambiguities = [], [], []
    for side, expected in enumerate((x, y, x+w, y+h)):
        vertical = side in (0, 2)
        origin = l if vertical else t
        radius = max(8, round((w if vertical else h) * .16))
        lo = max(1, expected - origin - radius)
        hi = min((rw if vertical else rh)-1, expected-origin+radius)
        start = max(0, round((y-t+.18*h) if vertical else (x-l+.18*w)))
        end = min(rh if vertical else rw, round((y-t+.82*h) if vertical else (x-l+.82*w)))
        scores = []
        for pos in range(lo, hi+1):
            values = gx[start:end, pos-1] if vertical else gy[pos-1, start:end]
            support = float(np.mean(values > 10)) if values.size else 0
            vectors = vx[start:end, pos-1] if vertical else vy[pos-1, start:end]
            # A photo boundary has a coherent color change; random interior
            # texture has large magnitudes but cancels in signed aggregation.
            strength = min(65.0, float(np.linalg.norm(np.median(vectors, axis=0)))) if values.size else 0
            distance_penalty = 1 + abs(pos + origin - expected) / max(4, radius) * 3.0
            scores.append((strength * support / distance_penalty, pos+origin, support))
        scores.sort(reverse=True)
        if not scores:
            return {"status":"needs_review", "method":"photo_edges", "candidate_rect":None,
                    "search_rect":roi, "reasons":["no_edge_candidates"]}, None
        best = scores[0]
        alternatives = [r for r in scores[1:] if abs(r[1]-best[1]) > 3]
        ambiguities.append(bool(alternatives and alternatives[0][0] > best[0]*.92))
        selections.append(best[1]);supports.append(best[2])
    left, top, right, bottom = selections
    reasons = []
    candidate = [left, top, right-left, bottom-top]
    if right <= left or bottom <= top:
        candidate = None;reasons.append("invalid_edge_order")
    if min(supports) < .65:
        reasons.append("weak_photo_edge")
    if any(ambiguities):
        reasons.append("competing_photo_edges")
    corner_support = []
    if candidate:
        for side, pos in enumerate(selections):
            vertical = side in (0, 2)
            normal = pos - (l if vertical else t) - 1
            first, last = ((top-t, bottom-t) if vertical else (left-l, right-l))
            gradient = gx if vertical else gy.T
            for lo, hi in ((first+2, min(first+6, last-1)), (max(first+1,last-6),last-2)):
                strip = gradient[max(0,lo):min(gradient.shape[0],hi),
                                 max(0,normal-1):min(gradient.shape[1],normal+2)]
                support = float(np.mean(np.max(strip, axis=1)>10)) if strip.size else 0
                corner_support.append(support)
        if sum(v < .4 for v in corner_support) >= 2:
            reasons.append("discontinuous_photo_corners")
    return {"status":"needs_review" if reasons else "refined", "method":"photo_edges",
            "candidate_rect":candidate, "search_rect":roi, "edge_support":supports, "corner_support":corner_support, "reasons":reasons}, None


def refine_element(rgb, item, category, background_id):
    if item.id == background_id:
        return {"status":"unchanged", "method":"full_canvas", "candidate_rect":item.source_rect,
                "search_rect":item.source_rect, "reasons":[]}, None
    if category == "slot":
        if item.mode != "photo":
            return {"status":"needs_review", "method":"unsupported_photo_mode", "candidate_rect":None,
                    "search_rect":item.source_rect, "reasons":["mode_requires_semantic_mask"]}, None
        return refine_photo(rgb, item)
    if category == "overlay" and item.action == "basic_shape" and item.shape.kind == "rectangle":
        result, mask = refine_photo(rgb, item)
        result["method"] = "rectangle_edges"
        return result, mask
    return refine_ink(rgb, item, category)


def refine_layout(input_path, reference, output, *, reuse=None):
    """Write an immutable review bundle. Uncertain candidates never replace input geometry."""
    started = time.monotonic()
    draft, source = validate_draft(input_path, reference)
    output = Path(output).resolve()
    require(not output.exists(), "OUTPUT_EXISTS", "Use a new refinement directory")
    previous = {}
    if reuse:
        path = Path(reuse).resolve() / "localization.json"
        from .core import read_json
        report = read_json(path)
        require(report.get("algorithm") == ALGORITHM, "CACHE_INVALID", "Cache algorithm differs")
        previous = {row["id"]: row for row in report["items"]}
    rgb = np.array(source.convert("RGB"))
    source_sha = file_hash(reference)
    background_id = draft.background.slot_id if isinstance(draft.background, SlotBackground) else None
    records, masks = [], {}
    refined = draft.model_copy(deep=True)
    versions = {"opencv":cv2.__version__, "numpy":np.__version__}
    for category, items in (("slot", refined.slots), ("text", refined.texts), ("overlay", refined.overlays)):
        for item in items:
            key = digest({"algorithm":ALGORITHM, "code_sha256":file_hash(__file__), "versions":versions, "source":source_sha,
                          "category":category, "element":item.model_dump(), "background_id":background_id,
                          "peers":[[p.id,p.source_rect] for p in draft.overlays] if category == "overlay" else []})
            cached = previous.get(item.id, {})
            if cached.get("cache_key") == key:
                row = {**cached, "cache_hit":True}
            else:
                result, mask = refine_element(rgb, item, category, background_id)
                row = {"id":item.id, "category":category, "label":item.label,
                       "rough_rect":item.source_rect.copy(), "cache_key":key, "cache_hit":False, **result}
                if mask is not None:
                    masks[item.id] = mask
            if row["status"] == "refined" and category == "overlay":
                def overlap(box, other):
                    ax, ay, aw, ah = box; bx, by, bw, bh = other
                    return max(0, min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))
                for peer in draft.overlays:
                    if peer.id == item.id:
                        continue
                    area = peer.source_rect[2] * peer.source_rect[3]
                    gained = overlap(row["candidate_rect"],peer.source_rect)-overlap(row["rough_rect"],peer.source_rect)
                    if gained > .15 * area:
                        row["status"] = "needs_review"
                        row["reasons"] = [*row["reasons"], "increased_overlap_with:" + peer.id]
                        break
            if row["status"] == "refined":
                item.source_rect = list(row["candidate_rect"])
            row["effective_rect"] = item.source_rect.copy()
            records.append(row)
            if row["status"] == "needs_review":
                question = "定位待复核：" + item.id + "；" + ", ".join(row["reasons"]) + "。当前保留粗框，详见 localization.json。"
                if question not in refined.questions:
                    refined.questions.append(question)
    # Inputs stay immutable and the report cannot be mistaken for a completed asset mask.
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "draft.refined.json", refined.model_dump(exclude_none=False))
    report = {"algorithm":ALGORITHM, "versions":versions, "source_sha256":source_sha,
              "input_sha256":file_hash(input_path), "refined_sha256":file_hash(output/"draft.refined.json"),
              "counts":dict(Counter(row["status"] for row in records)), "items":records,
              "visual_status":"unreviewed", "masks_are_production_assets":False}
    for name, mask in masks.items():
        (output/"masks").mkdir(exist_ok=True)
        Image.fromarray(mask*255).save(output/"masks"/(name+".png"))
    write_json(output/"localization.json", report)
    preview_draft(output/"draft.refined.json", reference, output/"review")
    comparison = source.copy()
    draw = ImageDraw.Draw(comparison)
    for row in records:
        for rect, color in ((row["rough_rect"], "#ff4757"),
                            (row["candidate_rect"], "#16c784" if row["status"]=="refined" else "#ffbd2e")):
            if rect:
                xx, yy, ww, hh = rect
                draw.rectangle((xx, yy, xx+ww-1, yy+hh-1), outline=color, width=2)
    comparison.save(output/"comparison.png")
    rows = "".join("<tr><td>"+html.escape(row["id"])+"</td><td>"+html.escape(row["status"])+
                   "</td><td>"+html.escape(str(row["rough_rect"]))+"</td><td>"+
                   html.escape(str(row["candidate_rect"]))+"</td><td>"+
                   html.escape(", ".join(row["reasons"]))+"</td></tr>" for row in records)
    page = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>定位前后对比</title><style>body{font:15px/1.6 system-ui;margin:24px}img{width:min(100%,720px)}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ccc}main{overflow-x:auto}</style>
<h1>定位前后对比</h1><p>红：粗框；绿：已采用的候选；黄：待复核候选，草案仍保留粗框。所有结果仍需看图。</p>
<p><a href="review/index.html">打开可点击的有效草案</a> · <a href="localization.json">定位诊断</a></p><img src="comparison.png" alt="原图与粗框、候选框">
<main><table><tr><th>对象</th><th>状态</th><th>粗框</th><th>候选框</th><th>待复核原因</th></tr>""" + rows + "</table></main></html>"
    (output/"index.html").write_text(page, encoding="utf-8")
    return {"ok":True, "draft":str(output/"draft.refined.json"), "report":str(output/"localization.json"),
            "preview":str(output/"index.html"), "comparison":str(output/"comparison.png"),
            "counts":report["counts"], "cache_hits":sum(row["cache_hit"] for row in records),
            "needs_review":[row["id"] for row in records if row["status"]=="needs_review"],
            "elapsed_seconds":round(time.monotonic()-started,3), "visual_status":"unreviewed"}
