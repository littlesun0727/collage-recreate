from inventory_examples import current_inventory
from collage import prompts
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image
from jsonschema import Draft202012Validator, ValidationError

from collage import contracts
from collage.workflow.stages import analyze as structure
from collage.workflow.stages import photo_windows as window_geometry
from collage.vlm.client import Client, ModelError
from collage.config import Config
from collage.vlm.coordinates import ReferenceCoordinates
from collage.storage import read, save


def inventory():
    return {'objects': [
        {'id': 'bg', 'kind': 'background', 'background_type': 'solid_color', 'bbox': [0, 0, 1000, 1000], 'description': 'white background', 'color': '#FFFFFF'},
        {'id': 'photo', 'kind': 'photo', 'bbox': [200, 200, 700, 700], 'description': 'portrait'},
        {'id': 'frame', 'kind': 'frame', 'bbox': [180, 180, 720, 760], 'description': 'printed frame with Music and timestamps', 'photo_id': 'photo'},
        {'id': 'title', 'kind': 'text', 'bbox': [200, 30, 800, 100], 'description': 'ordinary title', 'text': 'Hello', 'color': '#123456'},
    ], 'layer_order': ['bg', 'photo', 'frame', 'title']}


def test_inventory_compiles_to_valid_engine_without_model_text_ledger(tmp_path):
    save(tmp_path/'input.json', {'reference_size': [1200, 1600]})
    source = inventory(); original = deepcopy(source)
    result = structure.compile_inventory(source, [1200, 1600], 'normalized_1000', tmp_path)
    assert source == original
    assert result['analysis']['objects'][1]['bbox'] == [240, 320, 840, 1120]
    assert result['analysis']['layer_order'] == ['bg', 'photo', 'frame', 'title']
    assert result['analysis']['objects'][2]['method'] == 'extract'
    assert 'style' not in result['analysis']['objects'][2]
    assert 'rotation' not in result['analysis']['objects'][2]
    assert [u['text_id'] for u in result['text_units']] == ['title']
    assert result['analysis']['objects'][3]['style']['fill'] == '#123456'


def test_complex_background_and_decorative_lettering_keep_reference_pixels():
    value = {'objects': [
        {'id': 'bg', 'kind': 'background', 'background_type': 'reference_clean', 'bbox': [0, 0, 1000, 1000], 'description': 'printed paper'},
        {'id': 'art', 'kind': 'overlay', 'bbox': [10, 10, 100, 100], 'description': 'handwritten label with backing'}]}
    result = structure.compile_inventory(value, [1000, 1000], 'normalized_1000')
    assert result['analysis']['objects'][0]['background_type'] == 'reference_clean'
    assert all(o['method'] == 'extract' for o in result['analysis']['objects'])
    assert not result['text_units']


@pytest.mark.parametrize('change', ['bad_frame', 'bad_box', 'duplicate', 'cycle', 'extra_style'])
def test_structural_errors_are_not_silently_repaired(change):
    value = inventory()
    if change == 'bad_frame': value['objects'][2]['photo_id'] = 'missing'
    if change == 'bad_box': value['objects'][1]['bbox'] = [900, 0, 800, 1000]
    if change == 'duplicate': value['objects'][2]['id'] = 'photo'
    if change == 'cycle':
        value['objects'][1]['parent_id'] = 'frame'; value['objects'][2]['parent_id'] = 'photo'
    if change == 'extra_style': value['objects'][2]['style'] = {'shape': 'rectangle'}
    with pytest.raises(ValueError): structure.compile_inventory(value, [1000, 1000], 'normalized_1000')


def test_optional_null_and_known_alias_only():
    value = inventory(); value['objects'][1]['parent_id'] = None
    value['objects'][3]['fill'] = value['objects'][3].pop('color')
    normalized = structure.normalize(value)
    assert 'parent_id' not in normalized['objects'][1]
    assert normalized['objects'][3]['color'] == '#123456'
    assert 'fill' in value['objects'][3]
    bad = inventory(); bad['objects'][3]['text'] = None
    result = structure.compile_inventory(bad, [1000, 1000], 'normalized_1000')
    assert result['analysis']['objects'][3]['text_status'] == 'unreadable'


