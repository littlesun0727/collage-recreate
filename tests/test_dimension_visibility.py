"""Check actual wire messages while canonical coordinates remain in pixels."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from collage import contracts
from collage import prompts
from collage.workflow.stages import analyze as structure
from collage.workflow.stages import photo_windows as window_geometry
from collage.vlm.client import Client
from collage.config import Config
from collage.vlm.coordinates import ReferenceCoordinates
from collage.workflow.pipeline import Workflow
from collage.storage import read, save, file_sha
from photo_windows import defaults


SIZE = [960, 1280]


def prepared(tmp_path):
    path = tmp_path/'prepared/reference.png'
    path.parent.mkdir(parents=True)
    Image.new('RGB', tuple(SIZE), 'white').save(path)
    save(tmp_path/'input.json', {'reference_size': SIZE})
    return path


def inventory(mode):
    return {'objects': [
        {'id': 'photo', 'kind': 'photo', 'description': 'portrait',
         'bbox': [0, 0, 1000, 1000] if mode == 'normalized_1000' else [0, 0, *SIZE]},
        {'id': 'title', 'kind': 'text', 'bbox': [10, 10, 100, 100], 'text': 'Hello'}]}


def response(answer):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]}


def wire_context(payload):
    return json.loads(payload['messages'][1]['content'][0]['text'].split('\n', 1)[1])


def captions(payload):
    return [p['text'] for p in payload['messages'][1]['content'][1:] if p['type'] == 'text']


@pytest.mark.parametrize('mode', ['pixel', 'normalized_1000'])
@pytest.mark.parametrize('repair', ['field_patch', 'full_retry'])
def test_inventory_and_both_repair_paths_follow_dimension_policy(tmp_path, mode, repair):
    path = prepared(tmp_path)
    valid = inventory(mode); missing = deepcopy(valid); missing['objects'][1].pop('text')
    replies = iter([missing, {'updates': {'title': {'text': 'Hello'}}}] if repair == 'field_patch' else [[], valid])
    calls = []
    def transport(payload):
        calls.append(payload)
        return response(next(replies))
    config = Config(coordinate_mode=mode, response_format='json_object', max_requests=2)
    client = Client(tmp_path/'workflow/vlm', config, transport=transport)
    written = []
    runner = SimpleNamespace(run=tmp_path, control=tmp_path/'workflow', config=config, client=client,
        reference_context=lambda: {'reference_size': SIZE}, analysis_images=lambda: [('reference', path)],
        candidates=client.candidates, write_reference=written.append)
    runner.progress = lambda *args: None
    structure.execute(runner)
    assert len(calls) == 2
    for payload in calls:
        context = wire_context(payload)
        if mode == 'normalized_1000':
            assert 'reference_size' not in context
            assert context['reference_aspect_ratio'] == .75
            assert captions(payload) == ['reference']
            assert 'reference_size' not in payload['messages'][0]['content']
        else:
            assert context['reference_size'] == SIZE
            assert '960×1280' in captions(payload)[0]
    assert written[0]['analysis']['reference_size'] == SIZE
    assert written[0]['analysis']['objects'][0]['bbox'] == [0, 0, *SIZE]
    evidence = read(tmp_path/'workflow/structure-analysis.json')
    assert evidence['repair_kind'] == repair
    for path in (tmp_path/'workflow/vlm').glob('*/*/input.json'):
        assert read(path)['images'][0]['size'] == SIZE  # Local evidence retains dimensions.


@pytest.mark.parametrize('mode', ['pixel', 'normalized_1000'])
def test_window_transport_hides_nested_sizes_and_keeps_real_geometry(tmp_path, mode):
    path = prepared(tmp_path)
    current = structure.compile_inventory(inventory('normalized_1000'), SIZE, 'normalized_1000')
    current['analysis']['objects'][0]['bbox'] = [96, 256, 864, 1152]
    save(tmp_path/'analysis.json', current['analysis']); save(tmp_path/'text-units.json', current['text_units'])
    answer = defaults(current['analysis']); answer['coordinate_space'] = mode
    box = [125, 250, 875, 875] if mode == 'normalized_1000' else [120, 320, 840, 1120]
    answer['photos'][0]['placement'] = {'bbox': box, 'rotation': 0}
    calls = []
    def transport(payload):
        calls.append(payload); return response(answer)
    client = Client(tmp_path/'workflow/vlm', Config(coordinate_mode=mode, response_format='json_object'), transport=transport)
    written = []
    runner = SimpleNamespace(run=tmp_path, control=tmp_path/'workflow', client=client,
        reference_context=lambda: {'reference_size': SIZE}, analysis_images=lambda: [('reference', path)],
        write_reference=written.append, warn=lambda *args: pytest.fail(str(args)))
    window_geometry.execute(runner)
    context = wire_context(calls[0]); slot = context['photo_slots'][0]
    assert slot['aspect_ratio'] == round(768/896, 6)
    if mode == 'normalized_1000':
        assert 'reference_size' not in context and 'reference_size' not in context['analysis']
        assert 'width' not in slot and 'height' not in slot
        assert context['analysis']['objects'][0]['bbox'] == [100, 200, 900, 900]
        assert all('实际像素尺寸' not in label and '原图区域' not in label for label in captions(calls[0]))
        assert 'reference_size' not in calls[0]['messages'][0]['content']
    else:
        assert context['reference_size'] == SIZE
        assert 'reference_size' not in context['analysis']
        assert (slot['width'], slot['height']) == (768, 896)
        assert '960×1280' in captions(calls[0])[0]
    assert written[0]['analysis']['reference_size'] == SIZE
    assert written[0]['analysis']['objects'][0]['bbox'] == [120, 320, 840, 1120]
    assert read(next((tmp_path/'workflow/vlm/photo_windows').glob('*/coordinates.json')))['reference_size'] == SIZE


@pytest.mark.parametrize('mode', ['pixel', 'normalized_1000'])
def test_legacy_analysis_wire_schema_omits_size_only_in_normalized_mode(tmp_path, mode):
    prepared(tmp_path)
    current = structure.compile_inventory(inventory('normalized_1000'), SIZE, 'normalized_1000')
    context = {'reference_size': SIZE, **current}; original = deepcopy(context)
    adapter = ReferenceCoordinates(context, mode)
    raw = {**{k: adapter.context[k] for k in ('analysis', 'text_units')}, 'coordinate_space': mode}
    raw['analysis']['objects'][0]['bbox'] = [125, 250, 875, 875] if mode == 'normalized_1000' else [120, 320, 840, 1120]
    calls = []
    def transport(payload):
        calls.append(payload); return response(raw)
    client = Client(tmp_path/'vlm', Config(coordinate_mode=mode, response_format='json_schema'), transport=transport)
    decoded = client.ask('legacy_reference', '{{REFERENCE_COORDINATES}}', context, [], contracts.REFERENCE)
    fields = calls[0]['response_format']['json_schema']['schema']['properties']['analysis']
    assert ('reference_size' in fields['properties']) == (mode == 'pixel')
    assert ('reference_size' in fields['required']) == (mode == 'pixel')
    assert decoded['analysis']['reference_size'] == SIZE
    assert decoded['analysis']['objects'][0]['bbox'] == [120, 320, 840, 1120]
    assert context == original


@pytest.mark.parametrize('mode', ['pixel', 'normalized_1000'])
def test_binding_material_labels_and_catalog_use_the_same_policy(tmp_path, mode):
    path = prepared(tmp_path)
    save(tmp_path/'prepared/catalog.json', {'assets': [{'id': 'asset_a', 'file': str(path), 'size': SIZE, 'sha256': file_sha(path)}], 'contact_sheets': []})
    config = Config(coordinate_mode=mode, response_format='json_object')
    runner = Workflow(tmp_path, config=config, reveal=False)
    images = runner.material_images()
    calls = []
    def transport(payload):
        calls.append(payload); return response({'photos': []})
    client = Client(tmp_path/'vlm', config, transport=transport)
    context = {'reference_size': SIZE, 'photo_slots': [], 'catalog': [{'id': 'asset_a', 'size': SIZE}]}
    client.ask('bind', prompts.render('bind'), context, images, contracts.obj({'photos': {'type': 'array'}}), reference_input_only=True)
    wire = wire_context(calls[0])
    if mode == 'normalized_1000':
        assert wire['catalog'] == [{'id': 'asset_a', 'aspect_ratio': .75}]
        assert captions(calls[0]) == ['客户素材 asset_a']
    else:
        assert wire['catalog'] == context['catalog']
        assert str(SIZE) in captions(calls[0])[0]
    assert context['catalog'][0]['size'] == SIZE


def test_caption_policy_separates_cache_and_pixel_scene_requests(tmp_path):
    path = prepared(tmp_path); calls = []
    def transport(payload):
        calls.append(payload); return response({'ok': True})
    # A normalized model setting must not change the editor's explicit pixel contract.
    client = Client(tmp_path/'vlm', Config(coordinate_mode='normalized_1000', response_format='json_object'), transport=transport)
    schema = contracts.obj({'ok': {'type': 'boolean'}})
    for mode in (None, 'normalized_1000', 'normalized_1000'):
        client.ask('caption_check', 'Inspect.', {}, [('reference', path)], schema, reference_mode=mode)
    assert len(calls) == 2
    assert '960×1280' in captions(calls[0])[0] and captions(calls[1]) == ['reference']
