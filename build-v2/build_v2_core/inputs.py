"""Version-aware, non-mutating input checks and dependency inventory."""
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[2]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))
from binding.text_contract import effective_draft

from .common import BuildError, box, fields, fingerprint, oriented, read_json, sha, text_value

ID = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
KINDS = {'slots': 'slot', 'texts': 'text', 'overlays': 'overlay'}


def validate_draft(draft, version):
    if version not in ('v2-1', 'v2-2'):
        raise BuildError('draft_version', f'Unsupported draft version: {version}')
    fields(draft, {'background', 'slots', 'texts', 'overlays', 'layer_order', 'questions'}, label='draft')
    for key in (*KINDS, 'layer_order', 'questions'):
        if not isinstance(draft[key], list):
            raise BuildError('draft_structure', f'{key}: expected array')
    for question in draft['questions']:
        text_value(question, 'question')
    bg = draft['background']
    if not isinstance(bg, dict) or bg.get('mode') not in ('fixed', 'slot'):
        raise BuildError('draft_structure', 'Invalid background')
    required = {'mode', 'kind', 'background_brief'} if bg['mode'] == 'fixed' else {'mode', 'kind', 'slot_id'}
    if bg['mode'] == 'fixed' and bg.get('kind') == 'photo':
        required.add('preserve_reason')
    fields(bg, required, {'review_notes'}, 'background')
    if bg['kind'] not in ('solid', 'texture', 'photo', 'unknown'):
        raise BuildError('draft_structure', 'Invalid background kind')
    for name in required - {'mode', 'kind'}:
        text_value(bg[name], name)
    limit = 1000 if version == 'v2-1' else 999
    indexed = {}
    uncertainty = bg['kind'] == 'unknown'
    for collection, kind in KINDS.items():
        for item in draft[collection]:
            required = {'id', 'label', 'source_bbox_1000'}
            optional = {'review_notes'}
            if kind == 'slot':
                required.add('mode'); optional |= {'display_brief', 'source_slot_id'}
            elif kind == 'text':
                required.add('default_text'); optional.add('style_brief')
            else:
                required.add('action'); optional |= {'shape', 'generation_brief', 'attachment', 'requires_exact_content', 'text_content'}
            fields(item, required, optional, kind)
            ident = item['id']
            if not isinstance(ident, str) or not ID.fullmatch(ident) or ident in indexed:
                raise BuildError('draft_structure', 'Invalid or duplicate object id')
            text_value(item['label'], ident)
            box(item['source_bbox_1000'], ident, limit)
            if any(type(v) is not int for v in item['source_bbox_1000']):
                raise BuildError('draft_structure', f'{ident}: coordinates must be integers')
            for name in ('review_notes', 'display_brief', 'style_brief'):
                if name in item:
                    text_value(item[name], name)
            if kind == 'slot':
                if item['mode'] not in ('photo', 'photo_feather', 'cutout', 'unknown'):
                    raise BuildError('draft_structure', f'{ident}: invalid mode')
                if item['mode'] in ('cutout', 'photo_feather'):
                    text_value(item.get('display_brief'), ident)
                uncertainty |= item['mode'] == 'unknown'
            elif kind == 'text':
                if item['default_text'] is not None:
                    text_value(item['default_text'], ident)
                uncertainty |= item['default_text'] is None
            else:
                if item['action'] == 'basic_shape':
                    fields(item.get('shape'), {'kind', 'appearance_brief'}, label=ident)
                    if item['shape']['kind'] not in ('rectangle', 'rounded_rectangle', 'ellipse', 'dashed_rectangle', 'line', 'polyline') or 'generation_brief' in item or 'text_content' in item:
                        raise BuildError('draft_structure', f'{ident}: invalid basic shape')
                    text_value(item['shape']['appearance_brief'], ident)
                elif item['action'] == 'reference_generate':
                    text_value(item.get('generation_brief'), ident)
                    if 'shape' in item:
                        raise BuildError('draft_structure', f'{ident}: conflicting shape')
                else:
                    raise BuildError('draft_structure', f'{ident}: invalid action')
                if 'requires_exact_content' in item and type(item['requires_exact_content']) is not bool:
                    raise BuildError('draft_structure', f'{ident}: invalid exact-content flag')
                if 'text_content' in item:
                    if item.get('requires_exact_content') is not True:
                        raise BuildError('draft_structure', f'{ident}: text requires exact content')
                    if item['text_content'] is not None:
                        text_value(item['text_content'], ident)
                    uncertainty |= item['text_content'] is None
            indexed[ident] = (kind, item)
    expected = {(kind, ident) for ident, (kind, _) in indexed.items()}
    if bg['mode'] == 'fixed':
        expected.add(('background', None)); first = {'type': 'background'}
    else:
        target = indexed.get(bg['slot_id'])
        if not target or target[0] != 'slot' or target[1]['source_bbox_1000'] != [0, 0, limit, limit] or 'source_slot_id' in target[1]:
            raise BuildError('draft_structure', 'Invalid background slot')
        if bg['kind'] == 'photo' and target[1]['mode'] != 'photo':
            raise BuildError('draft_structure', 'Photo background must have photo mode')
        first = {'type': 'slot', 'id': bg['slot_id']}
    positions = {}
    for i, layer in enumerate(draft['layer_order']):
        fields(layer, {'type'} if isinstance(layer, dict) and layer.get('type') == 'background' else {'type', 'id'}, label='layer')
        key = (layer['type'], layer.get('id'))
        if key not in expected or key in positions:
            raise BuildError('draft_structure', 'Invalid or duplicate layer')
        positions[key] = i
    if set(positions) != expected or not draft['layer_order'] or draft['layer_order'][0] != first:
        raise BuildError('draft_structure', 'Missing or incorrectly ordered background/layers')
    for ident, (kind, item) in indexed.items():
        if kind == 'slot' and 'source_slot_id' in item:
            parent = indexed.get(item['source_slot_id'])
            if item['mode'] != 'cutout' or not parent or parent[0] != 'slot' or parent[1]['mode'] not in ('photo', 'photo_feather') or 'source_slot_id' in parent[1]:
                raise BuildError('draft_structure', f'{ident}: invalid source relation')
        if kind == 'overlay' and item.get('attachment') is not None:
            attach = item['attachment']; fields(attach, {'slot_id', 'position'}, label=ident)
            target = indexed.get(attach['slot_id'])
            if not target or target[0] != 'slot' or attach['slot_id'] == bg.get('slot_id') or attach['position'] not in ('above', 'below'):
                raise BuildError('draft_structure', f'{ident}: invalid attachment')
            above = positions[('overlay', ident)] > positions[('slot', attach['slot_id'])]
            if above != (attach['position'] == 'above'):
                raise BuildError('draft_structure', f'{ident}: attachment/layer conflict')
    if uncertainty and not draft['questions']:
        raise BuildError('draft_structure', 'Unknown content must retain questions')
    return indexed