@pytest.mark.parametrize('explicit', [False, True])
def test_analysis_preserves_order_with_multiple_frames_and_foreground(explicit):
    bg, photo, frame, title = inventory()['objects']
    photo['parent_id'] = 'frame'  # A carrier association is not an ordering dependency.
    second_frame = {**deepcopy(frame), 'id': 'frame_2'}
    second_frame['kind'] = 'overlay'
    second_frame.pop('photo_id')
    front = {**deepcopy(photo), 'id': 'front', 'linked_to': 'photo'}
    front.pop('parent_id')
    front_frame = {**deepcopy(frame), 'id': 'front_frame', 'photo_id': 'front'}
    top = {'id': 'top', 'kind': 'overlay', 'bbox': [400, 300, 600, 400],
           'description': 'Independent sticker above the foreground'}
    value = {'objects': [bg, frame, title, front, second_frame, photo, front_frame, top]}
    expected = [o['id'] for o in value['objects']]
    if explicit:
        expected = ['bg', 'photo', 'front_frame', 'frame', 'title', 'front', 'top', 'frame_2']
        value['layer_order'] = expected
    original = deepcopy(value)
    normalized = structure.normalize(value)
    assert normalized['objects'] == original['objects']
    assert {o['id']: o for o in normalized['objects']} == {o['id']: o for o in original['objects']}
    assert value == original and structure.normalize(normalized) == normalized
    assert structure.repair_plan(value, [1000, 1000], 'normalized_1000') is None
    assert structure.compile_inventory(value, [1000, 1000], 'normalized_1000')['analysis']['layer_order'] == expected


@pytest.mark.parametrize('order,expected', [
    (['a', 'd', 'f'], ['a', 'b', 'c', 'd', 'e', 'f']),
    (['c', 'd'], ['a', 'b', 'c', 'd', 'e', 'f']),
    (['d', 'c', 'f'], ['a', 'b', 'd', 'e', 'c', 'f']),
    (['d', 'a', 'f'], ['d', 'e', 'a', 'b', 'c', 'f']),
    (['f', 'd', 'c', 'b', 'a', 'e'], ['f', 'd', 'c', 'b', 'a', 'e']),
])
def test_missing_layers_follow_object_neighbours_without_sorting_existing_ids(order, expected):
    value = {'objects': [{'id': oid, 'kind': 'overlay', 'bbox': [0, 0, 10, 10],
                          'description': 'Independent decoration'} for oid in 'abcdef'],
             'layer_order': order}
    original = deepcopy(value)
    normalized = structure.normalize(value)
    assert normalized['layer_order'] == expected
    assert normalized['objects'] == original['objects'] and value == original
    assert structure.normalize(normalized) == normalized
    assert structure.repair_plan(value, [100, 100], 'pixel') is None


