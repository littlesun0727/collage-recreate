"""Model-facing analysis format is concise; full validation remains authoritative."""
import json

import pytest

from collage import prompts
from collage.config import Config
from collage.storage import read
from collage.vlm.client import Client, ModelError
from collage.workflow.stages.analyze import compile_inventory, schema


@pytest.mark.parametrize('template', ['analyze', 'analyze_full_retry'])
@pytest.mark.parametrize('mode', ['normalized_1000', 'pixel'])
@pytest.mark.parametrize('invalid', ['array', 'missing_description', 'frame_link'])
def test_analysis_uses_inline_format_but_rejects_invalid_answers(tmp_path, template, mode, invalid):
    photo = {'id': 'photo_1', 'kind': 'photo', 'bbox': [10, 10, 90, 90],
             'description': 'Complete photograph'}
    answer = {'objects': [photo], 'layer_order': ['photo_1']}
    if invalid == 'array':
        answer = []
    elif invalid == 'missing_description':
        del photo['description']
    else:
        answer['objects'].insert(0, {'id': 'frame_1', 'kind': 'frame', 'bbox': [0, 0, 100, 100],
                                     'description': 'Decorative frame', 'photo_id': 'missing'})
        answer['layer_order'].append('frame_1')
    calls = []
    def transport(payload):
        calls.append(payload)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]}
    client = Client(tmp_path/'vlm', Config(max_requests=1), transport=transport)
    contract = schema([200, 200], mode)
    with pytest.raises(ModelError):
        client.ask('analyze', prompts.render(template, mode=mode), {'reference_size': [200, 200]},
                   [], contract, lambda v: compile_inventory(v, [200, 200], mode),
                   single_request=True, reference_mode=mode)
    assert len(calls) == 1
    assert calls[0]['response_format'] == {'type': 'json_object'}
    system = calls[0]['messages'][0]['content']
    assert system == prompts.render(template, mode=mode)
    assert json.dumps(contract, ensure_ascii=False) not in system
    assert '"allOf"' not in system
    assert '{{' not in system
    folder = next((tmp_path/'vlm/analyze').iterdir())
    assert not (folder/'accepted.json').exists()
    assert read(folder/'prompt-sources.json')['output_contract'] == 'template'
    assert read(folder/'prompt-sources.json')['files'] == ['analyze.md']


def test_analysis_and_window_prompts_have_independent_responsibilities():
    analysis = prompts.render('analyze', mode='normalized_1000')
    windows = prompts.render('photo_windows')
    assert all(label not in analysis for label in ('cover', 'cutout', 'feather', 'stroke'))
    assert all(label not in windows for label in ('photo.mode', 'cutout', 'feather', 'cover'))
    assert all(field in windows for field in ('mask.stroke', 'edge_gradient', 'subject.stroke', 'preserve_background'))
    assert windows.sources == ['photo_windows.md']
    assert '{{PHOTO_EFFECT_RULES}}' not in windows


@pytest.mark.parametrize('mode', ['normalized_1000', 'pixel'])
def test_analysis_example_has_explicit_order_independent_of_object_listing(mode):
    from jsonschema import Draft202012Validator
    example = json.loads(prompts.render('analyze', mode=mode).split('```json\n')[1].split('```')[0])
    assert set(example) == {'objects', 'layer_order'}
    Draft202012Validator(schema([200, 200], mode)).validate(example)
    result = compile_inventory(example, [200, 200], mode)['analysis']
    assert result['layer_order'] == example['layer_order']
    assert result['layer_order'] != [o['id'] for o in result['objects']]


def test_field_repair_still_receives_its_dynamic_contract():
    contract = {'type': 'object', 'properties': {'updates': {'type': 'object'}}, 'required': ['updates']}
    request = prompts.prepare(prompts.render('analyze_repair', mode='normalized_1000'),
                              {}, [], contract, Config())
    assert json.dumps(contract, ensure_ascii=False) in prompts.messages(request)[0]['content']
