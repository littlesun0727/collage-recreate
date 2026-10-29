from copy import deepcopy
import json
import re
from pathlib import Path
import pytest
from PIL import Image, ImageDraw
from jsonschema import Draft202012Validator

from collage import prompts
from collage.analysis import run_analysis
from collage.config import Config
from collage.storage import read, save
from collage.vlm.client import Client
from collage.vlm.gpt_schema import project
from collage.workflow.stages.analyze import compile_inventory, schema
from collage.decomposition import pipeline, service


def inventory(background=None, count=2):
    return {'background': background or {'type': 'photographic', 'id': 'photo_base', 'description': 'Full canvas scene'},
            'objects': [{'id': f'obj_{i}', 'kind': 'photo' if i == 0 else 'overlay',
                         'bbox': [100, 100, 600, 600], 'description': f'Object {i}'} for i in range(count)],
            'layer_order': [f'obj_{i}' for i in range(count)]}


@pytest.mark.parametrize('mode', ['pixel', 'normalized_1000'])
def test_all_prompt_examples_match_current_contract(mode):
    contract = schema([1000, 1000], mode)
    Draft202012Validator.check_schema(project(contract))
    for block in re.findall(r'```json\s*(.*?)```', prompts.render('analyze', mode=mode), re.S):
        value = json.loads(block)
        Draft202012Validator(contract).validate(value)
        result = compile_inventory(value, [1000, 1000], mode)['analysis']
        assert len(result['objects']) == len(value['objects'])+1
        assert result['layer_order'][1:] == value['layer_order']


@pytest.mark.parametrize('background', [
    {'type': 'transparent'}, {'type': 'solid_color', 'color': '#123456'},
    {'type': 'photographic', 'id': 'scene', 'description': 'Full image'},
    {'type': 'reference_clean', 'id': 'paper', 'description': 'Paper texture'},
])
def test_background_only_and_maximum_foreground_inventory(background):
    for count in (0, 160):
        value = inventory(background, count)
        result = compile_inventory(value, [1000, 1000], 'pixel')['analysis']
        assert len(result['objects']) == count+1
        assert result['objects'][0]['bbox'] == [0, 0, 1000, 1000]
        assert result['objects'][0].get('style', {}).get('fill') == (
            '#00000000' if background['type'] == 'transparent' else background.get('color'))


def test_background_links_and_reserved_id_preserve_source():
    value = inventory()
    value['objects'][0].update(id='layer_0', linked_to='photo_base')
    value['layer_order'][0] = 'layer_0'
    compiled = compile_inventory(value, [1000, 1000], 'pixel')['analysis']
    before = deepcopy(compiled)
    result, mapping = pipeline.extraction_inventory(compiled)
    assert compiled == before
    assert result['objects'][1]['linked_to'] == 'layer_0'
    assert result['objects'][1]['id'] == mapping['layer_0'] != 'layer_0'
    value['background']['type'] = 'reference_clean'
    with pytest.raises(ValueError):
        compile_inventory(value, [1000, 1000], 'pixel')


@pytest.mark.parametrize('fault', ['duplicate', 'parent', 'background_bbox', 'legacy_field'])
def test_new_wire_rejects_mixed_or_ambiguous_background(fault):
    value = inventory()
    if fault == 'duplicate': value['background']['id'] = 'obj_0'
    if fault == 'parent': value['objects'][0]['parent_id'] = 'photo_base'
    if fault == 'background_bbox': value['background']['bbox'] = [0, 0, 1000, 1000]
    if fault == 'legacy_field': value['objects'][0]['background_type'] = 'photo'
    with pytest.raises(ValueError): compile_inventory(value, [1000, 1000], 'pixel')


