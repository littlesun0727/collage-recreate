"""Codex transport must preserve V8 structure, repairs and coordinate contracts."""
from copy import deepcopy
import json
import threading
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator
from PIL import Image

from batch_run import Workbench
from collage.workflow.stages import analyze as structure
from collage.vlm.client import Client
from collage.vlm.codex.transport import worker_request
from collage.config import Config, ROOT
from collage.storage import read, save
from workbench.server.http import create_server


@pytest.mark.parametrize('mode', ['normalized_1000', 'pixel'])
@pytest.mark.parametrize('output', ['json_schema', 'json_object'])
@pytest.mark.parametrize('repair', ['field_patch', 'full_retry'])
def test_structure_repair_preserves_codex_policy_and_inventory_contract(tmp_path, mode, output, repair):
    size = [960, 1280]
    image = tmp_path/'reference.png'
    Image.new('RGB', tuple(size), 'white').save(image)
    policy = Config.load(ROOT/'examples/codex.example.json').model_policy()
    policy['analyze'].update(coordinate_mode=mode, response_format=output,
                             reasoning={'enabled': True, 'effort': 'high'})
    # Any accidental use of the subsequent policy must fail the assertions below.
    policy['subsequent'].update(provider='yibu', model='qwen3.8-flash', reasoning={'enabled': False})
    config = Config(max_requests=2).with_policy(policy)
    save(tmp_path/'workflow-models.json', policy)
    config = Config().for_run(tmp_path)
    valid = {'objects': [
        {'id': 'photo', 'kind': 'photo', 'description': 'portrait',
         'bbox': [0, 0, 1000, 1000] if mode == 'normalized_1000' else [0, 0, *size]},
        {'id': 'title', 'kind': 'text', 'bbox': [10, 10, 100, 100], 'text': 'Hello'},
    ], 'layer_order': ['photo', 'title']}
    invalid = deepcopy(valid)
    # A bad box passes the projected remote subset but requires V8 business repair.
    invalid['objects'][0]['bbox'] = [900, 0, 100, 800]
    ambiguous = deepcopy(valid)
    ambiguous['objects'][1]['id'] = 'photo'
    replies = iter([invalid, {'updates': {'photo': {'bbox': valid['objects'][0]['bbox']}}}]
                   if repair == 'field_patch' else [ambiguous, valid])
    calls = []

    def transport(payload):
        calls.append(payload)
        request = worker_request(payload, tmp_path, 10)
        assert request['model'] == 'gpt-6.1-sol'
        assert request['effort'] == 'high'
        images = [p for p in request['input'] if p['type'] == 'local_image']
        assert len(images) == 1 and images[0]['detail'] == 'original'
        assert Image.open(images[0]['path']).size == tuple(size)
        context = json.loads(payload['messages'][1]['content'][0]['text'].split('\n', 1)[1])
        captions = [p['text'] for p in payload['messages'][1]['content'][1:] if p['type'] == 'text']
        assert ('reference_size' in context) == (mode == 'pixel')
        assert any('960×1280' in c for c in captions) == (mode == 'pixel')
        answer = next(replies)
        if output == 'json_schema':
            if 'objects' in answer:
                fields = structure.schema(size, mode)['properties']['objects']['items']['properties']
                answer = {**answer, 'objects': [{k: row.get(k) for k in fields} for row in answer['objects']]}
            Draft202012Validator(request['schema']).validate(answer)
        else:
            assert 'schema' not in request
        return {'model': request['model'], 'choices': [
            {'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]}

    client = Client(tmp_path/'workflow/vlm', config, transport=transport)
    save(tmp_path/'input.json', {'reference_size': size})
    written = []
    runner = SimpleNamespace(run=tmp_path, control=tmp_path/'workflow', config=config, client=client,
                             reference_context=lambda: {'reference_size': size},
                             analysis_images=lambda: [image], candidates=client.candidates,
                             write_reference=written.append)
    runner.progress = lambda *args: None
    structure.execute(runner)
    assert len(calls) == client.usage()['requests'] == 2
    assert read(tmp_path/'workflow/structure-analysis.json')['repair_kind'] == repair
    assert written[0] == structure.compile_inventory(valid, size, mode, tmp_path)
    assert config.for_stage('windows').provider == 'yibu'
    assert config.for_stage('analyze_repair').reasoning_effort == 'high'


def test_workbench_codex_settings_http_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr('collage.vlm.codex.transport.check_runtime', lambda: 'node')
    server = create_server([tmp_path/'tasks'], port=0, enable_editor=True, media_root=tmp_path/'service')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = Workbench(f'http://127.0.0.1:{server.server_port}')
        client.token = client.request('/api/chat-config')['token']
        public = client.request('/api/model-config')
        assert {'id': 'codex', 'name': 'Codex SDK'} in public['providers']
        assert public['credentials']['codex'] is True
        catalog = client.request('/api/models?provider=codex')
        assert {m['id'] for m in catalog['models']} == {'gpt-6.1-sol', 'gpt-6-luna'}
        caps = client.request('/api/model-capabilities?provider=codex&model=gpt-6.1-sol')
        assert caps['efforts'] == ['low', 'medium', 'high', 'xhigh']
        policy = Config.load(ROOT/'examples/codex.example.json').model_policy()
        policy['analyze']['reasoning']['effort'] = 'high'
        policy['subsequent'].update(model='gpt-6-luna', response_format='json_object')
        assert client.request('/api/model-config', {'policy': policy})['policy'] == policy
        assert client.request('/api/model-config')['policy'] == policy
        assert read(tmp_path/'service/model-settings.json') == policy
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
