"""Closed data language interpreted by build; never evaluates model code."""
import re

from .common import BuildError, box, fields, number, text_value
from .content_scope import validate_scope
from .handdraw import inherit_stroke, resolve_brush

PLAN_VERSION = 'build-plan-v1'
SHAPES = {'rectangle', 'rounded_rectangle', 'ellipse', 'polygon', 'polyline', 'circle', 'heart', 'star'}


def color(value, label='color'):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', value):
        raise BuildError('invalid_parameter', f'{label}: expected #RRGGBB or #RRGGBBAA')


def shape(value, opening=False):
    if not isinstance(value, dict) or value.get('kind') not in SHAPES:
        raise BuildError('unsupported_operation', 'Unsupported shape')
    path = value['kind'] in ('polygon', 'polyline')
    required = {'kind', 'points' if path else 'box'}
    optional = {'radius'} if value['kind'] == 'rounded_rectangle' else {'inner_ratio'} if value['kind'] == 'star' else set()
    if not opening:
        optional |= {'fill', 'stroke', 'stroke_width', 'dash', 'opacity'}
    fields(value, required, optional, 'shape')
    if path:
        points = value['points']
        if not isinstance(points, list) or not 2 <= len(points) <= 1024 or (value['kind'] == 'polygon' and len(points) < 3):
            raise BuildError('invalid_parameter', 'Invalid path point count')
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise BuildError('invalid_parameter', 'Invalid point')
            for v in point:
                number(v, 'point')
    else:
        box(value['box'])
    if opening and value['kind'] == 'polyline':
        raise BuildError('invalid_parameter', 'An opening must enclose an area')
    if 'inner_ratio' in value:
        number(value['inner_ratio'], 'inner_ratio', .15, .85)
    for key in ('radius', 'stroke_width', 'opacity'):
        if key in value:
            number(value[key], key)
    for key in ('fill', 'stroke'):
        if key in value:
            color(value[key], key)
    if not opening and not any(key in value for key in ('fill', 'stroke')):
        raise BuildError('invalid_parameter', 'Shape requires fill or stroke')
    if not opening and value['kind'] == 'polyline' and 'stroke' not in value:
        raise BuildError('invalid_parameter', 'Polyline requires stroke')
    if 'dash' in value:
        if value['kind'] != 'polyline' or not isinstance(value['dash'], list) or len(value['dash']) != 2:
            raise BuildError('invalid_parameter', 'dash is [on, off] for polyline only')
        for v in value['dash']:
            number(v, 'dash', .0001, 1)