def analyzed_run(tmp_path, value=None):
    image = tmp_path/'source.png'; Image.new('RGB', (1000, 1000), 'white').save(image)
    run = tmp_path/'run'
    config = Config(coordinate_mode='pixel', max_requests=2)
    calls = []
    def transport(payload):
        calls.append(payload)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value or inventory())}}]}
    client = Client(run/'workflow/vlm', config, transport=transport)
    run_analysis(image, run, config, client)
    return image, run, config, client, calls


def test_shared_analysis_resume_plan_and_changed_prompt(tmp_path, monkeypatch):
    image, run, config, client, calls = analyzed_run(tmp_path)
    before = (run/'analysis.json').read_bytes()
    run_analysis(image, run, config, client)
    assert len(calls) == 1
    plan = pipeline.run_decomposition(run, batch_size=1)
    assert plan['batches_top_down'] == [['obj_1'], ['obj_0']]
    assert (run/'analysis.json').read_bytes() == before
    render = prompts.render
    monkeypatch.setattr(prompts, 'render', lambda *a, **k: str(render(*a, **k))+'\nChanged')
    with pytest.raises(ValueError, match='changed'):
        run_analysis(image, run, config, client)
    assert len(calls) == 1


def test_photographic_background_remains_a_binding_slot(tmp_path):
    from collage.workflow.stages.bind import read_structure, context_for
    from photo_windows import defaults
    _, run, config, *_ = analyzed_run(tmp_path)
    structure = read_structure(run, config)
    assert structure['objects'][0]['id'] == 'photo_base'
    analysis = read(run/'analysis.json')
    context = context_for({'reference_size': [1000, 1000], 'catalog': []},
                          structure, defaults(analysis))
    assert {o['slot_id'] for o in context['photo_slots']} == {'photo_base', 'obj_0'}


def test_paused_extraction_never_contacts_service(tmp_path, monkeypatch):
    _, run, *_ = analyzed_run(tmp_path)
    monkeypatch.setattr(service, 'fetch_batch', lambda *a, **k: pytest.fail('Unexpected paid call'))
    with pytest.raises(ValueError, match='paused'):
        pipeline.run_decomposition(run, extract=True)
    assert (run/'decomposition/plan.json').exists()


