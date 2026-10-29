from inventory_examples import current_inventory
"""Real stage orchestration/render/resume with explicit offline model replies."""
from copy import deepcopy
import json

from PIL import Image
import pytest

from collage import prompts
from collage.config import Config
from collage.contracts import obj
from collage.storage import file_sha, read
from collage.vlm.client import Client
from collage.workflow.pipeline import Workflow
from collage.workflow.stages.analyze import compile_inventory
from photo_windows import defaults
from window_contract import empty_stroke
from prepare import prepare


def reply(value):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}


def run_offline_pipeline(folder):
    """Reusable smoke run; the caller decides whether to retain its artifacts."""
    folder.mkdir(parents=True, exist_ok=True)
    reference = folder/'reference.png'
    Image.new('RGB', (400, 300), '#dddddd').save(reference)
    materials = []
    material_dir = folder/'customers'
    material_dir.mkdir()
    for index, color in enumerate(('#bb3322', '#2255bb')):
        path = material_dir/f'customer-{index}.png'
        Image.new('RGB', (200, 300), color).save(path)
        materials.append(path)
    run = folder/'task'
    prepare(reference, [material_dir], run, width=400)
    objects = [{'id': f'photo_{i}', 'kind': 'photo', 'bbox': [i*500, 0, (i+1)*500, 1000],
                'description': 'Rectangular photo; retain its environment; no stroke or transparency.'} for i in range(2)]
    background = {'id': 'bg', 'kind': 'background', 'background_type': 'solid_color',
                  'bbox': [0, 0, 1000, 1000], 'description': 'Transparent canvas behind independent panels',
                  'color': '#00000000'}
    inventory = {'objects': [background, *objects], 'layer_order': ['bg', 'photo_1', 'photo_0']}
    compiled = compile_inventory(inventory, [400, 300], 'normalized_1000')
    windows = defaults(compiled['analysis'])
    windows['coordinate_space'] = 'normalized_1000'
    for spec, item in zip(windows['photos'], objects):
        spec['placement'] = {'bbox': item['bbox'], 'rotation': 0}
        spec['reason'] = 'Visible rectangular boundary; full environment; no outline or transparent blend.'
    # Engine geometry may change; compact binding reads the public structure inventory.
    windows['photos'][0]['placement']['bbox'] = [0, 0, 550, 1000]
    windows['photos'][0]['mask'] = {'enabled': False}
    windows['photos'][1]['mask'] = {'enabled': True, 'shape': {'type': 'rectangle'}, 'stroke': empty_stroke()}
    assets = read(run/'prepared/catalog.json')['assets']
    assert [asset['id'] for asset in assets] == ['material_00', 'material_01']
    binding = {'photos': [{'slot_id': item['id'], 'asset_id': asset['id'], 'source_crop': [0, 0, 1, 1],
                           'crop_center': [.5, .5], 'mirror_x': False, 'reason': 'Keep the complete customer image.'}
                          for item, asset in zip(objects, assets)]}
    answers = [current_inventory(inventory), windows, binding]
    calls = []
    def transport(payload):
        assert len(calls) < len(answers), 'Unexpected extra model request'
        calls.append(deepcopy(payload))
        return reply(answers[len(calls)-1])
    config = Config(coordinate_mode='normalized_1000', response_format='json_object', max_requests=3)
    client = Client(run/'workflow/vlm', config, transport=transport)
    workflow = Workflow(run, config=config, client=client, reveal=False)
    result = workflow.execute()
    assert len(calls) == 3
    assert read(run/'structure.json')['layer_order'] == current_inventory(inventory)['layer_order']
    assert read(run/'analysis.json')['layer_order'] == inventory['layer_order']
    assert read(run/'scene.json')['layer_order'] == inventory['layer_order']
    window_input = json.loads(calls[1]['messages'][1]['content'][0]['text'].split('\n', 1)[1])
    assert window_input['analysis']['layer_order'] == ['photo_1', 'photo_0']
    binding_input = read(run/'workflow/binding-input.json')
    assert 'photo_windows' not in binding_input
    slot = binding_input['photo_slots'][0]
    assert slot['bbox'] == [0, 0, 220, 300]
    assert slot['aspect_ratio'] == round(220/300, 6)
    assert slot['requires_segmentable_subject'] is False
    assert not {'subject', 'mask', 'edge_gradient', 'linked_foregrounds'} & set(slot)
    wire = json.loads(calls[2]['messages'][1]['content'][0]['text'].split('\n', 1)[1])
    assert wire['photo_slots'][0]['bbox'] == [0, 0, 550, 1000]
    assert 'photo_windows' not in wire and 'analysis' not in wire
    assert not workflow.state.get('warnings')
    assert read(run/'workflow/window-geometry.json')['status'] == 'accepted'
    assert result['customer_photos_verified'] is True
    with Image.open(run/'final.png') as final:
        assert final.size == (400, 300)
        assert final.getpixel((100, 150))[:3] == (187, 51, 34)
        assert final.getpixel((300, 150))[:3] == (34, 85, 187)
        # photo_0 extends into photo_1: explicit layer_order must put red on top.
        assert final.getpixel((210, 150))[:3] == (187, 51, 34)
    digest = file_sha(run/'final.png')
    # A new controller instance resumes from disk without any model call.
    blocked = Client(run/'workflow/vlm', config, transport=lambda _: pytest.fail('Resume called the model'))
    resumed = Workflow(run, config=config, client=blocked, reveal=False).execute()
    assert resumed['render_id'] == result['render_id']
    assert file_sha(run/'final.png') == digest
    assert set(workflow.state['steps']) == {'analyze', 'photo_windows', 'boxes', 'boxes_apply', 'bind', 'build', 'screen', 'screen_apply', 'register'}
    assert workflow.state['steps']['screen']['status'] == 'skipped'
    assert workflow.state['steps']['screen_apply']['status'] == 'skipped'
    for stage, payload in zip(('analyze', 'photo_windows', 'bind'), calls):
        call = next((run/'workflow/vlm'/stage).iterdir())
        assert (call/'system-0.txt').read_text(encoding='utf-8') == payload['messages'][0]['content']
        assert read(call/'prompt-sources.json')['template'] == stage
    return run


