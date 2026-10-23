import base64
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest
from PIL import Image

from collage.vlm.client import Client, ModelError
from collage.config import Config, ROOT
from collage.contracts import obj
from collage.vlm.codex import transport
from collage.vlm.providers import payload_for, request_options, restore_response, wire_schema
from collage.storage import read
from workbench.server.models import ModelSettings


def config():
    return Config.load(ROOT/'examples/codex.example.json')


def response(content='{"color":"red"}'):
    return {'id': 'thread-1', 'provider': 'codex', 'model': 'gpt-6.1-sol',
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10},
            'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}


def test_policy_defaults_and_round_trip(monkeypatch):
    monkeypatch.setenv('COLLAGE_VLM_PROVIDER', 'codex')
    for key in ['COLLAGE_VLM_MODEL', 'COLLAGE_ANALYSIS_MODEL', 'COLLAGE_MODEL_CONFIG']:
        monkeypatch.delenv(key, raising=False)
    loaded = Config.load()
    for stage in ['analyze', 'windows', 'boxes', 'review']:
        value = loaded.for_stage(stage)
        assert (value.model, value.reasoning_effort) == ('gpt-6.1-sol', 'medium')
    assert loaded.with_policy(loaded.model_policy()).model_policy() == loaded.model_policy()
    with pytest.raises(ValueError, match='本机 Codex 登录'):
        loaded.api_key()


@pytest.mark.parametrize('changes', [
    {'reasoning_enabled': False, 'reasoning_effort': None},
    {'reasoning_effort': 'minimal'},
    {'reasoning_effort': None, 'reasoning_max_tokens': 1024},
    {'model': 'qwen3.8-flash'}, {'model': 'openai/gpt-6.1-sol'},
])
def test_unsupported_policy_rejected(changes):
    with pytest.raises(ValueError):
        request_options(replace(config().for_stage('analyze'), **changes))


def test_schema_projection_correction_and_restore():
    schema = {'type': 'object', 'properties': {'color': {'type': 'string'}, 'note': {'type': 'string'}},
              'required': ['color'], 'additionalProperties': False}
    cfg = replace(config().for_stage('analyze'), response_format='json_schema')
    payload = payload_for(cfg, 'probe', [], schema, correction=True)
    assert payload['response_format']['type'] == 'json_schema'
    assert payload['response_format']['json_schema']['schema'] == wire_schema(cfg, schema)
    assert restore_response(cfg, {'color': 'red', 'note': None}, schema) == {'color': 'red'}
    assert 'temperature' not in payload and 'max_tokens' not in payload
    assert payload['modelReasoningEffort'] == 'medium'
    assert payload['output_token_limit_supported'] is False


@pytest.mark.parametrize('output', ['json_object', 'prompt'])
def test_no_schema_keeps_json_instructions_validation_and_cache_isolation(tmp_path, output):
    schema = obj({'color': {'type': 'string'}})
    original = config().for_stage('subsequent')
    cfg = replace(original, model='gpt-6-luna', response_format=output)
    calls = []
    replies = iter([response('{"color":42}'), response()])

    def complete(payload):
        calls.append(payload)
        worker = transport.worker_request(payload, tmp_path, 3)
        assert 'schema' not in worker
        assert worker['model'] == 'gpt-6-luna'
        assert worker['effort'] == 'medium'
        instructions = Path(worker['instructions_file']).read_text(encoding='utf-8')
        assert 'color' in instructions and 'JSON' in instructions
        return next(replies)

    client = Client(tmp_path/'vlm', cfg, transport=complete)
    assert client.ask('probe', 'Inspect', {}, [], schema) == {'color': 'red'}
    assert len(calls) == 2  # The wrong type still triggers local validation and correction.
    assert client.ask('probe', 'Inspect', {}, [], schema) == {'color': 'red'}
    assert len(calls) == 2
    with_schema = replace(cfg, response_format='json_schema')
    Client(tmp_path/'vlm', with_schema, transport=lambda p: calls.append(p) or response()).ask(
        'probe', 'Inspect', {}, [], schema)
    assert len(calls) == 3  # Schema-on cannot reuse schema-off output.
    assert calls[-1]['response_format']['type'] == 'json_schema'


def test_image_order_and_instruction_separation(tmp_path):
    image = tmp_path/'source.png'
    Image.new('RGB', (4, 3), 'red').save(image)
    url = 'data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode()
    messages = [{'role': 'system', 'content': 'Return JSON.'}, {'role': 'user', 'content': [
        {'type': 'text', 'text': 'First label'}, {'type': 'image_url', 'image_url': {'url': url}},
        {'type': 'text', 'text': 'Second label'}, {'type': 'image_url', 'image_url': {'url': url}}]},
        {'role': 'user', 'content': 'Correction data'}]
    request = transport.worker_request(payload_for(config(), 'probe', messages, obj({})), tmp_path, 3)
    inputs = request['input']
    assert [x['text'] for x in inputs if x['type'] == 'text'] == [
        'First label', 'Second label', 'Correction data']
    assert [x['type'] for x in inputs] == ['text', 'local_image', 'text', 'local_image', 'text']
    for item in inputs:
        if item['type'] == 'local_image':
            assert Path(item['path']).read_bytes() == image.read_bytes()
            assert item['detail'] == 'original'
    assert 'Return JSON.' in Path(request['instructions_file']).read_text(encoding='utf-8')
    assert request['timeout_ms'] == 3000
    assert request_options(config())['bridge_version'] == 3
    assert request_options(config())['image_detail'] == 'original'
    messages[1]['content'][1]['image_url']['url'] = 'https://example.com/image.png'
    with pytest.raises(ValueError, match='inline'):
        transport.worker_request(payload_for(config(), 'probe', messages, obj({})), tmp_path, 3)