@pytest.mark.parametrize('output', ['json_object', 'json_schema'])
@pytest.mark.parametrize('order_fields,expected', [
    ({'layer_order': ['bg', 'title']}, ['bg', 'photo', 'frame', 'title']),
    ({'layer_order': ['frame', 'photo', 'title']}, ['bg', 'frame', 'photo', 'title']),
    ({'layer_order': ['bg', 'frame', 'photo', 'frame', 'title', 'photo']}, ['bg', 'frame', 'photo', 'title']),
    ({'layer_order': ['ghost', 'bg', 'frame', 'photo', 'UNKNOWN ID', 'title']}, ['bg', 'frame', 'photo', 'title']),
    ({'layer_order': ['title', 'photo', 'photo', 'ghost']}, ['bg', 'title', 'photo', 'frame']),
    ({'layer_order': []}, ['bg', 'photo', 'frame', 'title']),
    ({}, ['bg', 'photo', 'frame', 'title']),
    ({'layer_order': None}, ['bg', 'photo', 'frame', 'title']),
])
def test_layer_id_faults_are_fixed_locally_in_one_analysis_request(tmp_path, output, order_fields, expected):
    import json
    original = inventory()
    original.pop('layer_order')
    original.update(deepcopy(order_fields))
    wire = current_inventory(original)
    if output == 'json_schema':
        fields = structure.schema([1000, 1000], 'normalized_1000')['properties']['objects']['items']['properties']
        wire['objects'] = [{key: row.get(key) for key in fields} for row in wire['objects']]
    calls = []
    def transport(payload):
        calls.append(payload)
        assert len(calls) == 1, 'Layer ID faults must not request a VLM repair'
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(wire)}}]}
    config = Config(model='openai/gpt-5.4', coordinate_mode='normalized_1000',
                    response_format=output, max_requests=1)
    client = Client(tmp_path/'workflow/vlm', config, transport=transport)
    save(tmp_path/'input.json', {'reference_size': [1000, 1000]})
    written = []
    runner = SimpleNamespace(run=tmp_path, control=tmp_path/'workflow', config=config, client=client,
        progress=lambda *args: None, reference_context=lambda: {'reference_size': [1000, 1000]},
        analysis_images=lambda: [], candidates=client.candidates, write_reference=written.append)
    structure.execute(runner)
    assert len(calls) == client.usage()['requests'] == 1
    assert calls[0]['response_format']['type'] == output
    assert written[0]['analysis']['layer_order'] == expected
    saved = read(tmp_path/'structure.json')
    assert saved['layer_order'] == expected[1:] and saved['objects'] == current_inventory(original)['objects']
    evidence = read(tmp_path/'workflow/structure-analysis.json')
    assert evidence['first_pass_valid'] and not evidence['repaired']
    assert not (tmp_path/'workflow/structure-repair-plan.json').exists()
    assert not (tmp_path/'workflow/vlm/analyze_repair').exists()
    folder = next((tmp_path/'workflow/vlm/analyze').iterdir())
    assert read(folder/'normalization-0.json')['changed'] == (structure.normalize(wire) != wire)
    assert read(folder/'accepted.json')['answer']['layer_order'] == expected[1:]
    assert json.loads(read(folder/'response-0.json')['content']) == wire


@pytest.mark.parametrize('frame_first', [False, True])
@pytest.mark.parametrize('action', ['keep', 'backing'])
def test_frame_screening_preserves_received_order(tmp_path, monkeypatch, frame_first, action):
    import asset_gate
    import reveal_assets  # Import its publish alias before mocking asset_gate.
    import render
    import scene
    from common import sha
    from screening import screen

    value = inventory()
    if frame_first:
        value['layer_order'] = ['bg', 'frame', 'photo', 'title']
    analysis = structure.compile_inventory(value, [1000, 1000], 'normalized_1000')['analysis']
    expected = list(analysis['layer_order'])
    save(tmp_path/'analysis.json', analysis)
    (tmp_path/'candidate.png').write_bytes(b'candidate pixels are not rendered in this test')
    candidate = {'file': str(tmp_path/'candidate.png'), 'sha256': sha(tmp_path/'candidate.png')}
    save(tmp_path/'gate.json', {'records': [{'id': 'frame', 'candidate': candidate, 'input_key': 'test'}]})
    current = {**deepcopy(analysis), 'asset_gate': {'file': str(tmp_path/'gate.json'),
                                                'sha256': sha(tmp_path/'gate.json')}}
    save(tmp_path/'scene.json', current)
    (tmp_path/'final.png').write_bytes(b'prior render')
    save(tmp_path/'result.json', {'render_id': 'test', 'scene_sha256': sha(tmp_path/'scene.json'),
                                 'final_sha256': sha(tmp_path/'final.png')})
    save(tmp_path/'decision.json', {'render_id': 'test', 'decisions': [
        {'id': 'frame', 'action': action, 'reason': 'Usable extracted frame', 'photo_content': 'absent'}]})
    monkeypatch.setattr(scene, 'load_scene', lambda run: deepcopy(current))
    monkeypatch.setattr(asset_gate, 'publish', lambda *args: None)
    monkeypatch.setattr(render, 'render', lambda run: None)
    screen(tmp_path, tmp_path/'decision.json')
    assert read(tmp_path/'scene.json')['layer_order'] == expected


def test_patch_preserves_unmodified_objects_and_rejects_new_ids():
    source = inventory(); source['objects'][3].pop('text')
    plan = structure.repair_plan(source, [1000, 1000], 'normalized_1000')
    result = structure.merge_repair(source, {'updates': {'title': {'text': 'Changed'}}}, plan)
    assert result['objects'][:3] == source['objects'][:3]
    assert 'text' not in source['objects'][3]
    from jsonschema import ValidationError
    with pytest.raises(ValidationError): structure.merge_repair(source, {'updates': {'unknown': {'text': 'Changed'}}}, plan)


