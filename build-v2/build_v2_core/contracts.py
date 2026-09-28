"""Intent-only planning contract and deterministic task ownership."""
from copy import deepcopy

from .common import BuildError, fields, fingerprint, text_value
from .content_scope import allowed_text, resolve_scope, validate_scope
from .recipes import validate_item as validate_tool_recipe

PLAN_VERSION = 'build-plan-v2'
METHODS = {'draw', 'text', 'generate', 'feather', 'blocked'}


def strings(value, label):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise BuildError('invalid_contract', f'{label}: expected text array')
    return value


def merge_batches(batches, expected):
    items = []
    for batch in batches:
        fields(batch, {'schema_version', 'items'}, label='plan batch')
        if batch['schema_version'] != PLAN_VERSION or not isinstance(batch['items'], list):
            raise BuildError('plan_version', 'Expected build-plan-v2')
        items.extend(batch['items'])
    keys = [v.get('key') for v in items if isinstance(v, dict)]
    if len(keys) != len(items) or len(set(keys)) != len(keys) or set(keys) != set(expected):
        raise BuildError('plan_coverage', 'Batches must cover selected keys exactly once')
    return {'schema_version': PLAN_VERSION, 'items': items}


def validate_plan(plan, inputs, fonts):
    plan = merge_batches([plan], inputs['selected'])
    known = set(inputs['entries']) | {v['key'] for v in inputs['passthrough']}
    for item in plan['items']:
        fields(item, {'key', 'method', 'reason', 'intent', 'content_scope', 'approximations', 'related_keys'}, label='plan item')
        if item['method'] not in METHODS:
            raise BuildError('invalid_method', 'Unknown production method')
        text_value(item['reason'], 'reason')
        strings(item['approximations'], 'approximations')
        related = strings(item['related_keys'], 'related_keys')
        if len(set(related)) != len(related) or set(related) - known or item['key'] in related:
            raise BuildError('invalid_relation', 'Unknown, duplicate or self relation')
        entry = inputs['entries'][item['key']]
        validate_scope(item['content_scope'], entry, inputs)
        intent, method = item['intent'], item['method']
        if not isinstance(intent, dict):
            raise BuildError('invalid_intent', 'Intent must be an object')
        if method == 'blocked':
            fields(intent, {'gap'}, label='blocked intent'); text_value(intent['gap'], 'gap')
            continue
        if entry['type'] == 'text':
            if method != 'text' or not allowed_text(entry):
                raise BuildError('exact_text', 'Resolved text stays an independent text object')
        elif entry['type'] == 'slot':
            if method != {'photo_feather': 'feather'}.get(entry['draft']['mode']):
                raise BuildError('binding_method', 'Customer source/method cannot be replaced')
            if not entry.get('source_asset'):
                raise BuildError('missing_binding', 'Customer source must be bound or blocked')
        elif method not in ('draw', 'generate'):
            raise BuildError('invalid_method', 'Decoration/background requires draw or generate')
        if entry['draft'].get('kind') == 'unknown' or ('text_content' in entry['draft'] and entry['draft']['text_content'] is None):
            raise BuildError('unresolved_content', 'Unknown content must be blocked')
        if method == 'draw':
            fields(intent, {'description', 'coordination'}, label='draw intent')
            text_value(intent['description'], 'description'); strings(intent['coordination'], 'coordination')
        elif method == 'text':
            fields(intent, {'font_id', 'layout_advice'}, label='text intent')
            if intent['font_id'] not in fonts:
                raise BuildError('missing_font', 'Unknown font ID')
            text_value(intent['layout_advice'], 'layout advice')
        else:
            validate_tool_recipe({'key': item['key'], 'method': method, 'reason': item['reason'],
                                  'recipe': intent, 'approximations': item['approximations'],
                                  'content_scope': item['content_scope']}, entry, fonts, inputs)
    return deepcopy(plan)


def tasks_for(plan, inputs, fonts):
    """Undirected connected components after all batches; context never gains ownership."""
    items = {v['key']: v for v in plan['items']}
    adjacency = {k: set() for k in items}
    for key, item in items.items():
        for other in item['related_keys']:
            if other in items and items[other]['method'] != 'blocked' and item['method'] != 'blocked':
                adjacency[key].add(other); adjacency[other].add(key)
    remaining = set(items); tasks = []
    while remaining:
        pending = [min(remaining)]; group = set()
        while pending:
            key = pending.pop()
            if key in group: continue
            group.add(key); pending.extend(adjacency[key] - group)
        remaining -= group
        keys = sorted(group)
        # All neighbors are visible context. Only declared relations invalidate production.
        related = sorted({v for k in keys for v in items[k]['related_keys']} - group)
        all_entries = {**inputs['entries'], **{v['key']: v for v in inputs['passthrough']}}
        objects = []
        for key in keys:
            entry = deepcopy(all_entries[key])
            entry.update(plan=items[key], content_scope=resolve_scope(entry, inputs, items[key]['content_scope']),
                         exact_text=allowed_text(entry), crop='inputs/' + key.replace(':', '__') + '.png')
            objects.append(entry)
        relevant_fonts = {items[k]['intent']['font_id']: fonts[items[k]['intent']['font_id']]
                          for k in keys if items[k]['method'] == 'text'}
        revision = fingerprint({'objects': objects, 'dependencies': [all_entries[k] for k in related],
                                'reference': inputs['info']['reference_sha256'], 'canvas': inputs['info']['canvas_size'],
                                'fonts': relevant_fonts})
        tasks.append({'schema_version': 'build-task-v2', 'id': 'task-' + fingerprint(keys)[:12],
                      'revision': revision, 'owned_keys': keys, 'dependency_keys': related,
                      'objects': objects, 'neighbors': [deepcopy(e) for k, e in all_entries.items() if k not in group],
                      'canvas_size': inputs['info']['canvas_size'], 'reference_size': inputs['info']['reference_size'],
                      'reference': 'inputs/reference.png', 'fonts': fonts,
                      'photo_windows': inputs['passthrough'], 'failure_feedback': [],
                      'output_requirements': {'format': 'PNG RGBA', 'one_file_per_object': True,
                                              'mapping': 'full raster maps to paint_box when supplied (draw only), otherwise target_box; target_box stays unchanged; transparent margins retained',
                                              'text': 'exact parameters plus independently rendered PNG',
                                              'scope': 'owned_keys only; neighbors read-only'}})
    return tasks
