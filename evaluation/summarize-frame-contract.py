"""Report model-produced photo/carrier relations without changing analyses."""
import argparse
import json
from pathlib import Path


def inspect(path):
    a = json.loads(path.read_text(encoding='utf-8-sig'))
    photos = [o for o in a['objects'] if o['kind'] == 'photo']
    overlays = [o for o in a['objects'] if o['kind'] == 'overlay']
    legacy = [{'id': o['id'], 'fields': [k for k, present in [
        ('appearance:polaroid', o.get('appearance') == 'polaroid'),
        ('style.card', 'card' in o.get('style', {})),
        ('window_bbox', 'window_bbox' in o)] if present]} for o in photos]
    legacy = [o for o in legacy if o['fields']]
    carriers = []
    for o in overlays:
        slots = [p for p in photos if o.get('photo_id') == p['id'] or p.get('parent_id') == o['id']]
        if slots:
            carriers.append({'id': o['id'], 'label': o['label'], 'bbox': o['bbox'],
                'rotation': o.get('rotation', 0), 'method': o.get('method', 'local'),
                'photos': [{'id': p['id'], 'bbox': p['bbox'], 'rotation': p.get('rotation', 0)} for p in slots]})
    return {'objects': len(a['objects']), 'photos': len(photos),
            'legacy_photos': legacy, 'linked_carriers': carriers}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--baseline', required=True)
    args = parser.parse_args()
    root, baseline = Path(args.root), Path(args.baseline)
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8-sig'))
    cases = []
    for name in manifest['names']:
        stem = Path(name).stem
        fresh = root/stem/'run/analysis-first.json'
        old = baseline/stem/'run/analysis-first.json'
        if not fresh.exists():
            continue
        cases.append({'name': stem, 'old': inspect(old) if old.exists() else None, 'new': inspect(fresh)})
    counts = {}
    for side in ['old', 'new']:
        items = [c[side] for c in cases if c[side]]
        counts[side] = {'legacy_photos': sum(len(x['legacy_photos']) for x in items),
            'linked_carriers': sum(len(x['linked_carriers']) for x in items),
            'linked_windows': sum(len(c['photos']) for x in items for c in x['linked_carriers'])}
    result = {'frozen_cases': len(cases), 'counts': counts,
        'scope': 'Field/relationship inventory only; does not prove frame completeness, window geometry, or rendering correctness.',
        'cases': cases}
    (root/'frame-contract.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