def test_photo_placement_coordinates_and_engine_windows(tmp_path):
    from photo_windows import defaults
    current = structure.compile_inventory(inventory(), [1200, 1600], 'normalized_1000')
    save(tmp_path/'input.json', {'reference_size': [1200, 1600]})
    answer = defaults(current['analysis'])
    answer['photos'][0]['placement'] = {'bbox': [260, 350, 820, 1080], 'rotation': 12}
    answer['frame_fallbacks'] = [{'frame_id': 'frame', 'fill': '#EEEEDD', 'radius': .04}]
    value, plan, _ = window_geometry.compile_response(answer, current, tmp_path)
    assert value['analysis']['objects'][1]['rotation'] == 12
    assert value['analysis']['objects'][2]['method'] == 'extract'
    assert 'rotation' not in value['analysis']['objects'][2]
    assert 'placement' not in plan['photos'][0] and 'frame_fallbacks' not in plan
    Draft202012Validator(contracts.PHOTO_WINDOWS).validate(plan)
    adapter = ReferenceCoordinates({'reference_size': [1200, 1600]}, 'normalized_1000')
    wire = deepcopy(answer); wire['coordinate_space'] = 'normalized_1000'
    wire['photos'][0]['placement']['bbox'] = [200, 200, 700, 700]
    Draft202012Validator(adapter.schema(window_geometry.schema())).validate(wire)
    assert adapter.decode(wire)['photos'][0]['placement']['bbox'] == [240, 320, 840, 1120]
    assert current['analysis']['objects'][1]['bbox'] == [240, 320, 840, 1120]


def test_client_normalizes_before_schema_validation_without_extra_request(tmp_path):
    value = current_inventory(inventory()); value['objects'][0]['parent_id'] = None
    config = Config(analysis_model=None, coordinate_mode='normalized_1000', max_requests=1, response_format='json_object')
    import json
    calls = []
    def transport(payload):
        calls.append(payload)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}
    client = Client(tmp_path/'vlm', config, transport=transport)
    result = client.ask('analyze', prompts.render('analyze', mode='normalized_1000'), {}, [], structure.schema([1000, 1000], 'normalized_1000'),
                        single_request=True, normalize_answer=structure.normalize)
    assert len(calls) == 1 and 'parent_id' not in result['objects'][0]
    assert json.loads(next((tmp_path/'vlm/analyze').glob('*/response-0.json')).read_text())['content'] == json.dumps(value)


@pytest.mark.parametrize('failure', ['missing_text', 'missing_description', 'empty_response', 'twice_invalid'])
def test_live_analyzer_repairs_within_two_request_budget(tmp_path, failure):
    import json
    original = inventory(); original['objects'][3].pop('text')
    replies = ([original, {'updates': {'title': {'text': 'Hello'}}}]
               if failure == 'missing_text' else
               [[], inventory()] if failure == 'empty_response' else [[], []])
    if failure == 'missing_description':
        original = inventory(); original['objects'][1].pop('description')
        replies = [original, {'updates': {'photo': {'description': 'portrait'}}}]
    calls = []
    def transport(payload):
        calls.append(payload)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(current_inventory(replies[len(calls)-1]))}}]}
    config = Config(analysis_model=None, coordinate_mode='normalized_1000', max_requests=2, response_format='json_object')
    client = Client(tmp_path/'workflow/vlm', config, transport=transport)
    save(tmp_path/'input.json', {'reference_size': [1200, 1600]})
    written = []
    runner = SimpleNamespace(run=tmp_path, control=tmp_path/'workflow', config=config, client=client,
                             reference_context=lambda: {'reference_size': [1200, 1600]},
                             analysis_images=lambda: [], candidates=client.candidates, write_reference=written.append)
    if failure == 'twice_invalid':
        runner.progress = lambda *args: None
        with pytest.raises(ModelError): structure.execute(runner)
        assert not written and not (tmp_path/'structure.json').exists()
    else:
        runner.progress = lambda *args: None
        structure.execute(runner)
        result = read(tmp_path/'workflow/structure-analysis.json')
        assert result['repaired'] and not result['first_pass_valid']
        assert result['repair_kind'] == ('field_patch' if failure in {'missing_text', 'missing_description'} else 'full_retry')
        assert written[0] == structure.compile_inventory(current_inventory(inventory()), [1200, 1600], 'normalized_1000', tmp_path)
    assert len(calls) == client.usage()['requests'] == 2