def load_inputs(draft_path, reference_path, binding_path=None, version='auto', width=None, objects=None):
    draft_path, reference_path = Path(draft_path).resolve(), Path(reference_path).resolve()
    draft = read_json(draft_path)
    meta_path = draft_path.parent / 'validation.json'
    meta = read_json(meta_path) if meta_path.is_file() else {}
    binding = read_json(binding_path) if binding_path else None
    versions = [v for v in (meta.get('contract_version'), (binding or {}).get('input', {}).get('draft_contract_version'), version if version != 'auto' else None) if v]
    if len(set(versions)) > 1:
        raise BuildError('draft_version', 'Conflicting declared draft versions')
    version = versions[0] if versions else 'v2-2'
    indexed = validate_draft(draft, version)
    if meta.get('valid') is False:
        raise BuildError('upstream_invalid', 'Analysis validation did not pass')
    draft_hash, ref_hash = sha(draft_path), sha(reference_path)
    if meta.get('image', {}).get('sha256') not in (None, ref_hash):
        raise BuildError('input_changed', 'Reference differs from analysis')
    reference = oriented(reference_path)
    rw, rh = reference.size
    width = rw if width is None else width
    if type(width) is not int or not 64 <= width <= 4096:
        raise BuildError('invalid_size', 'Working width must be 64..4096')
    height = round(rh * width / rw)
    if not 64 <= height <= 8192 or width * height > 20000000:
        raise BuildError('invalid_size', 'Working canvas is too large or too small')
    asset_map, bindings = {}, {}
    if binding:
        if binding.get('schema_version') != 'bindings-v1':
            raise BuildError('binding_version', 'Expected bindings-v1')
        info = binding['input']
        if info.get('draft_sha256') != draft_hash or info.get('reference_sha256') != ref_hash or info.get('reference_size') != [rw, rh]:
            raise BuildError('input_changed', 'Bindings do not match draft/reference')
        for asset in binding['assets']:
            aid = asset['asset_id']
            if aid in asset_map:
                raise BuildError('binding_structure', 'Duplicate asset id')
            asset_map[aid] = asset
        for row in binding['bindings']:
            ident = row['slot_id']
            if ident in bindings or ident not in indexed or indexed[ident][0] != 'slot':
                raise BuildError('binding_structure', 'Invalid or duplicate slot binding')
            if row['asset_id'] is not None and row['asset_id'] not in asset_map:
                raise BuildError('binding_structure', 'Missing bound asset')
            bindings[ident] = row
        if set(bindings) != {s['id'] for s in draft['slots']}:
            raise BuildError('binding_structure', 'Bindings must cover all slots')
        for slot in draft['slots']:
            if 'source_slot_id' in slot and bindings[slot['id']]['asset_id'] != bindings[slot['source_slot_id']]['asset_id']:
                raise BuildError('binding_structure', 'Same-source slots have different assets')
        for aid in {row['asset_id'] for row in bindings.values()} - {None}:
            asset = asset_map[aid]
            if not Path(asset['path']).is_file() or sha(asset['path']) != asset['sha256']:
                raise BuildError('input_changed', f'Customer asset changed: {aid}')
            if list(oriented(asset['path']).size) != asset['size']:
                raise BuildError('input_changed', f'Customer oriented size changed: {aid}')
    if binding and 'text_bindings' in binding:
        try:
            draft = effective_draft(draft, binding['text_bindings'])
        except ValueError as exc:
            raise BuildError('text_binding_structure', str(exc)) from exc
        indexed = validate_draft(draft, version)
    def mapped(item, w, h):
        return [(v * d + 500) // 1000 for v, d in zip(item['source_bbox_1000'], (w, h, w, h))]
    entries, passthrough = {}, []
    if draft['background']['mode'] == 'fixed':
        entries['background'] = {'key': 'background', 'type': 'background', 'draft': draft['background'], 'reference_box': [0, 0, rw, rh], 'target_box': [0, 0, width, height], 'size': [width, height]}
    for ident, (kind, item) in indexed.items():
        ref_box, target_box = mapped(item, rw, rh), mapped(item, width, height)
        if kind == 'slot' and ident == draft['background'].get('slot_id'):
            ref_box, target_box = [0, 0, rw, rh], [0, 0, width, height]
        if ref_box[2] <= ref_box[0] or ref_box[3] <= ref_box[1] or target_box[2] <= target_box[0] or target_box[3] <= target_box[1]:
            raise BuildError('pixel_collapse', f'{ident}: zero-sized mapped box')
        key = f'{kind}:{ident}'
        entry = {'key': key, 'type': kind, 'id': ident, 'draft': item, 'reference_box': ref_box, 'target_box': target_box, 'size': [target_box[2] - target_box[0], target_box[3] - target_box[1]]}
        if binding:
            copy_record = next((r for r in binding.get('text_bindings', []) if r['key'] == key), None)
            if copy_record:
                entry['text_binding'] = copy_record
        if kind == 'slot':
            row = bindings.get(ident, {'slot_id': ident, 'asset_id': None, 'method': 'unbound'})
            entry['binding'] = row
            entry['source_asset'] = asset_map.get(row['asset_id'])
            if item['mode'] in ('photo', 'cutout'):
                passthrough.append(entry)
                continue
        entries[key] = entry
    selected = list(entries) if objects is None else list(objects)
    if len(set(selected)) != len(selected) or any(key not in entries for key in selected):
        raise BuildError('invalid_scope', 'Unknown or duplicate build object key')
    info = {'draft_path': str(draft_path), 'draft_sha256': draft_hash, 'draft_contract_version': version,
            'reference_path': str(reference_path), 'reference_sha256': ref_hash, 'reference_size': [rw, rh],
            'bindings_path': str(Path(binding_path).resolve()) if binding_path else None,
            'bindings_sha256': sha(binding_path) if binding_path else None, 'canvas_size': [width, height],
            'coordinate_rule': 'declared-version / normalized-1000 / half-open / designated-background-full-canvas'}
    return {'info': info, 'draft': draft, 'reference': reference, 'entries': entries, 'selected': selected,
            'passthrough': passthrough, 'binding': binding, 'assets': asset_map}


def object_fingerprint(entry, inputs):
    value = {'draft_version': inputs['info']['draft_contract_version'], 'entry': {k: v for k, v in entry.items() if k not in ('binding', 'source_asset')}}
    value['reference_sha256'] = inputs['info']['reference_sha256']
    if entry.get('source_asset'):
        value['source'] = {k: entry['source_asset'][k] for k in ('asset_id', 'sha256', 'size')}
    return fingerprint(value)
