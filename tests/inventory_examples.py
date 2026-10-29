"""Express historical test scenes in the current model wire format."""
from copy import deepcopy


def current_inventory(value):
    value = deepcopy(value)
    if not isinstance(value, dict) or 'objects' not in value or 'background' in value:
        return value
    rows = value['objects']
    if not isinstance(rows, list) or not all(isinstance(o, dict) for o in rows):
        return value
    backgrounds = [o for o in rows if o.get('kind') == 'background']
    if len(backgrounds) != 1:
        return value
    bg = backgrounds[0]; kind = bg['background_type']
    if kind == 'solid_color':
        background = ({'type': 'transparent'} if bg['color'] == '#00000000'
                      else {'type': 'solid_color', 'color': bg['color']})
    else:
        background = {'type': 'photographic' if kind == 'photo' else kind,
                      'id': bg['id'], 'description': bg['description']}
    value['background'] = background
    value['objects'] = [o for o in rows if o is not bg]
    by_id = {o['id']: o for o in value['objects']}
    for obj in value['objects']:
        if isinstance(obj.get('photo_id'), str):
            obj['photo_id'] = [obj['photo_id']]
        owner = by_id.get(obj.pop('parent_id', None), {})
        if obj.get('kind') == 'photo' and owner.get('kind') == 'frame':
            ids = owner.setdefault('photo_id', [])
            if isinstance(ids, str):
                ids = owner['photo_id'] = [ids]
            if obj['id'] not in ids:
                ids.append(obj['id'])
    if isinstance(value.get('layer_order'), list):
        value['layer_order'] = [oid for oid in value['layer_order'] if oid != bg['id']]
    return value
