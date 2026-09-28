"""Reuse measured, unchanged passes; recheck changed units and overlapping neighbors."""
from .common import fingerprint


def signatures(state, row):
    outputs = {o['key']: o for o in row['active']['outputs']}
    return {o['key']: fingerprint({'object': o, 'output': {k: v for k, v in outputs[o['key']].items()
                      if k not in ('file', 'tool_receipt')}, 'engine': state['engine'],
                      'input': state['input']}) for o in row['package']['objects']}


def select_review(state, row):
    objects = {o['key']: o for o in row['package']['objects']}
    cache = row.get('review_cache', {})
    previous = cache.get('receipt', {})
    prior = {r['key']: r for r in previous.get('reviews', [])}
    current = signatures(state, row)
    changed = {k for k in objects if current[k] != cache.get('signatures', {}).get(k)}
    required = changed | {k for k in objects if prior.get(k, {}).get('status') != 'passed'}
    if previous.get('combination_issues'):
        required = set(objects)
    # One hop only: a changed full paper surface affects all objects, while a
    # small label does not force unrelated pictures and decorations to rerun.
    for key, obj in objects.items():
        a = obj['target_box']
        for other in changed:
            boxes = [objects[other]['target_box'], cache.get('boxes', {}).get(other, objects[other]['target_box'])]
            if any(min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]) for b in boxes):
                required.add(key)
                break
    return [k for k in objects if k in required], {k: prior[k] for k in objects if k not in required}
