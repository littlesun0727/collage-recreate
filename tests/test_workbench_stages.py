"""The workbench follows saved products, not the engine's legacy six numbers."""
import json
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

from PIL import Image
import pytest

from collage.storage import save
from workbench.server.http import create_server
from workbench.server.store import Store, read
from workbench.server.workflow_view import project


def task(tmp_path):
    save(tmp_path/'input.json', {'reference_size': [100, 100]})
    (tmp_path/'prepared').mkdir(exist_ok=True)
    Image.new('RGB', (100, 100)).save(tmp_path/'prepared/reference.png')
    return tmp_path


def test_partial_analysis_has_five_nodes_and_keeps_later_steps_pending(tmp_path):
    run = task(tmp_path)
    save(run/'structure.json', {'objects': []})
    stages, current, _, _ = project(run, read, [], [], [])
    assert [s['key'] for s in stages] == ['prepare', 'analyze', 'photo_windows', 'materials', 'compose']
    assert current == 2
    assert stages[1]['status'] == 'saved'
    assert [s['status'] for s in stages[2:]] == ['pending']*3
    assert stages[1]['artifacts'][1]['exists'] is False


@pytest.mark.parametrize('step,index', [('analyze', 2), ('photo_windows', 3), ('bind', 4), ('build', 4), ('register', 5)])
def test_failures_appear_at_the_corresponding_node(tmp_path, step, index):
    run = task(tmp_path)
    save(run/'workflow-progress.json', {'status': 'failed', 'current_step': step, 'error': 'failed request',
                                       'steps': {step: {'status': 'running'}}})
    stages, current, _, _ = project(run, read, [], [], [])
    assert current == index
    assert stages[index-1]['status'] == 'failed'
    assert stages[index-1]['summary'] == 'failed request'


def test_extraction_and_composition_are_distinct(tmp_path):
    run = task(tmp_path)
    save(run/'bindings.json', {'photos': []})
    state = {'status': 'running', 'current_step': 'build', 'steps': {
        'bind': {'status': 'complete'}, 'build': {'status': 'running', 'started_at': '2026-10-23T10:00:00Z'}}}
    save(run/'workflow-progress.json', state)
    stages, current, _, _ = project(run, read, [], [], [{'status': 'waiting'}])
    assert current == 4 and stages[3]['status'] == 'running'
    assert stages[4]['status'] == 'pending'
    old = [{'event': 'materials_started', 'at': '2026-10-22T10:00:00Z'}]
    assert project(run, read, old, [], [])[1] == 4
    rows = old + [{'event': 'materials_started', 'at': '2026-10-23T10:01:00Z'}]
    stages, current, _, _ = project(run, read, rows, [], [])
    assert current == 5 and stages[3]['status'] == 'complete'
    assert stages[4]['status'] == 'running'


def test_old_sixth_stage_never_creates_a_sixth_node_or_false_final(tmp_path):
    run = task(tmp_path)
    stages, current, _, _ = project(run, read, [{'event': 'workflow_stage', 'stage': 6, 'status': 'complete'}], [], [])
    assert len(stages) == 5 and current == 1
    assert stages[4]['status'] == 'pending'


def test_store_exposes_window_and_binding_products_without_a_scene(tmp_path):
    run = task(tmp_path)
    save(run/'structure.json', {'coordinate_mode': 'normalized_1000', 'objects': [
        {'id': 'photo_1', 'kind': 'photo', 'bbox': [100, 200, 900, 800], 'description': 'photo'},
        {'id': 'bg', 'kind': 'background', 'background_type': 'reference_clean', 'bbox': [0, 0, 1000, 1000], 'description': 'photographic background'}]})
    save(run/'bindings.json', {'photos': [{'slot_id': 'photo_1', 'asset_id': 'material_00'}]})
    save(run/'prepared/catalog.json', {'assets': [{'id': 'material_00', 'thumbnail': str(run.parent/'shared.jpg')}]})
    (run/'prepared/vlm-materials').mkdir()
    Image.new('RGB', (50, 50)).save(run/'prepared/vlm-materials/material_00.jpg')
    (run/'previews').mkdir()
    Image.new('RGB', (100, 100)).save(run/'previews/photo-windows.png')
    store = Store([run]); key = next(iter(store.tasks()))
    doc = store.document(key)
    assert doc['windows'] and doc['current_stage'] == 4
    assert doc['materials'][0]['bbox'] == [10, 20, 90, 80]
    assert doc['materials'][0]['binding']['asset_id'] == 'material_00'
    assert doc['materials'][0]['images']['customer'] == doc['catalog'][0]['image']
    assert doc['catalog'][0]['image']
    assert doc['materials'][1]['method'] == '参考提取'
    assert json.loads(store.product_document(key, 'structure.json'))['objects'][0]['id'] == 'photo_1'
    for name in ['input.json', '../.env', 'workflow/state.json']:
        with pytest.raises(KeyError):
            store.product_document(key, name)


def test_product_endpoint_serves_exact_json_and_denies_other_files(tmp_path):
    run = task(tmp_path)
    save(run/'structure.json', {'objects': []})
    server = create_server([run], 0, media_root=run/'service')
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        tasks = json.load(urlopen(base+'/api/tasks'))
        prefix = base+'/api/tasks/'+tasks[0]['id']+'/documents/'
        with urlopen(prefix+'structure.json?download=1') as response:
            assert response.read() == (run/'structure.json').read_bytes()
            assert response.headers['Content-Disposition'] == 'attachment; filename="structure.json"'
        with pytest.raises(HTTPError) as error:
            urlopen(prefix+'input.json')
        assert error.value.code == 404
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_extracted_layers_are_visible_before_a_first_image_exists(tmp_path):
    run = task(tmp_path)
    save(run/'analysis.json', {'objects': [{'id': 'deco', 'kind': 'overlay', 'method': 'extract',
                                          'bbox': [10, 10, 90, 90]}]})
    save(run/'bindings.json', {'photos': []})
    raw, processed = run/'raw.png', run/'processed.png'
    Image.new('RGBA', (80, 80), 'blue').save(raw)
    Image.new('RGBA', (80, 80), 'blue').save(processed)
    (run/'events.jsonl').write_text(json.dumps({'event': 'material_extracted', 'object_id': 'deco',
        'raw_file': str(raw), 'processed_file': str(processed)})+'\n')
    store = Store([run]); doc = store.document(next(iter(store.tasks())))
    assert doc['current_stage'] == 4
    assert doc['materials'][0]['images']['raw']
    assert doc['materials'][0]['images']['processed']
    assert doc['first_image'] is None and doc['stages'][4]['status'] == 'pending'
