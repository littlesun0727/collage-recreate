"""Readable directories must preserve cache ownership and durable queue identity."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import subprocess
import sys
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from collage import contracts  # Registers the standalone engine modules.
from collage.config import Config
from common import cache_directory, fingerprint, read, save, sha
import reveal
import reveal_chain
import reveal_grouped
from workbench.server.motion import reserve_task_paths
from workbench.server.batches import Batches
from workbench.server.store import Store


def test_task_reservations_survive_restart_deleted_and_occupied_paths(tmp_path):
    root = tmp_path/'tasks'
    first = reserve_task_paths(root, 2)
    day = datetime.now().astimezone().strftime('%Y%m%d')
    assert [p.name for p in first] == [f'task-{day}-001', f'task-{day}-002']
    assert not any(p.exists() for p in first), 'Preparing requires a new directory'
    first[0].mkdir(); first[0].rmdir()
    # A separately created task must never be mistaken for this import's output.
    (root/f'task-{day}-010').mkdir()
    result = subprocess.run(
        [sys.executable, '-B', '-c',
         'import json,sys; from workbench.server.motion import reserve_task_paths; '
         'print(json.dumps([p.name for p in reserve_task_paths(sys.argv[1], 2)]))', str(root)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert json.loads(result.stdout) == [f'task-{day}-011', f'task-{day}-012']
    assert reserve_task_paths(root)[0].name == f'task-{day}-013'


def test_parallel_cache_directories_preserve_full_identity(tmp_path):
    def allocate(number):
        return cache_directory(tmp_path, 'batch', {'request': number}, 'group.json')
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(allocate, [0, 1, 2, 0, 3, 1]))
    assert paths[0] == paths[3] and paths[1] == paths[5]
    assert len(set(paths)) == 4
    assert {p.name for p in paths} == {f'batch_{n:02d}' for n in range(1, 5)}
    for number, path in zip([0, 1, 2, 0, 3, 1], paths):
        assert read(path/'group.json') == {'request': number}


def test_batch_recovers_reserved_names_after_dispatch_interruption(tmp_path, monkeypatch):
    service = SimpleNamespace(root=tmp_path/'service', guard=RLock(),
                              store=Store([tmp_path/'tasks']), creator=Mock())
    upload_id = 'batch-'+'a'*32
    upload_path = service.root/'uploads'/upload_id/'batch.json'
    files = []
    for index, role in enumerate(['reference', 'reference', 'material']):
        path = tmp_path/f'image-{index}.png'
        Image.new('RGB', (16, 16), (index, 0, 0)).save(path)
        files.append({'name': path.name, 'file': str(path), 'role': role,
                      'sha256': sha(path), 'bytes': path.stat().st_size})
    save(upload_path, {'id': upload_id, 'files': files, 'multiple_references': True, 'sealed': False})
    service.batch = lambda identifier: (upload_path, read(upload_path))
    dispatched = []

    def enqueue(job):
        dispatched.append(job)
        save(service.root/'jobs'/(job['id']+'.json'), {**job, 'status': 'queued'})

    service.enqueue = enqueue
    batches = Batches(service)
    payload = {'id': 'run-'+'b'*32, 'upload_id': upload_id, 'name': 'two references',
               'model_policy': Config().model_policy()}
    with monkeypatch.context() as patch:
        patch.setattr(batches, 'dispatch', Mock(side_effect=RuntimeError('interrupted')))
        with pytest.raises(RuntimeError, match='interrupted'):
            batches.submit(payload)
    committed = read(batches.path(payload['id']))
    paths = [Path(job['run']) for job in committed['jobs']]
    assert len(set(paths)) == 2 and not any(p.exists() for p in paths)
    assert [p.name[-3:] for p in paths] == ['001', '002']
    counter = read(tmp_path/'tasks/.task-names.json')
    recovered = Batches(service)
    assert [job['run'] for job in dispatched] == [str(p) for p in paths]
    assert [job['batch_run']['reference_name'] for job in dispatched] == [f['name'] for f in files[:2]]
    assert recovered.submit(payload)['total'] == 2
    assert len(dispatched) == 2
    assert read(tmp_path/'tasks/.task-names.json') == counter


@pytest.fixture
def extraction(tmp_path, monkeypatch):
    reference = tmp_path/'reference.png'
    Image.new('RGB', (240, 200), 'gray').save(reference)
    target = {'id': 'deco', 'kind': 'overlay', 'method': 'extract', 'bbox': [20, 20, 50, 50]}
    scene = {'reference': {'file': str(reference), 'sha256': sha(reference)},
             'reference_size': [240, 200], 'objects': [
                 {'id': 'bg', 'kind': 'background', 'background_type': 'reference_clean',
                  'bbox': [0, 0, 240, 200]},
                 {'id': 'photo', 'kind': 'photo', 'bbox': [60, 20, 100, 80]}, target]}
    calls = []

    def fetch(task, image, objects, key, timeout):
        calls.append(task)
        save(task/'request_meta.json', {'reference_sha256': sha(image), 'objects': objects,
                                       'model': 'ecommerce_layer', 'version': 'v1.1'})
        save(task/'query_response.json', {'status': 'done', 'task_id': 'offline-task',
             'output': {'boxes_mapping_index': list(range(len(objects))),
                        'image_boxes': [o['bbox'] for o in objects]}})
        with Image.open(image) as im:
            Image.new('RGB', im.size, 'white').save(task/'layers_aug_00.png')
            for index in range(1, len(objects)+1):
                Image.new('RGBA', im.size, (255, 0, 0, 255)).save(task/f'layers_aug_{index:02d}.png')

    monkeypatch.setattr(reveal, 'fetch_batch', fetch)
    monkeypatch.setattr(reveal, 'api_key', lambda _: 'offline')
    return scene, [target], calls


@pytest.mark.parametrize('layout', ['full', 'grouped', 'legacy'])
@pytest.mark.parametrize('old_names', [False, True])
def test_extraction_reuses_new_and_historical_cache(tmp_path, extraction, layout, old_names, monkeypatch):
    scene, targets, calls = extraction
    if layout != 'full':
        scene['objects'] = scene['objects'][1:]
    acquire = {'full': reveal_chain.acquire, 'grouped': reveal_grouped.acquire, 'legacy': reveal.acquire}[layout]
    run = tmp_path/'task'
    config = {'remote': True, 'layout': layout, 'max_requests': 8}
    found, receipts = acquire(run, scene, targets, config)
    assert 'deco' in found and all(not r.get('error') for r in receipts)
    count = 2 if layout == 'full' else 1
    assert len(calls) == count
    root = run/'assets/reveal'/{'full': 'chain', 'grouped': 'grouped', 'legacy': 'api'}[layout]
    prefix = 'round' if layout == 'full' else 'batch'
    paths = sorted(root.glob(prefix+'_*'))
    assert [p.name for p in paths] == [f'{prefix}_{n:02d}' for n in range(1, count+1)]
    if old_names:
        for path in paths:
            manifest = {'full': 'chain-input.json', 'grouped': 'group.json', 'legacy': 'batch-input.json'}[layout]
            identity = read(path/manifest)
            digest = fingerprint(identity)[:20]
            if layout == 'legacy':
                digest = fingerprint([identity['reference_sha256'], identity['objects']])[:16]
            old = root/(prefix+'_'+digest)
            assert path.resolve().is_relative_to(tmp_path.resolve())
            assert old.resolve().is_relative_to(tmp_path.resolve())
            path.rename(old)
            if layout == 'legacy':
                (old/manifest).unlink()  # Historical legacy caches have request_meta only.
    before = {p.name for p in root.glob(prefix+'_*')}
    monkeypatch.setattr(reveal, 'fetch_batch', lambda *args: pytest.fail('Resume submitted a new request'))
    resumed, receipts = acquire(run, scene, targets, {**config, 'remote': False})
    assert set(resumed) == set(found)
    assert all(not r.get('error') for r in receipts)
    assert all(Path(record['file']).is_file() for record in resumed.values())
    assert {p.name for p in root.glob(prefix+'_*')} == before


def test_chain_changed_request_does_not_reuse_previous_round(tmp_path, extraction):
    scene, targets, calls = extraction
    run = tmp_path/'task'
    config = {'remote': True, 'max_requests': 8}
    reveal_chain.acquire(run, scene, targets, config)
    old = read(run/'assets/reveal/chain/round_01/chain-input.json')
    changed = deepcopy(targets)
    changed[0]['bbox'] = [10, 10, 50, 50]
    found, receipts = reveal_chain.acquire(run, scene, changed, config)
    assert 'deco' in found and all(not r.get('error') for r in receipts)
    assert len(calls) == 4
    assert read(run/'assets/reveal/chain/round_01/chain-input.json') == old
    assert [Path(r['task']).name for r in receipts] == ['round_03', 'round_04']


@pytest.mark.parametrize('old_names', [False, True])
def test_grouped_external_cache_is_read_only(tmp_path, extraction, old_names, monkeypatch):
    scene, targets, calls = extraction
    scene['objects'] = scene['objects'][1:]
    source = tmp_path/'source'
    reveal_grouped.acquire(source, scene, targets, {'remote': True, 'layout': 'grouped'})
    cache = source/'assets/reveal'
    folder = cache/'grouped/batch_01'
    if old_names:
        destination = folder.with_name('batch_'+fingerprint(read(folder/'group.json'))[:20])
        assert folder.resolve().is_relative_to(tmp_path.resolve())
        assert destination.resolve().is_relative_to(tmp_path.resolve())
        folder.rename(destination)
    before = {str(p): sha(p) for p in cache.rglob('*') if p.is_file()}
    monkeypatch.setattr(reveal, 'fetch_batch', lambda *args: pytest.fail('External cache missed'))
    run = tmp_path/'task'
    found, receipts = reveal_grouped.acquire(run, scene, targets,
        {'remote': False, 'layout': 'grouped', 'cache': str(cache)})
    assert 'deco' in found and all(r['cache_hit'] for r in receipts)
    assert Path(found['deco']['file']).is_relative_to(run/'assets/reveal/grouped/batch_01')
    assert {str(p): sha(p) for p in cache.rglob('*') if p.is_file()} == before
