from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator, ValidationError

from collage.workflow.stages import analyze
from frame_fallback import align_frames
from reveal_plan import plan
from scene import remove_objects


def inventory():
    return {'background': {'type': 'solid_color', 'color': '#FFFFFF'},
            'objects': [
                {'id': 'p1', 'kind': 'photo', 'bbox': [100, 100, 400, 600],
                 'description': 'left portrait'},
                {'id': 'p2', 'kind': 'photo', 'bbox': [600, 100, 900, 600],
                 'description': 'right portrait'},
                {'id': 'f', 'kind': 'frame', 'bbox': [50, 50, 950, 700],
                 'description': 'one paper frame with two separate windows',
                 'photo_id': ['p1', 'p2']}],
            'layer_order': ['p1', 'p2', 'f']}


def test_multi_window_compiles_and_requests_both_photos():
    source = inventory()
    Draft202012Validator(analyze.schema([1000, 1000], 'pixel')).validate(source)
    scene = analyze.compile_inventory(source, [1000, 1000], 'pixel')['analysis']
    frame = scene['objects'][-1]
    assert frame['photo_id'] == ['p1', 'p2']
    before = deepcopy(frame)
    align_frames(scene)
    assert frame == before
    result = plan(scene, [frame])
    assert result['status'] == 'ready'
    contexts = [o['source_id'] for batch in result['batches'] for o in batch['objects']
                if o['role'] == 'photo_context']
    assert sorted(contexts) == ['p1', 'p2']
    remove_objects(scene, ['p1'])
    assert frame['photo_id'] == ['p2']
    with pytest.raises(ValueError, match='linked frame'):
        remove_objects(scene, ['p2'])


@pytest.mark.parametrize('field,value,index', [
    ('parent_id', 'f', 0), ('frame_id', 'f', 0),
    ('photo_id', 'p1', 2), ('photo_id', [], 2),
    ('photo_id', ['p1', 'p1'], 2), ('photo_id', ['p1'], 0),
])
def test_new_wire_contract_rejects_obsolete_or_invalid_links(field, value, index):
    source = inventory()
    source['objects'][index][field] = value
    with pytest.raises(ValidationError):
        Draft202012Validator(analyze.schema([1000, 1000], 'pixel')).validate(source)


@pytest.mark.parametrize('target', ['missing', 'f'])
def test_photo_list_requires_existing_photos(target):
    source = inventory()
    source['objects'][-1]['photo_id'] = ['p1', target]
    with pytest.raises(ValueError, match='existing photo'):
        analyze.compile_inventory(source, [1000, 1000], 'pixel')


def test_photo_cannot_belong_to_two_frames():
    source = inventory()
    source['objects'].append({**source['objects'][-1], 'id': 'f2', 'photo_id': ['p1']})
    source['layer_order'].append('f2')
    with pytest.raises(ValueError, match='multiple frames'):
        analyze.compile_inventory(source, [1000, 1000], 'pixel')


def test_single_window_uses_single_item_list():
    source = inventory()
    source['objects'][-1]['photo_id'] = ['p1']
    Draft202012Validator(analyze.schema([1000, 1000], 'pixel')).validate(source)
    scene = analyze.compile_inventory(source, [1000, 1000], 'pixel')['analysis']
    assert scene['objects'][-1]['photo_id'] == ['p1']


def test_editor_removes_frame_only_after_all_windows_are_removed():
    from editor_scene import apply_edits
    scene = analyze.compile_inventory(inventory(), [1000, 1000], 'pixel')['analysis']
    once = apply_edits(scene, [{'id': 'p1', 'remove': True}])
    assert once['objects'][-1]['photo_id'] == ['p2']
    twice = apply_edits(once, [{'id': 'p2', 'remove': True}])
    together = apply_edits(scene, [{'id': 'p1', 'remove': True}, {'id': 'p2', 'remove': True}])
    assert [o['kind'] for o in twice['objects']] == ['background']
    assert together['objects'] == twice['objects']


def test_decomposition_remaps_photo_list_without_changing_source():
    from collage.decomposition.pipeline import extraction_inventory
    source = inventory()
    source['objects'][0]['id'] = 'layer_0'
    source['objects'][-1]['photo_id'][0] = 'layer_0'
    source['layer_order'][0] = 'layer_0'
    scene = analyze.compile_inventory(source, [1000, 1000], 'pixel')['analysis']
    original = deepcopy(scene)
    result, mapping = extraction_inventory(scene)
    assert result['objects'][-1]['photo_id'] == [mapping['layer_0'], 'p2']
    assert scene == original