@pytest.mark.parametrize('fault', ['moved_away', 'duplicate', 'missing', 'unknown_frame'])
def test_window_stage_rejects_misassigned_geometry(fault):
    from photo_windows import defaults
    current = structure.compile_inventory(inventory(), [1000, 1000], 'normalized_1000')
    answer = defaults(current['analysis'])
    answer['photos'][0]['placement'] = {'bbox': [200, 200, 700, 700], 'rotation': 10}
    if fault == 'moved_away': answer['photos'][0]['placement']['bbox'] = [800, 800, 950, 950]
    if fault == 'duplicate': answer['photos'].append(deepcopy(answer['photos'][0]))
    if fault == 'missing': answer['photos'] = []
    if fault == 'unknown_frame': answer['frame_fallbacks'] = [{'frame_id': 'photo', 'fill': '#FFFFFF'}]
    with pytest.raises(ValueError): window_geometry.compile_response(answer, current)


def test_text_content_is_required_but_description_is_derived():
    value = inventory(); value['objects'][3].pop('description')
    Draft202012Validator(structure.legacy_schema([1000, 1000], 'normalized_1000')).validate(value)
    compiled = structure.compile_inventory(value, [1000, 1000], 'normalized_1000')
    assert compiled['analysis']['objects'][3]['description'] == 'Hello'
    assert 'description' not in value['objects'][3]
    value['objects'][3].pop('text')
    assert not Draft202012Validator(structure.legacy_schema([1000, 1000], 'normalized_1000')).is_valid(value)
    assert 'text' not in structure.normalize(value)['objects'][3]
    with pytest.raises(ValueError): structure.compile_inventory(value, [1000, 1000], 'normalized_1000')


@pytest.mark.parametrize('patch', [
    {'updates': {'title': {'text': 'OK', 'bbox': [0, 0, 1000, 1000]}}},
    {'updates': {'title': {'text': 'OK', 'kind': 'overlay'}}},
    {'updates': {'title': {'text': 'OK'}}, 'order': ['bg', 'photo', 'frame']},
    {'updates': {'title': {'text': 'OK'}}, 'layer_order': ['bg', 'frame', 'photo', 'title']},
    {'updates': {'title': {'text': 'OK'}}, 'move_after': {'frame': 'photo'}},
    {'replacements': []}])
def test_text_repair_cannot_change_other_fields_or_order(patch):
    from jsonschema import ValidationError
    value = inventory(); value['objects'][3].pop('text')
    plan = structure.repair_plan(value, [1000, 1000], 'normalized_1000')
    assert plan['fields'] == {'title': ['text']} and 'move_after' not in plan['schema']['properties']
    with pytest.raises(ValidationError): structure.merge_repair(value, patch, plan)


def test_missing_text_repair_never_asks_the_model_to_fix_frame_order():
    value = inventory(); value['objects'][1:3] = value['objects'][1:3][::-1]
    value['objects'][3].pop('text')
    value['layer_order'] = ['bg', 'frame', 'frame', 'title', 'unknown']
    plan = structure.repair_plan(value, [1000, 1000], 'normalized_1000')
    assert plan['fields'] == {'title': ['text']}
    assert set(plan['schema']['properties']) == {'updates'}
    result = structure.merge_repair(value, {'updates': {'title': {'text': 'Hello'}}}, plan)
    expected = structure.normalize(value)
    expected['objects'][3]['text'] = 'Hello'
    assert result['layer_order'] == ['bg', 'frame', 'photo', 'title']
    assert result == expected
    structure.compile_inventory(result, [1000, 1000], 'normalized_1000')