@pytest.mark.parametrize('transparent', [False, True])
def test_two_batches_use_shared_transport_and_export_without_touching_source(tmp_path, monkeypatch, transparent):
    _, run, *_ = analyzed_run(tmp_path, inventory({'type': 'transparent'}) if transparent else None)
    before = (run/'analysis.json').read_bytes()
    monkeypatch.setattr(pipeline, 'ROOT', tmp_path/'unpaused')
    import reveal
    monkeypatch.setattr(reveal, 'api_key', lambda *a: 'test-key')
    calls = []
    def fetch(task, reference, objects, key, timeout):
        calls.append((Path(reference).read_bytes(), deepcopy(objects)))
        task.mkdir(parents=True, exist_ok=True)
        for variant in ('base', 'aug'):
            Image.new('RGBA', (100, 100), (255, 255, 255, 0 if transparent else 255)).save(task/f'layers_{variant}_00.png')
            for n, obj in enumerate(objects, 1):
                layer = Image.new('RGBA', (100, 100))
                ImageDraw.Draw(layer).rectangle([10, 10, 59, 59], fill='red')
                ImageDraw.Draw(layer).point((5, 95), fill='red')
                layer.save(task/f'layers_{variant}_{n:02d}.png')
        save(task/'query_response.json', {'status': 'done', 'task_id': str(len(calls)),
             'output': {'boxes_mapping_index': list(range(len(objects))),
                        'layers_base': ['unused']*(len(objects)+1), 'layers_aug': ['unused']*(len(objects)+1)}})
    monkeypatch.setattr(service, 'fetch_batch', fetch)
    result = pipeline.run_decomposition(run, extract=True, batch_size=1)
    assert result['status'] == 'complete'
    out = run/'decomposition'
    from io import BytesIO
    assert Image.open(BytesIO(calls[1][0])).size == (100, 100)
    assert calls[1][1][0]['bbox'] == [10, 10, 60, 60]
    assert (run/'analysis.json').read_bytes() == before
    final = read(out/'analysis-refined.json')
    assert final['layer_order'] == ['layer_0', 'obj_0', 'obj_1']
    assert final['objects'][1]['bbox'] == [10, 10, 60, 60]
    assert final['objects'][0]['bbox'] == [0, 0, 100, 100]
    assert Image.open(out/'layers/aug/obj_0.png').getpixel((5, 95))[3] == 0
    assert Image.open(out/'reference.png').getpixel((5, 95)) == (255, 255, 255, 0 if transparent else 255)

    prediction = read(out/'analysis.json')
    assert prediction['coordinate_mode'] == final['coordinate_mode'] == 'pixel'
    assert prediction['reference_size'] == [1000, 1000]
    assert final['reference_size'] == [100, 100]
    assert set(prediction) == set(final)
    assert [set(o) for o in prediction['objects']] == [set(o) for o in final['objects']]
    assert not (out/'layers/base').exists()
    assert (out/'bbox.png').exists() and (out/'original.png').exists()
    import base64
    from io import BytesIO
    from xml.etree import ElementTree as ET
    root = ET.parse(out/'reference.svg').getroot()
    images = root.findall('{http://www.w3.org/2000/svg}g/{http://www.w3.org/2000/svg}image')
    composite = Image.new('RGBA', (100, 100))
    for image in images:
        asset = Image.open(BytesIO(base64.b64decode(image.attrib['href'].split(',')[1])))
        composite.alpha_composite(asset, (int(image.attrib['x']), int(image.attrib['y'])))
    expected = Image.open(out/'reference.png').convert('RGBA')
    assert composite.getchannel('A').tobytes() == expected.getchannel('A').tobytes()
    backdrop = Image.new('RGBA', composite.size, 'white')
    assert Image.alpha_composite(backdrop, composite).tobytes() == Image.alpha_composite(backdrop, expected).tobytes()
    monkeypatch.setattr(service, 'fetch_batch', lambda *a, **k: pytest.fail('Unexpected API call'))
    from collage.decomposition.artifacts import DELIVERABLES
    assert {p.name for p in out.iterdir()} == DELIVERABLES
    # Migrate previously delivered normalized JSON without remote calls or caches.
    for filename in ('analysis.json', 'analysis-refined.json'):
        value = read(out/filename)
        for obj in value['objects']:
            obj['bbox'] = [v*1000/value['reference_size'][i % 2] for i,v in enumerate(obj['bbox'])]
        value['coordinate_mode'] = 'normalized_1000'
        save(out/filename, value)
    assert pipeline.run_decomposition(run, batch_size=1, export_only=True)['status'] == 'exported'
    assert read(out/'analysis.json') == prediction
    assert read(out/'analysis-refined.json') == final
    assert pipeline.run_decomposition(run, extract=True, batch_size=1)['status'] == 'existing'
    assert {p.name for p in out.iterdir()} == DELIVERABLES


@pytest.mark.parametrize('mode', ['pixel', 'normalized_1000'])
def test_import_analysis_without_vlm_non_square(tmp_path, mode):
    image = tmp_path/'original.png'
    Image.new('RGB', (800, 400), 'white').save(image)
    value = inventory(count=1)
    value['objects'][0]['bbox'] = [80, 40, 480, 240] if mode == 'pixel' else [100, 100, 600, 600]
    path = tmp_path/'prediction.json'; save(path, value)
    before = path.read_bytes()
    run = tmp_path/'imported'
    pipeline.run_decomposition(run, analysis_path=path, reference=image, coordinate_mode=mode)
    output = read(run/'decomposition/analysis.json')
    assert output['reference_size'] == [800, 400]
    assert output['objects'][1]['bbox'] == [80, 40, 480, 240]
    assert path.read_bytes() == before
    # The exported analysis is a valid input to a separate extraction run.
    pipeline.run_decomposition(tmp_path/'resumed', analysis_path=run/'decomposition/analysis.json', reference=image)
    assert read(tmp_path/'resumed/decomposition/source-analysis.json')['objects'][1]['bbox'] == [80, 40, 480, 240]


