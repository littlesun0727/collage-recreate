"""Binding-only contracts: no layout repair or rendering decisions."""
import hashlib
import json
import re
from pathlib import Path

VERSION = 'bindings-v1'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_json(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def read_json(path):
    return parse_json(Path(path).read_text(encoding='utf-8-sig'))


def read_model_json(path):
    """Accept one JSON code fence, never extract JSON from arbitrary prose."""
    text = Path(path).read_text(encoding='utf-8-sig').strip()
    fenced = re.fullmatch(r'```(?:json)?[ \t]*\r?\n(.*?)\r?\n```', text, re.DOTALL | re.IGNORECASE)
    return parse_json(fenced.group(1) if fenced else text)


def load_slots(draft, version):
    """Check binding inputs only; this is not whole-draft visual/structure approval."""
    if version not in ('v2-1', 'v2-2'):
        raise ValueError('Unsupported draft version')
    if not isinstance(draft, dict) or not isinstance(draft.get('slots'), list):
        raise ValueError('draft.slots must be an array')
    slots = draft['slots']
    indexed = {}
    limit = 1000 if version == 'v2-1' else 999
    for slot in slots:
        if not isinstance(slot, dict):
            raise ValueError('Each slot must be an object')
        ident = slot.get('id')
        if not isinstance(ident, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', ident) or ident in indexed:
            raise ValueError(f'Invalid/duplicate slot ID: {ident}')
        if slot.get('mode') not in ('photo', 'photo_feather', 'cutout', 'unknown'):
            raise ValueError(f'Invalid slot mode: {ident}')
        box = slot.get('source_bbox_1000')
        if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int or not 0 <= v <= limit for v in box):
            raise ValueError(f'Invalid {version} bbox: {ident}')
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError(f'Empty bbox: {ident}')
        indexed[ident] = slot
    for slot in slots:
        if 'source_slot_id' in slot:
            source = indexed.get(slot['source_slot_id'])
            if slot['mode'] != 'cutout' or source is None or source is slot or 'source_slot_id' in source or source['mode'] not in ('photo', 'photo_feather'):
                raise ValueError(f'Invalid source_slot_id: {slot["id"]}')
    return slots


def resolve_overrides(overrides, slots, assets):
    if not isinstance(overrides, dict):
        raise ValueError('Overrides must map slot IDs to asset IDs or full file paths')
    known = {s['id']: s for s in slots}
    by_id = {a['asset_id']: a for a in assets}
    by_path = {str(Path(path).resolve()).casefold(): a['asset_id'] for a in assets
               for path in [a['path'], *a.get('aliases', [])]}
    result = {}
    for ident, value in overrides.items():
        if ident not in known or not isinstance(value, str):
            raise ValueError(f'Unknown slot or invalid override: {ident}')
        aid = value if value in by_id else by_path.get(str(Path(value).resolve()).casefold())
        if aid is None:
            raise ValueError(f'Override asset not in indexed materials: {value}')
        root_id = known[ident].get('source_slot_id', ident)
        if root_id in result and result[root_id] != aid:
            raise ValueError(f'Conflicting same-source overrides: {root_id}')
        result[root_id] = aid
    return result


def validate_selection(data, expected_ids, assets):
    if not isinstance(data, dict) or set(data) != {'bindings'} or not isinstance(data['bindings'], list):
        raise ValueError('Model output must be {"bindings": [...]}')
    valid_assets = {a['asset_id'] for a in assets}
    found = {}
    for row in data['bindings']:
        if not isinstance(row, dict) or set(row) != {'slot_id', 'asset_id', 'reason'}:
            raise ValueError('Each selection requires only slot_id, asset_id, reason')
        ident, aid = row['slot_id'], row['asset_id']
        if not isinstance(ident, str) or ident not in expected_ids or ident in found:
            raise ValueError(f'Unexpected/duplicate selection: {ident}')
        if aid is not None and (not isinstance(aid, str) or aid not in valid_assets):
            raise ValueError(f'Unknown asset ID: {aid}')
        if not isinstance(row['reason'], str) or not row['reason'].strip():
            raise ValueError('Selection reason is required, including unbound slots')
        found[ident] = dict(row)
    if set(found) != set(expected_ids):
        raise ValueError('Selection must cover every requested slot exactly once')
    return found


def assemble_bindings(slots, assets, selected, overrides):
    roots = {}
    for slot in slots:
        ident = slot['id']
        if 'source_slot_id' in slot:
            continue
        if ident in overrides:
            roots[ident] = {'slot_id': ident, 'asset_id': overrides[ident], 'method': 'user', 'reason': '用户指定素材'}
        else:
            roots[ident] = {**selected[ident], 'method': 'visual' if selected[ident]['asset_id'] else 'unbound'}
    # Reserve all successful/user selections first, including later slots.
    # Fill only explicit unmatched results; malformed/failed model calls still fail validation.
    usage = {a['asset_id']: 0 for a in assets}
    for row in roots.values():
        if row['asset_id'] is not None:
            usage[row['asset_id']] += 1
    for row in roots.values():
        if row['asset_id'] is None and assets:
            chosen = min(assets, key=lambda a: usage[a['asset_id']])
            unused = usage[chosen['asset_id']] == 0
            row.update(asset_id=chosen['asset_id'], method='fallback',
                       reason=row['reason'] + ('；未找到匹配，按素材顺序补选尚未使用的客户图片' if unused else '；未找到匹配，素材均已使用，补选使用次数最少的客户图片'))
            usage[chosen['asset_id']] += 1
    rows = []
    for slot in slots:
        ident = slot['id']
        if 'source_slot_id' in slot:
            source = slot['source_slot_id']
            rows.append({'slot_id': ident, 'asset_id': roots[source]['asset_id'], 'method': 'inherited',
                         'source_slot_id': source, 'reason': f'按 draft 复用 {source} 的客户源图；尚未抠图'})
        else:
            rows.append(roots[ident])
    warnings = []
    used = {}
    for row in roots.values():
        if row['asset_id']:
            used.setdefault(row['asset_id'], []).append(row['slot_id'])
    for aid, ids in used.items():
        if len(ids) > 1:
            warnings.append(f'独立 slot 复用了 {aid}: {", ".join(ids)}')
    return rows, warnings