def test_full_pipeline_and_fresh_process_resume(tmp_path):
    run = run_offline_pipeline(tmp_path)
    from workbench.server.store import Store
    progress = read(run/'workflow-progress.json')
    assert progress['status'] == 'finished'
    assert progress['steps']['photo_windows']['status'] == 'complete'
    assert all('value' not in step for step in progress['steps'].values())
    store = Store([run]); document = store.document(next(iter(store.tasks())))
    assert document['current_stage'] == 5
    assert [s['status'] for s in document['stages']] == ['complete']*5
    assert document['first_image'] and document['windows']


@pytest.mark.parametrize('provider', ['openrouter', 'yibu', 'codex'])
def test_saved_prompt_and_correction_match_actual_transport(tmp_path, provider):
    image = tmp_path/'input.png'
    Image.new('RGB', (120, 80), 'red').save(image)
    codex = provider == 'codex'
    config = Config(provider=provider, model='gpt-6.1-sol' if codex else 'qwen3.8-flash',
                    response_format='json_object', reasoning_enabled=True if codex else False,
                    reasoning_effort='medium' if codex else None)
    calls = []
    def transport(payload):
        calls.append(deepcopy(payload))
        return reply({} if len(calls) == 1 else {'color': 'red'})
    client = Client(tmp_path/'vlm', config, transport=transport)
    client.ask('probe', prompts.render('probe'), {}, [image], obj({'color': {'type': 'string'}}))
    folder = next((tmp_path/'vlm/probe').iterdir())
    for n, payload in enumerate(calls):
        stored = read(folder/f'request-{n}.json')
        assert stored['messages'][0] == payload['messages'][0]
        assert (folder/f'system-{n}.txt').read_text(encoding='utf-8') == payload['messages'][0]['content']
        assert stored['messages'][1]['content'][0] == payload['messages'][1]['content'][0]
        assert 'data:image/' not in json.dumps(stored)
    assert len(calls[1]['messages']) == 3
    assert calls[1]['messages'][-1] == read(folder/'request-1.json')['messages'][-1]
    assert read(folder/'prompt-sources.json')['files'] == (['codex_system.md', 'probe.md'] if codex else ['probe.md'])
    if codex:
        from collage.vlm.codex.transport import worker_request
        worker = tmp_path/'worker'; worker.mkdir()
        worker_request(calls[0], worker, 30)
        assert (worker/'instructions.txt').read_text(encoding='utf-8') == calls[0]['messages'][0]['content']
