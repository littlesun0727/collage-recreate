"""Reconcile bottom-to-top batch responses with the analysis overlap constraints."""
from graphlib import TopologicalSorter
from itertools import combinations

from .layout.windows import tilted_envelope
from .timings import log


def reaches(parents, node, ancestor):
    pending = [node]
    visited = set()
    while pending:
        current = pending.pop()
        if current == ancestor:
            return True
        if current not in visited:
            visited.add(current)
            pending.extend(parents[current])
    return False


def scene_order(layers, batches):
    ids = [obj["id"] for obj in layers]
    positions = {oid: (batch, rank) for batch, group in enumerate(batches)
                 for rank, oid in enumerate(group)}
    boxes = {obj["id"]: tilted_envelope(obj["bbox"], obj.get("rotation", 0))
             for obj in layers}
    parents = {oid: set() for oid in ids}
    changes = []
    for lower, upper in combinations(ids, 2):
        if lower == ids[0]:
            parents[upper].add(lower)
            continue
        l, t, r, b = boxes[lower]
        x, y, xx, yy = boxes[upper]
        if max(l, x) >= min(r, xx) or max(t, y) >= min(b, yy):
            continue
        lo, hi = positions.get(lower), positions.get(upper)
        if lo and hi and lo[0] == hi[0] and lo[1] > hi[1]:
            changes.append((lower, upper))
        else:
            parents[upper].add(lower)

    applied = False
    for lower, upper in changes:
        if reaches(parents, upper, lower):
            log(f"保留 {lower} 在 {upper} 下方：360 顺序与其他遮挡约束冲突")
        else:
            parents[lower].add(upper)
            applied = True
    if not applied:
        return ids
    z_order = {}
    for oid in TopologicalSorter(parents).static_order():
        z_order[oid] = 1 + max((z_order[p] for p in parents[oid]), default=-1)
    return sorted(ids, key=z_order.__getitem__)