@pytest.mark.parametrize('wrapper', ['envelope', 'nested_objects', 'both'])
def test_only_redundant_containers_are_normalized(wrapper):
    value = inventory()
    if wrapper in {'nested_objects', 'both'}: value = {**value, 'objects': [value['objects']]}
    if wrapper in {'envelope', 'both'}: value = [value]
    assert structure.normalize(value) == inventory()
    assert structure.normalize([inventory(), inventory()]) == [inventory(), inventory()]
    assert structure.normalize([]) == []
    assert structure.normalize({'objects': []}) == {'objects': []}


def test_boxes_wrapper_is_recovered_but_business_content_is_unchanged(tmp_path):
    from collage.vlm.response_normalization import normalize_boxes_answer
    from collage.workflow.support import box_geometry as box_refinement
    import json
    row = {'id': 'photo', 'status': 'keep', 'reason': 'already covered',
           'edges': {e: {'action': 'keep', 'percent': 0} for e in ('left', 'top', 'right', 'bottom')}}
    value = {'movement_unit': 'original_bbox_percent', 'objects': [row]}
    def transport(payload):
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps([value])}}]}
    client = Client(tmp_path/'vlm', Config(analysis_model=None, max_requests=1, response_format='json_object'), transport=transport)
    answer = client.ask('boxes', 'Check', {}, [], box_refinement.schema(['photo']),
                        single_request=True, normalize_answer=normalize_boxes_answer)
    assert answer == value and client.usage()['requests'] == 1
    metadata = read(next((tmp_path/'vlm/boxes').glob('*/normalization-0.json')))
    assert metadata['changed'] and not metadata['raw_schema_valid']
    assert normalize_boxes_answer([value, value]) == [value, value]


def test_conditional_inventory_contract_can_be_projected_for_strict_provider():
    from collage.vlm.gpt_schema import project
    projected = project(structure.schema([1000, 1000], 'normalized_1000'))
    Draft202012Validator.check_schema(projected)


def test_photo_description_survives_compilation_without_effect_classification():
    from collage.vlm.gpt_schema import project, restore
    value = inventory()
    description = 'Environment remains visible; blue outline follows the whole subject; soft bottom edge.'
    value['objects'][1]['description'] = description
    schema = structure.legacy_schema([1000, 1000], 'normalized_1000')
    fields = schema['properties']['objects']['items']['properties']
    strict_value = {**value, 'objects': [{k: row.get(k) for k in fields} for row in value['objects']]}
    Draft202012Validator(project(schema)).validate(strict_value)
    restored = structure.normalize(restore(strict_value, schema))
    result = structure.compile_inventory(restored, [1000, 1000], 'normalized_1000')
    assert result['analysis']['objects'][1]['description'] == description
    assert all('mode' not in o and 'effects' not in o for o in result['analysis']['objects'])
    assert 'style' not in result['analysis']['objects'][1]


@pytest.mark.parametrize('field,value', [('mode', 'cover'), ('mode', None), ('effects', {'stroke': True})])
def test_analysis_does_not_accept_or_repair_effect_classification(field, value):
    source = inventory(); source['objects'][1][field] = value
    with pytest.raises(ValueError, match='Additional properties'):
        structure.compile_inventory(source, [1000, 1000], 'normalized_1000')
    assert structure.repair_plan(source, [1000, 1000], 'normalized_1000') is None