def test_client_cache_usage_and_provider_isolation(tmp_path):
    calls = []
    client = Client(tmp_path/'vlm', config(), transport=lambda p: calls.append(p) or response())
    schema = obj({'color': {'type': 'string'}})
    assert client.ask('probe', 'Inspect', {}, [], schema) == {'color': 'red'}
    assert client.ask('probe', 'Inspect', {}, [], schema) == {'color': 'red'}
    assert len(calls) == 1
    # Accepted results remain usable even without Node/SDK on a resume machine.
    cached = Client(tmp_path/'vlm', config())
    assert cached.ask('probe', 'Inspect', {}, [], schema) == {'color': 'red'}
    assert client.usage() == {'requests': 1, 'prompt_tokens': 20, 'completion_tokens': 10,
                              'reported_cost_usd': 0, 'cost_complete': False}
    other = replace(config().for_stage('subsequent'), provider='yibu')
    Client(tmp_path/'vlm', other, transport=lambda p: calls.append(p) or response()).ask('probe', 'Inspect', {}, [], schema)
    assert len(calls) == 2
    assert len(list((tmp_path/'vlm/probe').iterdir())) == 2


def test_sdk_failure_is_durable_and_not_retried(tmp_path):
    calls = []
    def fail(payload):
        calls.append(payload)
        raise transport.CodexError(transport.ERRORS['timeout'])
    client = Client(tmp_path, config(), transport=fail)
    with pytest.raises(ModelError, match='超时'):
        client.ask('probe', 'Inspect', {}, [], obj({}))
    assert len(calls) == 1
    assert client.usage()['requests'] == 1
    assert read(tmp_path/'requests/001.json')['status'] == 'sdk_error'


def test_bridge_process_stdin_and_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(transport, 'check_runtime', lambda: 'node')
    seen = {}
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            seen.update(command=command, options=kwargs)
        def communicate(self, data, timeout):
            seen['request'] = json.loads(data)
            assert Path(seen['request']['instructions_file']).is_file()
            return json.dumps(response()).encode(), None
    monkeypatch.setattr(transport.subprocess, 'Popen', Process)
    payload = payload_for(config(), 'probe', [{'role': 'system', 'content': 'JSON'}], obj({}))
    assert transport.complete(payload, 5) == response()
    assert seen['command'] == ['node', str(transport.BRIDGE)]
    assert seen['options']['stderr'] == subprocess.DEVNULL
    assert not Path(seen['request']['working_directory']).exists()


def test_settings_catalog_and_mixed_policy_no_http(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, 'check_runtime', lambda: 'node')
    monkeypatch.setattr(Config, 'api_key', lambda self: 'test-key')
    settings = ModelSettings(tmp_path)
    policy = config().model_policy()
    policy['subsequent']['provider'] = 'yibu'
    saved = settings.update(policy)
    assert saved['policy'] == policy
    assert saved['credentials']['codex'] is True
    assert settings.catalog('codex')['models'][0]['id'] == 'gpt-6.1-sol'


def test_hard_timeout_stops_worker_and_does_not_retry(monkeypatch):
    monkeypatch.setattr(transport, 'check_runtime', lambda: 'node')
    stopped = []
    class Process:
        def __init__(self, *args, **kwargs):
            self.cwd = Path(kwargs['cwd'])
        def communicate(self, data, timeout):
            raise subprocess.TimeoutExpired('node', timeout)
    monkeypatch.setattr(transport.subprocess, 'Popen', Process)
    monkeypatch.setattr(transport, 'stop_worker', lambda process: stopped.append(process))
    payload = payload_for(config(), 'probe', [{'role': 'system', 'content': 'JSON'}], obj({}))
    with pytest.raises(transport.CodexError, match='超时'):
        transport.complete(payload, 1)
    assert len(stopped) == 1
    assert not stopped[0].cwd.exists()


@pytest.mark.parametrize('flag, output', [('--json-schema', 'json_schema'), ('--no-json-schema', 'json_object')])
def test_batch_cli_accepts_codex_and_snapshots_both_stages(tmp_path, flag, output):
    import batch_run
    image = tmp_path/'input.png'
    Image.new('RGB', (4, 3), 'red').save(image)
    args = batch_run.parser().parse_args([
        '--provider', 'codex', '--model', 'gpt-6-luna', '--reasoning', 'medium',
        flag, '--references', str(image), '--materials', str(image), '--dry-run'])
    planned = batch_run.plan(args)
    for entry in planned['model_policy'].values():
        assert entry['provider'] == 'codex'
        assert entry['model'] == 'gpt-6-luna'
        assert entry['reasoning'] == {'enabled': True, 'effort': 'medium'}
        assert entry['response_format'] == output
