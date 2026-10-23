"""Batch output-format selection, frozen settings, and actual transport validation."""
import json
from unittest.mock import Mock

from PIL import Image
import pytest

import batch_run
from collage.vlm.client import Client, ModelError
from collage.config import Config
from collage.storage import read, save


@pytest.fixture
def batch_file(tmp_path):
    for name in ('reference.png', 'material.png'):
        Image.new('RGB', (128, 128), 'blue').save(tmp_path / name)
    path = tmp_path / 'batch.json'
    save(path, {'provider': 'openrouter', 'model': 'qwen/qwen3.8-flash',
                'reasoning': 'off', 'references': ['reference.png'], 'materials': ['material.png']})
    return path


def planned(path, flags=()):
    return batch_run.plan(batch_run.parser().parse_args(['--config', str(path), *flags]))


@pytest.mark.parametrize('settings,flags,expected', [
    ({}, [], 'json_object'),
    ({}, ['--json-schema'], 'json_schema'),
    ({'json_schema': True}, [], 'json_schema'),
    ({'json_schema': False}, [], 'json_object'),
    ({'json_schema': True}, ['--no-json-schema'], 'json_object'),
    ({'json_schema': False}, ['--json-schema'], 'json_schema'),
    ({'response_format': 'json_schema'}, [], 'json_schema'),
    ({'response_format': 'prompt'}, [], 'prompt'),
    ({'response_format': 'json_schema'}, ['--no-json-schema'], 'json_object'),
    ({'response_format': 'json_schema', 'json_schema': False}, [], 'json_object'),
    ({'response_format': 'json_object', 'json_schema': True}, [], 'json_schema'),
])
def test_batch_format_precedence(batch_file, settings, flags, expected):
    save(batch_file, {**read(batch_file), **settings})
    before = batch_file.read_bytes()
    result = planned(batch_file, flags)
    assert {p['response_format'] for p in result['model_policy'].values()} == {expected}
    assert result['bbox_refinement'] is False
    assert batch_file.read_bytes() == before


@pytest.mark.parametrize('flag,expected', [(None, ('json_object', 'prompt')),
                                          ('--json-schema', ('json_schema', 'json_schema')),
                                          ('--no-json-schema', ('json_object', 'json_object'))])
def test_stage_policy_defaults_and_overrides(batch_file, flag, expected):
    data = read(batch_file)
    policy = Config(analysis_model='qwen/qwen3.8-max-0902').model_policy()
    policy['analyze'].pop('response_format')
    policy['subsequent']['response_format'] = 'prompt'
    data['model_policy'] = policy
    save(batch_file, data)
    result = planned(batch_file, [flag] if flag else [])['model_policy']
    assert tuple(result[s]['response_format'] for s in ('analyze', 'subsequent')) == expected
    assert result['analyze']['model'] == 'qwen/qwen3.8-max-0902'
    assert result['analyze']['reasoning']['enabled'] is True
    assert result['subsequent']['reasoning']['enabled'] is False
    assert read(batch_file)['model_policy'] == policy


def test_config_switch_overrides_both_explicit_stage_formats(batch_file):
    data = read(batch_file)
    data.update(model_policy=Config(response_format='json_schema').model_policy(), json_schema=False)
    save(batch_file, data)
    assert {p['response_format'] for p in planned(batch_file)['model_policy'].values()} == {'json_object'}


@pytest.mark.parametrize('invalid', ['false', 0, 1, None, [], {}])
def test_invalid_switch_fails_before_submission(batch_file, invalid):
    save(batch_file, {**read(batch_file), 'json_schema': invalid})
    with pytest.raises(ValueError, match='json_schema'):
        planned(batch_file)


@pytest.mark.parametrize('flag', ['--json-schema', '--no-json-schema'])
def test_resume_cannot_change_frozen_format(flag, monkeypatch):
    connect = Mock(side_effect=AssertionError('must not connect'))
    monkeypatch.setattr(batch_run.Workbench, 'connect', connect)
    assert batch_run.main(['--resume', 'unused.json', flag]) == 1
    connect.assert_not_called()


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('valid', [False, True])
def test_frozen_batch_controls_wire_format_and_keeps_local_validation(batch_file, tmp_path, enabled, valid):
    plan = planned(batch_file, ['--json-schema'] if enabled else ['--no-json-schema'])
    run = tmp_path / 'task'
    save(run / 'workflow-models.json', plan['model_policy'])
    # The saved batch policy must override even an opposite process default.
    config = Config(response_format='json_object' if enabled else 'json_schema').for_run(run)
    calls = []
    expected = {'photos': [{'photo_id': 'photo_a'}]}
    schema = {'type': 'object', 'properties': {'photos': {'type': 'array', 'minItems': 1,
              'items': {'type': 'object', 'properties': {'photo_id': {'const': 'photo_a'}},
                        'required': ['photo_id'], 'additionalProperties': False}}},
              'required': ['photos'], 'additionalProperties': False}
    def transport(payload):
        calls.append(payload)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(expected if valid else [])}}]}
    client = Client(tmp_path / 'vlm', config, transport=transport)
    ask = lambda: client.ask('photo_windows', 'Return all photo windows as a JSON object.',
                             {'photo_ids': ['photo_a']}, [batch_file.parent / 'reference.png'],
                             schema, single_request=True)
    if valid:
        assert ask() == expected
    else:
        with pytest.raises(ModelError, match='object'):
            ask()
        assert not list((tmp_path / 'vlm').rglob('accepted.json'))
    assert len(calls) == 1
    output = calls[0]['response_format']
    if enabled:
        assert output['type'] == 'json_schema'
        assert output['json_schema']['strict'] is True
        assert output['json_schema']['schema'] == schema
    else:
        assert output == {'type': 'json_object'}
    assert json.dumps(schema, ensure_ascii=False) in calls[0]['messages'][0]['content']


def test_codex_batch_supports_schema_toggle(batch_file):
    data = read(batch_file)
    data.update(provider='codex', model='gpt-6.1-sol', reasoning='medium')
    save(batch_file, data)
    assert {p['response_format'] for p in planned(batch_file)['model_policy'].values()} == {'json_object'}
    assert {p['response_format'] for p in planned(batch_file, ['--json-schema'])['model_policy'].values()} == {'json_schema'}
    assert {p['response_format'] for p in planned(batch_file, ['--no-json-schema'])['model_policy'].values()} == {'json_object'}
