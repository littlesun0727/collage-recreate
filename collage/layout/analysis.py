from copy import deepcopy
import math
import re
from collage.timings import log
from jsonschema import Draft202012Validator, ValidationError
from collage import contracts
from collage.layout.normalization import unwrap_object
from collage.layout.inventory import schema
from collage.layout import windows


ID = {'type': 'string', 'pattern': '^[a-z][a-z0-9_-]{0,63}$'}
COLOR = {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$'}
# The model writes only `align` of the text styling; the font is chosen by the renderer from the
# content, and the look of the lettering is described in `description`.
TEXT_STYLE_FIELDS = ('align',)
PHOTO_FIELDS = ('mode', 'shape', 'radius', 'star_points', 'points', 'corners', 'axis', 'stroke', 'fade', 'popout')
from collage.layout.window_geometry import CORNER_NAMES


def photo_window_fields(number):
    """Window parameters of a photo; every field is optional and omission means the default."""
    point = {'type': 'array', 'items': number, 'minItems': 2, 'maxItems': 2}
    edge = {'type': 'number', 'minimum': 0, 'maximum': .5}
    return {
        'mode': {'enum': ['window', 'cutout', 'bare']},
        'shape': {'enum': list(windows.WIRE_SHAPES)},
        'radius': {'type': 'number', 'minimum': 0, 'maximum': .5},
        'star_points': {'type': 'integer', 'minimum': 3, 'maximum': 12},
        'points': {'type': 'array', 'items': point, 'minItems': 3, 'maxItems': 32},
        'corners': contracts.obj({k: deepcopy(point) for k in CORNER_NAMES}),
        'axis': {'type': 'array', 'items': point, 'minItems': 2, 'maxItems': 2},
        'stroke': contracts.obj({
            'width': {'type': 'number', 'minimum': .001, 'maximum': .1},
            'color': {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$'},
            'pattern': {'enum': ['solid', 'dash', 'dot']},
            'placement': {'enum': ['outside', 'center', 'inside']},
            'offset': {'type': 'number', 'minimum': 0, 'maximum': .2}}, ['width', 'color']),
        'fade': {'type': 'object', 'additionalProperties': False, 'properties': {
            'direction': {'enum': ['inside', 'outside']},
            'top': edge, 'bottom': edge, 'left': edge, 'right': edge}},
        'popout': {'type': 'array', 'items': number, 'minItems': 4, 'maxItems': 4},
    }


def legacy_schema(size, mode):
    number = {'type': 'number' if mode == 'normalized_1000' else 'integer',
              'minimum': 0, 'maximum': 1000 if mode == 'normalized_1000' else max(size)}
    item = contracts.obj({
        'id': ID, 'kind': {'enum': ['background', 'photo', 'overlay', 'frame', 'text']},
        'background_type': {'enum': ['photo', 'reference_clean', 'solid_color']},
        'bbox': {'type': 'array', 'items': number, 'minItems': 4, 'maxItems': 4},
        'description': {'type': 'string', 'minLength': 1, 'maxLength': 100},
        'subject_type': {'enum': contracts.SUBJECT_TYPES},
        'linked_to': ID,
        'photo_id': {'anyOf': [ID, {'type': 'array', 'items': ID, 'minItems': 1,
                                   'maxItems': 160, 'uniqueItems': True}]}, 'parent_id': ID,
        **photo_window_fields(number),
        'text': {'type': ['string', 'null'], 'description': '保留参考图逐行内容；JSON 中用 \\n 表示实际换行，不能合并成单行。'},
        'color': COLOR,
        **{key: deepcopy(contracts.STYLE['properties'][key]) for key in TEXT_STYLE_FIELDS},
    }, ['id', 'kind', 'bbox'])
    item['allOf'] = [
        {'if': {'properties': {'kind': {'const': 'background'}}},
         'then': {'required': ['background_type'],
                  'properties': {'linked_to': False, 'parent_id': False}},
         'else': {'properties': {'background_type': False}}},
        {'if': {'properties': {'background_type': {'const': 'solid_color'}},
                'required': ['background_type']},
         'then': {'required': ['color']}},
        {'if': {'properties': {'background_type': {'enum': ['photo', 'reference_clean']}},
                'required': ['background_type']},
         'then': {'properties': {'color': False}}},
        {'if': {'properties': {'kind': {'const': 'text'}}},
         'then': {'required': ['text', 'description']}, 'else': {'required': ['description']}},
        {'if': {'properties': {'kind': {'const': 'frame'}}}, 'then': {'required': ['photo_id']}},
        {'if': {'properties': {'kind': {'const': 'photo'}}},
         'else': {'properties': {'linked_to': False, **dict.fromkeys(PHOTO_FIELDS, False)}}}]
    return contracts.obj({
        # Count/ordering are checked by problems(), including providers whose
        # strict schema subset cannot express array-wide contains constraints.
        'objects': {'type': 'array', 'items': item, 'minItems': 1, 'maxItems': 161},
        'layer_order': {'type': 'array', 'items': ID, 'minItems': 1, 'maxItems': 161,
                        'uniqueItems': True},
    })


def normalize(value):
    """Normalize containers, aliases and layer IDs without reordering valid layers."""
    result = unwrap_object(value, {'objects'})
    if not isinstance(result, dict) or not isinstance(result.get('objects'), list):
        return result
    rows = result['objects']
    if len(rows) == 1 and isinstance(rows[0], list) and all(isinstance(o, dict) for o in rows[0]):
        result['objects'] = rows[0]
    background = result.get('background')
    def fix_color(obj):
        color = obj.get('color')
        if isinstance(color, str) and re.fullmatch(r'[0-9a-fA-F]{6}', color):
            obj['color'] = '#' + color
            log("补齐六位颜色的 # 前缀")
    if isinstance(background, dict):
        fix_color(background)
    described = list(result['objects'])
    if isinstance(background, dict) and background.get('type') in ('photographic', 'reference_clean'):
        described.append(background)
    for row in described:
        if isinstance(row, dict):
            description = row.get('description')
            fallback = f"{row.get('kind') or row.get('type', '')} {row.get('id', '')}".strip()
            row['description'] = ((description.strip() if isinstance(description, str) else '') or fallback)[:100]
    for item in result['objects']:
        if not isinstance(item, dict):
            continue
        fix_color(item)
        stroke = item.get('stroke')
        if item.get('kind') == 'photo' and type(stroke) in (float,int) and .001 <= stroke <= .1:
            item['stroke'] = {'width': stroke, 'color': '#FFFFFF'}
            log(f"{item.get('id')} 将数字描边展开为宽度 {stroke}，默认白色")
        elif isinstance(stroke, dict):
            fix_color(stroke)
        for key in ('photo_id', 'parent_id', 'linked_to', 'background_type', 'color', *TEXT_STYLE_FIELDS, *PHOTO_FIELDS):
            if key in item and item[key] is None:
                del item[key]
        for key, owner in (('text', 'text'),):
            if item.get('kind') != owner and item.get(key, 1) is None:
                del item[key]
        if 'fill' in item and 'color' not in item:
            item['color'] = item.pop('fill')
    rows = result['objects']
    if rows and all(isinstance(o, dict) and isinstance(o.get('id'), str) for o in rows):
        ids = list(dict.fromkeys(o['id'] for o in rows))
        result['layer_order'] = normalize_layer_order(ids, result.get('layer_order'))
    return result


def normalize_layer_order(ids, order):
    """Keep the lowest occurrence; insert missing IDs beside their object-list neighbours."""
    known, seen, result = set(ids), set(), []
    for oid in order if isinstance(order, list) else []:
        if isinstance(oid, str) and oid in known and oid not in seen:
            result.append(oid)
            seen.add(oid)
    for i, oid in enumerate(ids):
        if oid in seen:
            continue
        # Earlier object IDs have already been inserted. Anchor after the
        # predecessor, or at the bottom for the first object. Never sort survivors.
        position = result.index(ids[i - 1]) + 1 if i else 0
        result.insert(position, oid)
        seen.add(oid)
    return result


def problems(value, size, mode):
    if isinstance(value, dict) and 'background' in value:
        from collage.layout.inventory import to_internal_inventory, wire_problems
        issues = wire_problems(value, size, mode)
        if issues:
            return issues
        try:
            return problems(to_internal_inventory(value, size, mode), size, mode)
        except (ValueError, ValidationError) as error:
            return [{'path': '$', 'message': str(error)[:1500]}]
    value = normalize(value)
    issues = []
    for e in Draft202012Validator(legacy_schema(size, mode)).iter_errors(value):
        path = list(e.absolute_path)
        issue = {'path': '.'.join(map(str, path)) or '$', 'message': e.message[:400]}
        if len(path) >= 2 and path[0] == 'objects' and isinstance(path[1], int):
            item = value['objects'][path[1]]
            if isinstance(item, dict) and isinstance(item.get('id'), str):
                issue['id'] = item['id']
                if e.validator == 'required':
                    issue['fields'] = [k for k in e.validator_value if k not in e.instance]
                elif len(path) >= 3 and e.validator != 'additionalProperties':
                    issue['fields'] = [path[2]]
        issues.append(issue)
    if not isinstance(value, dict) or not isinstance(value.get('objects'), list):
        return issues[:24]
    objects = value['objects']
    if not all(isinstance(o, dict) and isinstance(o.get('id'), str) for o in objects):
        return issues[:24]
    by_id = {o['id']: o for o in objects}
    indices = {o['id']: i for i, o in enumerate(objects)}
    if len(by_id) != len(objects):
        issues.append({'path': 'objects', 'message': 'Duplicate object IDs'})
    order = value.get('layer_order')
    backgrounds = [o for o in objects if o.get('kind') == 'background']
    if len(backgrounds) != 1:
        issues.append({'path': 'objects', 'message': 'Exactly one kind=background is required; reassess canvas source and object ownership'})
    elif isinstance(order, list) and order and order[0] != backgrounds[0]['id']:
        issues.append({'path': 'layer_order', 'message': 'The unique background must be first in layer_order'})
    if isinstance(order, list) and all(isinstance(oid, str) for oid in order):
        if len(order) != len(objects) or set(order) != set(by_id):
            issues.append({'path': 'layer_order',
                           'message': 'layer_order must contain each object ID exactly once; '
                                      f'missing={sorted(set(by_id)-set(order))}, '
                                      f'unknown={sorted(set(order)-set(by_id))}'})
    from collage.layout.photo_links import inventory_issues
    for issue in inventory_issues(objects, order if isinstance(order, list) else []):
        issues.append({'path': 'objects.' + str(indices[issue['id']]), **issue})
    from collage.frame_links import frame_issues
    for issue in frame_issues(objects):
        issues.append({'path': 'objects.' + str(indices[issue['id']]), **issue})
    for i, item in enumerate(objects):
        def fail(message, fields=()):
            issues.append({'path': f'objects.{i}', 'id': item['id'], 'message': message,
                           'fields': list(fields)})
        box = item.get('bbox')
        if (isinstance(box, list) and len(box) == 4
                and all(isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in box)):
            l, t, r, b = box; w, h = [1000, 1000] if mode == 'normalized_1000' else size
            if not (0 <= l < r <= w and 0 <= t < b <= h):
                fail('bbox must be nonempty and inside the reference', ['bbox'])
        kind = item.get('kind')
        if kind != 'frame' and 'photo_id' in item:
            fail('photo_id belongs to frame')
        if 'parent_id' in item:
            owner = item['parent_id']
            if not isinstance(owner, str) or owner not in by_id or owner == item['id']:
                fail('parent_id must reference another existing object', ['parent_id'])
            else:
                visited = {item['id']}
                while isinstance(owner, str) and owner in by_id:
                    if owner in visited:
                        fail('parent_id must not form a cycle', ['parent_id']); break
                    visited.add(owner); owner = by_id[owner].get('parent_id')
        if kind == 'text':
            if 'text' not in item or (isinstance(item['text'], str) and not item['text'].strip()):
                fail('independent text requires readable text or null', ['text'])
        elif 'text' in item or any(key in item for key in TEXT_STYLE_FIELDS):
            fail('text/typography are only for independent text; describe fixed inscriptions on their carrier')
        if 'color' in item and kind not in {'text', 'background'}:
            fail('extracted objects do not need colour or drawing parameters')
        if kind == 'photo':
            found = windows.field_issues(item)
            for message, fields in found:
                fail(message, fields)
            if not found:
                try:
                    windows.resolve(item, size, mode)
                except windows.WindowError as error:
                    fail(str(error), error.fields)
    return issues[:24]


def compile_inventory(value, size, mode, report=None):
    """Wire inventory -> engine analysis. `report` collects {'warnings': [...], 'geometry': [...]}."""
    report = report if report is not None else {}
    report.update(warnings=[], geometry=[])
    if isinstance(value, dict) and 'background' in value:
        from collage.layout.inventory import to_internal_inventory
        try:
            value = to_internal_inventory(value, size, mode)
        except ValidationError as error:
            raise ValueError(error.message) from error
    value = normalize(value)
    issues = problems(value, size, mode)
    if issues:
        raise ValueError('; '.join(i['path'] + ': ' + i['message'] for i in issues))
    objects, popouts = [], {}
    by_id = {o['id']: o for o in value['objects']}
    photo_geometry, above = {}, []
    # Resolve topmost photos first so lower photos can see the actual compiled silhouettes.
    for oid in reversed(value['layer_order']):
        source = by_id[oid]
        if source['kind'] == 'photo':
            resolved = windows.resolve(source, size, mode, above=above)
            photo_geometry[oid] = resolved
            outline = windows.window_outline(resolved[0])
            if outline is not None:
                above.append(outline)
    for source in value['objects']:
        item = {k: deepcopy(source[k]) for k in ('id', 'kind', 'bbox', 'description', 'photo_id', 'parent_id', 'linked_to', 'background_type', 'subject_type') if k in source}
        if mode == 'normalized_1000':
            item['bbox'] = [round(v * size[i % 2] / 1000) for i, v in enumerate(item['bbox'])]
        item['label'] = item['description'][:30]
        kind = item['kind']
        item['method'] = 'extract' if kind in {'overlay', 'frame'} else 'local'
        if kind == 'photo':
            fields, popout, review = photo_geometry[source['id']]
            item.update(fields)
            if popout:
                popouts[item['id']] = popout
            if review:
                report['geometry'].append({**review, 'photo_id': item['id']})
        if kind == 'background':
            item['bbox'] = [0, 0, *size]
            item['rotation'] = 0
            item['method'] = 'extract' if source['background_type'] == 'reference_clean' else 'local'
            if source.get('color'):
                item['style'] = {'fill': source['color']}
        if kind == 'text':
            item['text'] = source['text']
            item['text_status'] = 'known' if source['text'] else 'unreadable'
            item['style'] = {k: deepcopy(source[v]) for k, v in
                             [('fill', 'color'), *((key, key) for key in TEXT_STYLE_FIELDS)] if v in source}
        objects.append(item)
    windows.unify_group_tilt([obj for obj in objects if 'corners' not in by_id[obj['id']]],
                             report['warnings'])
    objects, order = windows.add_popouts(objects, deepcopy(value['layer_order']), popouts, report['warnings'])
    analysis = {'schema_version': 'collage-analysis-v1', 'reference_size': list(size),
                'objects': objects, 'layer_order': order}
    from collage.layout.photo_windows import resolve_seams, check_photos
    report['warnings'] += [{'code': 'seam_adjusted', 'message': note} for note in resolve_seams(analysis)]
    check_photos(analysis)
    Draft202012Validator(contracts.ANALYSIS).validate(analysis)
    return analysis
