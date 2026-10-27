from copy import deepcopy

from PIL import Image
import pytest

from collage import contracts
from common import read, save, sha
import reveal
import reveal_chain


@pytest.mark.parametrize('retry_finds_missing', [True, False])
def test_missing_asset_is_retried_once_on_residual_and_resume_reuses_cache(tmp_path, monkeypatch, retry_finds_missing):
    reference = tmp_path / 'reference.png'
    Image.new('RGB', (240, 200), 'gray').save(reference)
    targets = [{'id': name, 'kind': 'overlay', 'method': 'extract', 'bbox': box}
               for name, box in [('first', [10, 10, 60, 70]), ('second', [100, 100, 180, 180])]]
    scene = {'reference': {'file': str(reference), 'sha256': sha(reference)},
             'reference_size': [240, 200], 'objects': deepcopy(targets)}
    calls = []

    def fetch(task, image, objects, key, timeout):
        calls.append({'ids': [o['id'] for o in objects], 'input_sha': sha(image)})
        indices = [0] if len(calls) == 1 or retry_finds_missing else []
        save(task / 'request_meta.json', {'reference_sha256': sha(image), 'objects': objects,
             'model': 'ecommerce_layer', 'version': 'v1.1'})
        save(task / 'query_response.json', {'status': 'done', 'task_id': 'offline', 'output': {
             'boxes_mapping_index': indices, 'image_boxes': [o['bbox'] for o in objects]}})
        Image.new('RGBA', (240, 200), 'white').save(task / 'layers_aug_00.png')
        for index in range(1, len(indices) + 1):
            Image.new('RGBA', (240, 200), 'red').save(task / f'layers_aug_{index:02d}.png')

    monkeypatch.setattr(reveal, 'fetch_batch', fetch)
    monkeypatch.setattr(reveal, 'api_key', lambda _: 'offline')
    run = tmp_path / 'run'
    config = {'remote': True, 'max_requests': 3}
    found, receipts = reveal_chain.acquire(run, scene, targets, config)
    assert [c['ids'] for c in calls] == [['first', 'second'], ['second']]
    assert calls[1]['input_sha'] != calls[0]['input_sha']
    assert receipts[0]['status'] == 'partial'
    assert receipts[1]['phase'] == 'materials_retry'
    assert set(found) == ({'first', 'second'} if retry_finds_missing else {'first'})
    assert read(run / 'assets/reveal/chain-report.json')['missing_material_ids'] == ([] if retry_finds_missing else ['second'])
    monkeypatch.setattr(reveal, 'fetch_batch', lambda *args: pytest.fail('Resume submitted another request'))
    resumed, _ = reveal_chain.acquire(run, scene, targets, {**config, 'remote': False})
    assert set(resumed) == set(found)


def test_missing_retry_respects_request_ceiling(tmp_path, monkeypatch):
    reference = tmp_path / 'reference.png'
    Image.new('RGB', (240, 200)).save(reference)
    target = {'id': 'missing', 'kind': 'overlay', 'method': 'extract', 'bbox': [20, 20, 60, 70]}
    scene = {'reference': {'file': str(reference), 'sha256': sha(reference)},
             'reference_size': [240, 200], 'objects': [target]}
    calls = []

    def fetch(task, image, objects, key, timeout):
        calls.append(task)
        save(task / 'request_meta.json', {'reference_sha256': sha(image), 'objects': objects,
             'model': 'ecommerce_layer', 'version': 'v1.1'})
        save(task / 'query_response.json', {'status': 'done', 'output': {'boxes_mapping_index': [], 'image_boxes': []}})
        Image.new('RGBA', (240, 200), 'white').save(task / 'layers_aug_00.png')

    monkeypatch.setattr(reveal, 'fetch_batch', fetch)
    monkeypatch.setattr(reveal, 'api_key', lambda _: 'offline')
    found, _ = reveal_chain.acquire(tmp_path / 'run', scene, [target], {'remote': True, 'max_requests': 1})
    assert not found and len(calls) == 1