def validate_item(item, entry, fonts, inputs):
    fields(item, {'key', 'method', 'reason', 'recipe', 'approximations'}, {'content_scope'}, label='plan item')
    if 'content_scope' in item:
        validate_scope(item['content_scope'], entry, inputs)
    if item['key'] != entry['key']:
        raise BuildError('invalid_reference', 'Wrong plan object key')
    text_value(item['reason'], 'reason')
    if not isinstance(item['approximations'], list):
        raise BuildError('invalid_parameter', 'approximations must be array')
    for note in item['approximations']:
        text_value(note, 'approximation')
    method, recipe = item['method'], item['recipe']
    if method == 'blocked':
        if recipe is not None:
            raise BuildError('invalid_parameter', 'Blocked item recipe must be null')
        return
    if method not in ('draw', 'text', 'generate', 'feather'):
        raise BuildError('unsupported_method', f'Unsupported method: {method}')
    kind = entry['type']
    if kind == 'background' and entry['draft'].get('kind') == 'unknown':
        raise BuildError('unresolved_object', 'Unknown background needs an explicit source decision')
    if kind == 'overlay' and 'text_content' in entry['draft'] and entry['draft']['text_content'] is None:
        raise BuildError('exact_text', 'Unresolved decorative text cannot be guessed')
    if kind == 'text' and method not in ('text', 'generate'):
        raise BuildError('invalid_method', 'Design text requires text or generate recipe')
    if kind == 'slot' and method != {'photo_feather': 'feather'}.get(entry['draft']['mode']):
        raise BuildError('invalid_method', 'Customer slots cannot be generated or redrawn')
    if kind in ('overlay', 'background') and method not in ('draw', 'generate'):
        raise BuildError('invalid_method', 'Overlay/background requires draw or generate')
    if method == 'text':
        fields(recipe, {'text', 'font_id', 'font_size', 'color', 'align', 'line_spacing', 'angle'}, {'stroke_width', 'stroke_color'}, 'text recipe')
        if recipe['text'] != entry['draft']['default_text'] or recipe['text'] is None:
            raise BuildError('exact_text', 'Text must exactly match the effective draft text')
        if recipe['font_id'] not in fonts:
            raise BuildError('missing_font', f'Unknown font id: {recipe["font_id"]}')
        if recipe['align'] not in ('left', 'center', 'right'):
            raise BuildError('invalid_parameter', 'Invalid text alignment')
        number(recipe['font_size'], 'font_size', .001, 2)
        number(recipe['line_spacing'], 'line_spacing', 0, 1)
        number(recipe['angle'], 'angle', -90, 90)
        number(recipe.get('stroke_width', 0), 'stroke_width', 0, .1)
        color(recipe['color'])
        if 'stroke_color' in recipe:
            color(recipe['stroke_color'])
    elif method == 'draw':
        fields(recipe, {'ops', 'openings'}, {'brush', 'raster_scale'}, label='draw recipe')
        brush = resolve_brush(recipe['brush']) if 'brush' in recipe else None
        scale = recipe.get('raster_scale', 1)
        if type(scale) is not int or not 1 <= scale <= 4:
            raise BuildError('invalid_parameter', 'raster_scale must be an integer in 1..4')
        if not isinstance(recipe['ops'], list) or not 1 <= len(recipe['ops']) <= 128:
            raise BuildError('invalid_parameter', 'draw requires 1..128 operations')
        for operation in recipe['ops']:
            if not isinstance(operation, dict):
                raise BuildError('invalid_parameter', 'Each draw operation must be an object')
            if brush and 'dash' in operation:
                raise BuildError('unsupported_operation', 'Hand-drawn dash is not supported; use a separate clean draw recipe')
            shape(inherit_stroke(operation, brush) if brush else operation)
        validate_openings(recipe['openings'], entry, inputs)
    elif method == 'generate':
        fields(recipe, {'prompt', 'background', 'key_color'}, {'openings'}, label='generate recipe')
        text_value(recipe['prompt'], 'prompt')
        if len(recipe['prompt']) > 12000 or recipe['background'] not in ('opaque', 'key'):
            raise BuildError('invalid_parameter', 'Invalid generation parameters')
        color(recipe['key_color'])
        if kind == 'background' and recipe['background'] != 'opaque':
            raise BuildError('invalid_parameter', 'Fixed background must be opaque')
        if kind in ('overlay', 'text') and recipe['background'] != 'key':
            raise BuildError('invalid_parameter', 'Independent decoration/text requires transparency')
        if kind == 'text':
            generated_text_metadata(entry)
            if recipe.get('openings', []) != []:
                raise BuildError('invalid_parameter', 'Generated text cannot contain photo openings')
        # Legacy plans remain readable; generated images never use opening masks.
        if 'openings' in recipe:
            validate_openings(recipe['openings'], entry, inputs)
    elif method == 'feather':
        fields(recipe, {'edge_width', 'shape'}, label='feather recipe')
        number(recipe['edge_width'], 'edge_width', .001, .5)
        if recipe['shape'] not in ('rectangle', 'ellipse'):
            raise BuildError('invalid_parameter', 'Unsupported feather shape')


def generated_text_metadata(entry):
    # Exact wording comes from the effective draft, never from model prose.
    expected = entry['draft'].get('default_text')
    if not isinstance(expected, str) or not expected.strip():
        raise BuildError('exact_text', 'Generated text requires resolved effective draft text')
    return {'expected_text': expected, 'original_text': entry.get('original_text', expected),
            'representation': 'generated-raster', 'editable_as_text': False,
            'change_requires_regeneration': True, 'verification': {'status': 'unreviewed'}}


def validate_openings(openings, entry, inputs):
    if not isinstance(openings, list) or len(openings) > 16:
        raise BuildError('invalid_parameter', 'openings must be an array of at most 16')
    slot_ids = {s['id'] for s in inputs['draft']['slots']}
    seen = set()
    for opening in openings:
        fields(opening, {'slot_id', 'shape'}, label='opening')
        if opening['slot_id'] not in slot_ids or opening['slot_id'] in seen or opening['slot_id'] == inputs['draft']['background'].get('slot_id'):
            raise BuildError('invalid_reference', 'Unknown, background, or duplicate opening slot')
        seen.add(opening['slot_id'])
        shape(opening['shape'], opening=True)


def index_plan(plan, expected):
    fields(plan, {'schema_version', 'items'}, label='plan')
    if plan['schema_version'] != PLAN_VERSION or not isinstance(plan['items'], list):
        raise BuildError('plan_version', 'Expected build-plan-v1 items')
    found = {}
    for row in plan['items']:
        if not isinstance(row, dict) or row.get('key') not in expected or row['key'] in found:
            raise BuildError('invalid_reference', 'Unexpected or duplicate plan key')
        found[row['key']] = row
    if set(found) != set(expected):
        raise BuildError('incomplete_plan', 'Plan must cover the requested object keys exactly once')
    return found
