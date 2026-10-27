"""The screen switch controls actual extracted pixels, not just VLM calls."""
from copy import deepcopy

import pytest
from PIL import Image, ImageDraw

from collage.storage import read, save
from collage.workflow.stages.analyze import compile_inventory
from common import sha
from prepare import prepare
from scene import compile_scene, load_scene
from render import render


@pytest.mark.parametrize('enabled', [None, False, True])
@pytest.mark.parametrize('opaque', [False, True])
def test_build_renders_extracted_frame_when_screen_disabled(tmp_path, monkeypatch, enabled, opaque):
    reference = tmp_path/'reference.png'
    customers = tmp_path/'customers'
    customers.mkdir()
    customer = customers/'customer.png'
    Image.new('RGB', (200, 200), 'white').save(reference)
    Image.new('RGB', (200, 200), 'red').save(customer)
    run = tmp_path/'task'
    prepare(reference, [customers], run, width=200)
    bundle = compile_inventory({'objects': [
        {'id': 'bg', 'kind': 'background', 'background_type': 'solid_color',
         'bbox': [0, 0, 200, 200], 'description': 'White canvas', 'color': '#FFFFFF'},
        {'id': 'photo_1', 'kind': 'photo', 'bbox': [40, 40, 160, 160], 'description': 'Customer photo'},
        {'id': 'frame_1', 'kind': 'frame', 'bbox': [20, 20, 180, 180],
         'photo_id': 'photo_1', 'description': 'Blue drawn frame with interior decoration'},
    ]}, [200, 200], 'pixel')
    save(run/'analysis.json', bundle['analysis'])
    asset = read(run/'prepared/catalog.json')['assets'][0]
    save(run/'bindings.json', {'schema_version': 'collage-bindings-v1',
                              'photos': [{'slot_id': 'photo_1', 'asset_id': asset['id'],
                                          'crop_center': [.5, .5], 'reason': 'Use customer photo'}],
                              'texts': []})
    if enabled is not None:
        save(run/'workflow-options.json', {'screen': enabled})
    candidate = tmp_path/'extracted.png'
    layer = Image.new('RGBA', (200, 200))
    draw = ImageDraw.Draw(layer)
    draw.rectangle((20, 20, 180, 180), outline='blue', width=5)
    draw.rectangle((40, 40, 160, 160) if opaque else (40, 80, 160, 95), fill='blue')
    layer.save(candidate)
    source = {'file': str(candidate), 'sha256': sha(candidate),
              'bbox': [20, 20, 180, 180], 'original_bbox': [20, 20, 180, 180]}
    monkeypatch.setattr('reveal_assets.acquire', lambda *args: ({'frame_1': source}, []))

    compile_scene(run, {'enabled': True, 'remote': False})
    render(run)
    scene = load_scene(run)
    gate = read(run/'assets/gate/report.json')['records'][0]
    frame = next(o for o in scene['objects'] if o['id'] == 'frame_1')
    resource = next(r for r in read(run/'result.json')['resources'] if r['id'] == 'frame_1')
    assert scene['layer_order'] == ['bg', 'photo_1', 'frame_1']
    assert scene['workflow_options']['screen'] is bool(enabled)
    with Image.open(run/'final.png') as final:
        pixel = final.getpixel((100, 90))[:3]
    if enabled:
        assert gate['status'] == 'pending'
        assert 'recovered' not in frame
        assert resource['metadata']['method'] == 'local_frame'
        assert pixel != (0, 0, 255)
    else:
        assert gate['status'] == 'accepted' and not gate['issues']
        assert gate['warnings'] and not gate['semantic_verified']
        assert sha(frame['recovered']['file']) == sha(candidate)
        assert resource['metadata']['method'] == 'reveal'
        assert not resource['metadata'].get('fallback')
        assert pixel == (0, 0, 255)


@pytest.mark.parametrize('problem', ['empty', 'repair_failed', 'explicitly_omitted'])
def test_disabled_screen_still_handles_unavailable_or_explicitly_removed_layers(problem):
    from asset_gate import inspect
    image = Image.new('RGBA', (20, 20), None if problem == 'empty' else 'blue')
    target = {'id': 'frame', 'kind': 'frame', 'bbox': [0, 0, 20, 20]}
    scene = {'sources': {}, 'workflow_options': {'screen': False}, 'objects': [target]}
    repair = {'omit': [{'id': 'frame'}]} if problem == 'explicitly_omitted' else {}
    errors = ['Failed explicit edit'] if problem == 'repair_failed' else []
    record = inspect(image, target, scene, {}, repair, [], [], errors)
    assert record['status'] == 'rejected'
    assert ('empty_layer' if problem == 'empty' else problem) in record['issues']


def test_gate_decision_identity_includes_screen_policy():
    from asset_gate import inspect
    image = Image.new('RGBA', (20, 20), 'blue')
    target = {'id': 'frame', 'kind': 'frame', 'bbox': [0, 0, 20, 20]}
    scene = {'sources': {}, 'workflow_options': {'screen': False}, 'objects': [target]}
    disabled = inspect(image, target, scene, {}, {}, [], [], [])
    enabled_scene = deepcopy(scene)
    enabled_scene['workflow_options']['screen'] = True
    enabled = inspect(image, target, enabled_scene, {}, {}, [], [], [])
    assert disabled['input_key'] != enabled['input_key']
