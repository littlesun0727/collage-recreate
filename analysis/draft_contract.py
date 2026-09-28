"""Strict, non-mutating validation for the contract in analysis.md (v2-2)."""
import json
import math
import re

CONTRACT_VERSION = 'v2-2'
ID = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
COLLECTIONS = {'slots': 'slot', 'texts': 'text', 'overlays': 'overlay'}


def parse_response(raw):
    """No fence extraction, duplicate-key overwrite, NaN, or silent repair."""
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f'non-JSON number: {value}')

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f'non-finite JSON number: {value}')
        return number

    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                      parse_constant=constant, parse_float=finite_float)


def validate(draft):
    errors, warnings = [], []

    def err(path, code, message):
        errors.append({'path': path, 'code': code, 'message': message})

    def obj(value, path, required, optional=()):
        if not isinstance(value, dict):
            err(path, 'type', 'Expected an object')
            return False
        for key in sorted(set(required) - value.keys()):
            err(f'{path}.{key}', 'required', 'Required field is missing')
        for key in sorted(value.keys() - set(required) - set(optional)):
            err(f'{path}.{key}', 'unexpected', 'Field is not part of this contract')
        return True

    def string(value, path, nullable=False, identifier=False):
        if nullable and value is None:
            return True
        if not isinstance(value, str) or not value.strip():
            err(path, 'string', 'Expected a non-empty string' + (' or null' if nullable else ''))
            return False
        if identifier and not ID.fullmatch(value):
            err(path, 'id', 'Expected a lowercase ID of at most 64 characters')
            return False
        return True

    def enum(value, path, values):
        if not isinstance(value, str) or value not in values:
            err(path, 'enum', 'Expected one of: ' + ', '.join(values))

    def optional_string(value, path, key):
        if key in value:
            string(value[key], f'{path}.{key}')

    def bbox(value, path):
        if not isinstance(value, list) or len(value) != 4 or any(type(n) is not int for n in value):
            err(path, 'bbox_type', 'Expected exactly four integers (not bool or float)')
        elif not (0 <= value[0] < value[2] <= 999 and 0 <= value[1] < value[3] <= 999):
            err(path, 'bbox_range', 'Expected 0 <= min < max <= 999 on both axes')

    top = {'background', 'slots', 'texts', 'overlays', 'layer_order', 'questions'}
    if not obj(draft, '$', top):
        return errors, warnings
    # Relations are evaluated only after shape/type checks, to avoid cascades and crashes.
    uncertainty = []
    arrays = {}
    for name in (*COLLECTIONS, 'layer_order', 'questions'):
        value = draft.get(name)
        if not isinstance(value, list):
            err(f'$.{name}', 'type', 'Expected an array')
            arrays[name] = []
        else:
            arrays[name] = value
    for i, value in enumerate(arrays['questions']):
        string(value, f'$.questions[{i}]')
    bg = draft.get('background')
    if isinstance(bg, dict):
        mode = bg.get('mode')
        enum(mode, '$.background.mode', ('fixed', 'slot'))
        required = {'mode', 'kind', 'background_brief'} if mode == 'fixed' else {'mode', 'kind', 'slot_id'}
        extra = {'review_notes'}
        if mode == 'fixed' and bg.get('kind') == 'photo':
            required.add('preserve_reason')
        obj(bg, '$.background', required, extra)
        enum(bg.get('kind'), '$.background.kind', ('solid', 'texture', 'photo', 'unknown'))
        for key in ('background_brief', 'preserve_reason', 'review_notes'):
            optional_string(bg, '$.background', key)
        if 'slot_id' in bg:
            string(bg['slot_id'], '$.background.slot_id', identifier=True)
        if bg.get('kind') == 'unknown':
            uncertainty.append('$.background.kind')
    else:
        err('$.background', 'type', 'Expected an object')

    items, slots, attachments, sources = {}, {}, [], []
    for collection, kind in COLLECTIONS.items():
        for i, item in enumerate(arrays[collection]):
            path = f'$.{collection}[{i}]'
            required = {'id', 'label', 'source_bbox_1000'}
            optional = {'review_notes'}
            if kind == 'slot':
                required.add('mode')
                optional |= {'display_brief', 'source_slot_id'}
            elif kind == 'text':
                required.add('default_text')
                optional.add('style_brief')
            else:
                required.add('action')
                optional |= {'shape', 'generation_brief', 'attachment', 'requires_exact_content', 'text_content'}
            if not obj(item, path, required, optional):
                continue
            ident = item.get('id')
            if string(ident, path + '.id', identifier=True):
                if ident in items:
                    err(path + '.id', 'duplicate_id', 'ID must be unique across all element arrays')
                else:
                    items[ident] = (kind, item, path)
                    if kind == 'slot':
                        slots[ident] = item
            string(item.get('label'), path + '.label')
            bbox(item.get('source_bbox_1000'), path + '.source_bbox_1000')
            optional_string(item, path, 'review_notes')
            if kind == 'slot':
                mode = item.get('mode')
                enum(mode, path + '.mode', ('photo', 'photo_feather', 'cutout', 'unknown'))
                optional_string(item, path, 'display_brief')
                if mode in ('photo_feather', 'cutout') and 'display_brief' not in item:
                    err(path + '.display_brief', 'required', 'Feathered photos and cutouts require a display description')
                if mode == 'unknown':
                    uncertainty.append(path + '.mode')
                if 'source_slot_id' in item:
                    string(item['source_slot_id'], path + '.source_slot_id', identifier=True)
                    if mode != 'cutout':
                        err(path + '.source_slot_id', 'source_mode', 'Only cutouts may reference a source slot')
                    sources.append((ident, item['source_slot_id'], path))
            elif kind == 'text':
                if 'default_text' in item:
                    string(item['default_text'], path + '.default_text', nullable=True)
                    if item['default_text'] is None:
                        uncertainty.append(path + '.default_text')
                optional_string(item, path, 'style_brief')
            else:
                action = item.get('action')
                enum(action, path + '.action', ('basic_shape', 'reference_generate'))
                if 'requires_exact_content' in item and type(item['requires_exact_content']) is not bool:
                    err(path + '.requires_exact_content', 'type', 'Expected a boolean')
                if action == 'basic_shape':
                    for forbidden in ('generation_brief', 'text_content'):
                        if forbidden in item:
                            err(path + '.' + forbidden, 'forbidden', 'Omit this field for basic_shape, including null')
                    shape = item.get('shape')
                    if obj(shape, path + '.shape', {'kind', 'appearance_brief'}):
                        enum(shape.get('kind'), path + '.shape.kind', ('rectangle', 'rounded_rectangle', 'ellipse', 'dashed_rectangle', 'line', 'polyline'))
                        string(shape.get('appearance_brief'), path + '.shape.appearance_brief')
                if action == 'reference_generate':
                    string(item.get('generation_brief'), path + '.generation_brief')
                    if 'shape' in item:
                        err(path + '.shape', 'forbidden', 'Omit shape for reference_generate, including null')
                    if 'text_content' in item:
                        string(item['text_content'], path + '.text_content', nullable=True)
                        if item.get('requires_exact_content') is not True:
                            err(path + '.requires_exact_content', 'exact_content', 'Decorative text requires true')
                        if item['text_content'] is None:
                            uncertainty.append(path + '.text_content')
                attachment = item.get('attachment')
                if attachment is not None and obj(attachment, path + '.attachment', {'slot_id', 'position'}):
                    string(attachment.get('slot_id'), path + '.attachment.slot_id', identifier=True)
                    enum(attachment.get('position'), path + '.attachment.position', ('above', 'below'))
                    attachments.append((ident, attachment, path))

    layers = arrays['layer_order']
    for i, layer in enumerate(layers):
        path = f'$.layer_order[{i}]'
        if not isinstance(layer, dict):
            err(path, 'type', 'Expected an object')
            continue
        kind = layer.get('type')
        obj(layer, path, {'type'} if kind == 'background' else {'type', 'id'})
        enum(kind, path + '.type', ('background', 'slot', 'text', 'overlay'))
        if kind != 'background':
            string(layer.get('id'), path + '.id', identifier=True)
    if errors:
        return errors, warnings

    if uncertainty and not arrays['questions']:
        err('$.questions', 'unexplained_unknown', 'A question is required for: ' + ', '.join(uncertainty))
    if arrays['questions']:
        warnings.append({'path': '$.questions', 'code': 'open_questions', 'message': 'Questions require review; their relevance cannot be verified structurally'})
    if uncertainty:
        warnings.append({'path': '$.questions', 'code': 'unknown_coverage', 'message': 'Manually verify that questions explain each unknown: ' + ', '.join(uncertainty)})
    bg_id = bg.get('slot_id') if bg['mode'] == 'slot' else None
    if bg_id is not None:
        target = slots.get(bg_id)
        if target is None:
            err('$.background.slot_id', 'reference', 'Must reference an existing slot')
        else:
            if target['source_bbox_1000'] != [0, 0, 999, 999]:
                err('$.background.slot_id', 'background_bbox', 'Background slot must encode the full canvas as [0, 0, 999, 999]')
            if 'source_slot_id' in target:
                err('$.background.slot_id', 'background_source', 'Background cannot be a derived cutout')
            if bg['kind'] == 'photo' and target['mode'] != 'photo':
                err('$.background.slot_id', 'background_mode', 'Photographic background requires photo mode')
    expected_first = {'type': 'background'} if bg['mode'] == 'fixed' else {'type': 'slot', 'id': bg_id}
    if not layers or layers[0] != expected_first:
        err('$.layer_order', 'background_order', 'The designated background must be first')
    positions = {}
    for i, layer in enumerate(layers):
        key = (layer['type'], layer.get('id'))
        path = f'$.layer_order[{i}]'
        if key in positions:
            err(path, 'duplicate_layer', 'Each layer must occur exactly once')
        positions[key] = i
        if layer['type'] == 'background':
            if bg['mode'] != 'fixed':
                err(path, 'extra_background', 'Slot background cannot have a fixed background layer')
        elif layer['id'] not in items or items[layer['id']][0] != layer['type']:
            err(path, 'reference', 'Layer ID and type must match an existing element')
    for ident, (kind, item, path) in items.items():
        if (kind, ident) not in positions:
            err('$.layer_order', 'missing_layer', f'Missing {kind} {ident}')
    for ident, source, path in sources:
        target = slots.get(source)
        if source == ident or target is None or target['mode'] not in ('photo', 'photo_feather') or 'source_slot_id' in target:
            err(path + '.source_slot_id', 'source_reference', 'Must reference a different, non-derived photo or photo_feather slot')
    for ident, attachment, path in attachments:
        target = attachment['slot_id']
        if target not in slots or target == bg_id:
            err(path + '.attachment.slot_id', 'attachment_reference', 'Must reference an existing non-background slot')
            continue
        overlay_pos, slot_pos = positions.get(('overlay', ident)), positions.get(('slot', target))
        if overlay_pos is not None and slot_pos is not None:
            valid = overlay_pos > slot_pos if attachment['position'] == 'above' else overlay_pos < slot_pos
            if not valid:
                err(path + '.attachment.position', 'attachment_order', 'Attachment position contradicts layer_order')
    return errors, warnings