def test_import_requires_unambiguous_coordinates_and_size(tmp_path):
    from collage.decomposition.artifacts import load_analysis
    with pytest.raises(ValueError, match='coordinate-mode'):
        load_analysis(inventory(), [800, 400])
    value = inventory(); value.update(reference_size=[400, 800], coordinate_mode='pixel')
    with pytest.raises(ValueError, match='dimensions'):
        load_analysis(value, [800, 400])


def test_pixel_output_rounds_normalized_once_and_preserves_pixel_input():
    from collage.decomposition.artifacts import pixel_document
    value = compile_inventory(inventory(count=1), [960, 1280], 'normalized_1000')['analysis']
    value['coordinate_mode'] = 'normalized_1000'
    value['objects'][0]['bbox'] = [0, 0, 1000, 1000]
    value['objects'][1]['bbox'] = [77, 64, 428, 410]
    output = pixel_document(value)
    assert output['objects'][1]['bbox'] == [74, 82, 411, 525]
    assert all(type(v) is int for obj in output['objects'] for v in obj['bbox'])
    assert pixel_document(output) == output
    assert value['objects'][1]['bbox'] == [77, 64, 428, 410]


def test_decomposition_accepts_legacy_multi_window_frame():
    value = inventory(count=2)
    for obj in value['objects']:
        obj.update(kind='photo', parent_id='carrier')
    value['objects'].append({'id':'carrier', 'kind':'frame', 'bbox':[50,50,900,900],
                             'description':'Two-window carrier', 'photo_id':'obj_0'})
    value['layer_order'].insert(0, 'carrier')
    compiled = compile_inventory(value, [1000,1000], 'pixel')['analysis']
    from validate import analysis_check
    analysis_check(compiled)
    result, _ = pipeline.extraction_inventory(compiled)
    assert next(o for o in result['objects'] if o['id']=='carrier')['kind']=='frame'
    assert next(o for o in compiled['objects'] if o['id']=='carrier')['kind']=='frame'


def test_thin_layer_expands_only_request_box():
    from collage.decomposition.chain import scaled_analysis
    source = {'reference_size':[900,675], 'objects':[{'id':'divider','bbox':[488,91,490,590]}],
              'layer_order':['divider']}
    before = deepcopy(source)
    request = scaled_analysis(source, ['divider'], [900,675])
    assert source == before
    l,t,r,b = request['objects'][0]['bbox']
    assert r-l >= 8*900/1024
    assert l <= 488 < 490 <= r and (t,b)==(91,590)


def test_permission_error_resumes_only_known_submitted_task(tmp_path, monkeypatch):
    batch = tmp_path/'batches/001'
    save(batch/'input/analysis.json', {'objects':[{'id':'obj_0','kind':'photo'}]})
    save(tmp_path/'submission-budget.json', {'limit':3})
    monkeypatch.setattr(service, 'reserve_submission', lambda *a,**k: None)
    monkeypatch.setattr(service.time, 'sleep', lambda *a: None)
    attempts = []
    def fetch(task, *args):
        attempts.append(1)
        if len(attempts)==1:
            save(task/'submit_response.json', {'task_id':'existing'})
            raise PermissionError('transient sharing violation')
    monkeypatch.setattr(service, 'fetch_batch', fetch)
    service.submit_and_fetch(batch,'test',900)
    assert len(attempts)==2
    (batch/'raw/submit_response.json').unlink()
    def unknown(*args):
        raise PermissionError('receipt missing')
    monkeypatch.setattr(service, 'fetch_batch', unknown)
    with pytest.raises(PermissionError):
        service.submit_and_fetch(batch,'test',900)
