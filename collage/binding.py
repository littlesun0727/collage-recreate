from collage.object_roles import is_customer_photo, is_photo_background

from copy import deepcopy
from jsonschema import Draft202012Validator
from collage import contracts
from collage.layout.window_contract import photo_mode

from collage.crop_quality import expand_crop


def context_for(base, structure):
    """Expose photo descriptions and geometry, with only one derived subject requirement."""
    slots = []
    for photo in structure['objects']:
        if not is_customer_photo(photo):
            continue
        row = {key: deepcopy(photo[key]) for key in
               ('description', 'bbox', 'rotation', 'linked_to', 'kind', 'background_type') if key in photo}
        l, t, r, b = photo['bbox']
        if r <= l or b <= t:
            raise ValueError(photo['id'] + ': photo slot must have positive area')
        row.update(slot_id=photo['id'], aspect_ratio=round((r-l)/(b-t), 6))
        slots.append(row)
    ids = [p['slot_id'] for p in slots]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate photo slot ID')
    result = {'reference_size': deepcopy(base['reference_size']),
              'instructions': base.get('instructions', ''), 'photo_slots': slots,
              'catalog': [{key: deepcopy(asset[key]) for key in ('id', 'size')}
                          for asset in base['catalog']]}
    # Explicit pixel-quality fields survive normalized reference-coordinate mode.
    seen_sources = {}
    for asset, original in zip(result['catalog'], base['catalog']):
        asset['source_size_px'] = deepcopy(asset['size'])
        identity = original.get('sha256', asset['id'])
        if identity in seen_sources:
            asset['duplicate_of'] = seen_sources[identity]
        else:
            seen_sources[identity] = asset['id']
    if not slots:
        return result
    by_id = {p['slot_id']: p for p in slots}
    scale = base.get('output_width', base['reference_size'][0]) / base['reference_size'][0]
    for photo in structure['objects']:
        if not is_customer_photo(photo):
            continue
        slot = by_id[photo['id']]
        # Only `cutout` photos (a removed background with an optional subject outline) need a
        # segmentable subject; a kept background never does.
        cutout = photo_mode(photo) == 'cutout'
        slot['requires_segmentable_subject'] = cutout or photo_mode(photo) == 'popout'
        l,t,r,b = slot['bbox']
        slot['resolution_policy'] = {
            'output_size_px': [max(1,round(r*scale)-round(l*scale)), max(1,round(b*scale)-round(t*scale))],
            'fit': 'contain' if cutout else 'cover',
        }
    for slot in slots:
        linked = slot.get('linked_to')
        if not linked:
            continue
        if linked not in by_id or linked == slot['slot_id'] or by_id[linked].get('linked_to'):
            raise ValueError('linked_to must name an independent photo slot')
        base_slot = by_id[linked]
        base_slot['requires_segmentable_subject'] |= slot['requires_segmentable_subject']
        # A linked pair is scaled from its segmented foreground; don't impose a
        # separate cover-to-base mapping that the renderer no longer uses.
        base_slot['resolution_policy'] = deepcopy(slot['resolution_policy'])
    return result


def schema_for(context, structure=None, *, allow_reuse=False):
    item = deepcopy(contracts.BINDING_PLAN['properties']['photos']['items'])
    slots = [p['slot_id'] for p in context['photo_slots']]
    assets = [a['id'] for a in context['catalog']]
    if slots and not assets:
        raise ValueError('Photo binding requires customer assets')
    groups = sum(not p.get('linked_to') for p in context['photo_slots'])
    if not allow_reuse and groups > sum(not a.get('duplicate_of') for a in context['catalog']):
        raise ValueError('Not enough distinct customer photos for independent slots; reuse is forbidden')
    for field, values in [('slot_id', slots), ('asset_id', assets)]:
        if values:
            item['properties'][field]['enum'] = values
    item['properties']['reason']['minLength'] = 1
    # Keep rendering details local. Only the required crop reaches the output contract.
    # An independent cutout is cut from the whole customer photo, so its crop is fixed.
    modes = {p['id']: photo_mode(p) for p in (structure or {}).get('objects', []) if is_customer_photo(p)}
    full_crop = [p['slot_id'] for p in context['photo_slots']
                 if modes.get(p['slot_id']) == 'cutout' and not p.get('linked_to')]
    if full_crop:
        item['allOf'] = [{'if': {'properties': {'slot_id': {'enum': full_crop}},
                                'required': ['slot_id']},
                          'then': {'properties': {'source_crop': {'const': [0, 0, 1, 1]}}}}]
    return contracts.obj({'photos': {'type': 'array', 'items': item,
                                     'minItems': len(slots), 'maxItems': len(slots)}})


def validate(answer, context, structure, assets, *, allow_reuse=False):
    """Validate selection, then expand undersized crops locally without model retries."""
    Draft202012Validator(schema_for(context, structure, allow_reuse=allow_reuse)).validate(answer)
    by_id = {p['slot_id']: p for p in answer['photos']}
    if len(by_id) != len(answer['photos']) or set(by_id) != {p['slot_id'] for p in context['photo_slots']}:
        raise ValueError('photos must cover every photo slot exactly once')
    for slot in context['photo_slots']:
        oid = slot['slot_id']; row = by_id[oid]
        l, t, r, b = row['source_crop']
        if not l < r or not t < b:
            raise ValueError(oid + ': source_crop must have positive area')
        if not row['reason'].strip():
            raise ValueError(oid + ': reason must describe visible evidence and crop choice')
        linked = slot.get('linked_to')
        if linked:
            for field in ('asset_id', 'source_crop', 'crop_center', 'mirror_x'):
                if row[field] != by_id[linked][field]:
                    raise ValueError(f'{oid}: {field} must match linked base {linked}')
    catalog = {a['id']: a for a in assets}
    used = {}
    for slot in context['photo_slots']:
        if slot.get('linked_to'):
            continue
        row = by_id[slot['slot_id']]; asset = catalog[row['asset_id']]
        identity = asset.get('sha256', asset['id'])
        if not allow_reuse and identity in used:
            raise ValueError(f"{slot['slot_id']}: duplicate customer photo already used by {used[identity]}; only linked_to pairs may share")
        used[identity] = slot['slot_id']
    value = {'schema_version': 'collage-bindings-v1', 'photos': deepcopy(answer['photos']), 'texts': []}
    repaired = {p['slot_id']: p for p in value['photos']}
    for slot in context['photo_slots']:
        if slot.get('linked_to'):
            continue
        row = repaired[slot['slot_id']]
        policy = slot.get('resolution_policy')
        if policy:
            row['source_crop'] = expand_crop(row['source_crop'], catalog[row['asset_id']]['size'],
                policy['output_size_px'], contain=policy['fit']=='contain')
    for slot in context['photo_slots']:
        if slot.get('linked_to'):
            repaired[slot['slot_id']]['source_crop'] = deepcopy(repaired[slot['linked_to']]['source_crop'])
    return value