def map_boxes(draft, width, height):
    """Original oriented-image edges, half-open xyxy, integer round-half-up."""
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError('Image dimensions must be positive integers')
    result, errors = [], []
    background_id = draft['background'].get('slot_id') if draft['background']['mode'] == 'slot' else None
    for collection, kind in COLLECTIONS.items():
        for i, item in enumerate(draft[collection]):
            box = item['source_bbox_1000']
            full_background = kind == 'slot' and item['id'] == background_id
            if full_background:
                if box != [0, 0, 999, 999]:
                    raise ValueError('Validate full-canvas background coordinates before mapping')
                # Background semantics determine coverage, without altering the model's JSON.
                x1, y1, x2, y2 = 0, 0, width, height
            else:
                x1, y1, x2, y2 = [(n * size + 500) // 1000 for n, size in zip(box, (width, height, width, height))]
            result.append({'type': kind, 'id': item['id'], 'label': item['label'],
                           'source_bbox_1000': list(box),
                           'mapping_rule': 'full_canvas_background' if full_background else 'normalized_1000', 'pixel_bbox_xyxy': [x1, y1, x2, y2],
                           'pixel_rect_xywh': [x1, y1, x2-x1, y2-y1]})
            if x1 == x2 or y1 == y2:
                errors.append({'path': f'$.{collection}[{i}].source_bbox_1000', 'code': 'pixel_collapse',
                               'message': 'Box rounds to zero pixel area; coordinates were not inflated'})
    return result, errors