@pytest.mark.parametrize('frame_first', [False, True])
def test_description_only_analysis_is_accepted_in_one_request(tmp_path, frame_first):
    import json
    original = inventory()
    if frame_first:
        original['layer_order'] = ['bg', 'frame', 'photo', 'title']
    config = Config(coordinate_mode='normalized_1000', max_requests=1)
    client = Client(tmp_path/'workflow/vlm', config, transport=lambda payload: {
        'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(current_inventory(original))}}]})
    save(tmp_path/'input.json', {'reference_size': [1000, 1000]})
    written = []
    runner = SimpleNamespace(run=tmp_path, control=tmp_path/'workflow', config=config, client=client,
        progress=lambda *args: None, reference_context=lambda: {'reference_size': [1000, 1000]},
        analysis_images=lambda: [], candidates=client.candidates, write_reference=written.append)
    structure.execute(runner)
    assert client.usage()['requests'] == 1
    assert read(tmp_path/'workflow/structure-analysis.json')['first_pass_valid']
    assert not (tmp_path/'workflow/structure-repair-plan.json').exists()
    assert 'mode' not in read(tmp_path/'structure.json')['objects'][1]
    assert 'mode' not in written[0]['analysis']['objects'][1]
    assert written[0]['analysis']['layer_order'] == original['layer_order']
    assert read(tmp_path/'structure.json')['objects'] == current_inventory(original)['objects']
    folder = next((tmp_path/'workflow/vlm/analyze').iterdir())
    assert not read(folder/'normalization-0.json')['changed']
    assert json.loads(read(folder/'response-0.json')['content']) == current_inventory(original)


@pytest.mark.parametrize('preserve,stroke,fade', [
    (True, True, 0), (False, True, 0), (True, True, .1), (False, False, 0), (True, False, 0),
])
def test_window_effect_combinations_do_not_add_categories_to_analysis(preserve, stroke, fade):
    from photo_windows import defaults, configure, needs_subject_mask
    from window_contract import empty_stroke
    current = structure.compile_inventory(inventory(), [1000, 1000], 'normalized_1000')
    original = deepcopy(current)
    answer = defaults(current['analysis']); spec = answer['photos'][0]
    spec.update(placement={'bbox': [200, 200, 700, 700], 'rotation': 0},
                reason='The reference shows the stated background, outline and edge treatment.')
    spec['subject']['preserve_background'] = preserve
    spec['subject']['stroke'].update(enabled=stroke, width=.02 if stroke else 0)
    if fade:
        spec['mask'] = {'enabled': True, 'shape': {'type': 'rectangle'}, 'stroke': empty_stroke()}
    spec['edge_gradient'].update(enabled=bool(fade), bottom=fade)
    compiled, plan, _ = window_geometry.compile_response(answer, current)
    assert current == original and 'mode' not in compiled['analysis']['objects'][1]
    review = window_geometry.drawing_review(plan, assessed=True)[0]
    assert 'initial_mode' not in review and 'applied_mode' not in review
    assert review['status'] == 'assessed'
    assert review['subject']['preserve_background'] == preserve
    assert review['subject']['stroke']['enabled'] == stroke
    assert review['edge_gradient']['enabled'] == bool(fade)
    photo = {**compiled['analysis']['objects'][1], 'style': {}}
    configure(photo, plan['photos'][0])
    assert photo['mode'] == ('cover' if preserve else 'cutout')
    assert needs_subject_mask(photo) == (not preserve or stroke)


def test_window_failure_records_unresolved_effects_without_guessing_parameters(tmp_path):
    import json
    current = structure.compile_inventory(inventory(), [1000, 1000], 'normalized_1000')
    save(tmp_path/'analysis.json', current['analysis'])
    save(tmp_path/'text-units.json', current['text_units'])
    save(tmp_path/'input.json', {'reference_size': [1000, 1000]})
    (tmp_path/'prepared').mkdir()
    Image.new('RGB', (1000, 1000), 'white').save(tmp_path/'prepared/reference.png')
    calls, warnings = [], []
    def transport(payload):
        calls.append(payload)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}
    config = Config(coordinate_mode='pixel', response_format='json_object', max_requests=2)
    client = Client(tmp_path/'workflow/vlm', config, transport=transport)
    def unexpected_write(value):
        pytest.fail('A failed window judgment must not rewrite the original analysis')
    runner = SimpleNamespace(run=tmp_path, control=tmp_path/'workflow', client=client,
                             reference_context=lambda: {'reference_size': [1000, 1000]},
                             analysis_images=lambda: [], write_reference=unexpected_write,
                             warn=lambda *args: warnings.append(args))
    window_geometry.execute(runner)
    assert len(calls) == client.usage()['requests'] == 2
    context = json.loads(calls[0]['messages'][1]['content'][0]['text'].split('\n', 1)[1])
    assert {o['kind'] for o in context['analysis']['objects']} <= {'photo', 'frame'}
    assert all('mode' not in o for o in context['analysis']['objects'])
    assert read(tmp_path/'analysis.json') == current['analysis']
    evidence = read(tmp_path/'workflow/window-geometry.json')
    assert evidence['status'] == 'fallback'
    review = evidence['drawing_review'][0]
    assert review['status'] == 'unresolved' and 'applied_mode' not in review
    spec = read(tmp_path/'photo-windows.json')['photos'][0]
    assert spec['subject']['preserve_background']
    assert spec['mask'] == {'enabled': False} and not spec['edge_gradient']['enabled']
    assert warnings[0][0] == 'window_geometry_unresolved'


@pytest.mark.parametrize('code', ['home_unavailable', 'timeout'])
def test_analysis_does_not_repair_codex_transport_failures(tmp_path, code):
    from collage.vlm.client import ModelTransportError
    from collage.vlm.codex.transport import CodexError, ERRORS
    calls = []
    def fail(payload):
        calls.append(payload)
        raise CodexError(ERRORS[code])
    config = Config(coordinate_mode='normalized_1000')
    client = Client(tmp_path/'workflow/vlm', config, transport=fail)
    runner = SimpleNamespace(run=tmp_path, control=tmp_path/'workflow', config=config, client=client,
        progress=lambda *args: None, reference_context=lambda: {'reference_size': [1000, 1000]},
        analysis_images=lambda: [], candidates=client.candidates,
        write_reference=lambda value: pytest.fail('No model output to write'))
    with pytest.raises(ModelTransportError):
        structure.execute(runner)
    assert len(calls) == client.usage()['requests'] == 1
    assert not (tmp_path/'workflow/vlm/analyze_repair').exists()
    evidence = read(tmp_path/'workflow/structure-analysis.json')
    assert evidence['status'] == 'transport_failed'
    assert evidence['repaired'] is False


def test_window_parameters_require_nonempty_evidence():
    from photo_windows import defaults
    current = structure.compile_inventory(inventory(), [1000, 1000], 'normalized_1000')
    answer = defaults(current['analysis'])
    answer['photos'][0].update(placement={'bbox': [200, 200, 700, 700], 'rotation': 0}, reason='   ')
    with pytest.raises(ValueError, match='reason'):
        window_geometry.compile_response(answer, current)


def test_window_success_persists_subject_parameters_for_binding(tmp_path):
    from photo_windows import defaults
    from collage.workflow.stages.bind import context_for
    current = structure.compile_inventory(inventory(), [1000, 1000], 'normalized_1000')
    save(tmp_path/'analysis.json', current['analysis'])
    save(tmp_path/'text-units.json', current['text_units'])
    save(tmp_path/'input.json', {'reference_size': [1000, 1000]})
    (tmp_path/'prepared').mkdir()
    Image.new('RGB', (1000, 1000), 'white').save(tmp_path/'prepared/reference.png')
    answer = defaults(current['analysis']); spec = answer['photos'][0]
    spec.update(placement={'bbox': [200, 200, 700, 700], 'rotation': 0},
                reason='A blue outline follows the subject; the photographic background remains visible.')
    spec['subject']['stroke'].update(enabled=True, width=.02, color='#99DDFF')
    def ask(stage, prompt, context, images, schema, validate):
        Draft202012Validator(schema).validate(answer)
        validate(answer)
        return answer
    def write_reference(value):
        save(tmp_path/'analysis.json', value['analysis'])
    runner = SimpleNamespace(run=tmp_path, control=tmp_path/'workflow',
                             client=SimpleNamespace(ask=ask),
                             reference_context=lambda: {'reference_size': [1000, 1000]},
                             analysis_images=lambda: [], write_reference=write_reference,
                             warn=lambda *args: pytest.fail(str(args)))
    window_geometry.execute(runner)
    evidence = read(tmp_path/'workflow/window-geometry.json')
    assert evidence['status'] == 'accepted'
    review = evidence['drawing_review'][0]
    assert review['subject']['preserve_background'] and review['subject']['stroke']['enabled']
    assert review['status'] == 'assessed' and 'applied_mode' not in review
    assert review['reason'] == spec['reason']
    analysis = read(tmp_path/'analysis.json'); windows = read(tmp_path/'photo-windows.json')
    context = context_for({'reference_size': [1000, 1000], 'catalog': []}, analysis, windows)
    slot = context['photo_slots'][0]
    assert 'mode' not in slot and 'photo_windows' not in context
    assert 'subject' not in slot
    assert slot['requires_segmentable_subject'] is True
