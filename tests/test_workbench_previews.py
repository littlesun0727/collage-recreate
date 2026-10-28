import gzip
import http.client
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
import pytest

from workbench.server.http import create_server
from workbench.server.store import Store
from workbench.server.previews import PreviewCache


def test_batch_listing_scans_task_directories_once(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from workbench.server.batches import Batches
    batches = Batches.__new__(Batches)
    batches.root = tmp_path
    store = SimpleNamespace(tasks=Mock(return_value={}))
    batches.service = SimpleNamespace(store=store, root=tmp_path)
    for index in range(3):
        record = dict(id=str(index), name='test', created_at=str(index), model_policy={},
                      dataset_sha256='test', width=1200, instructions='', jobs=[], references=[], materials=[])
        (tmp_path/f'run-{index}.json').write_text(json.dumps(record))
    assert len(batches.listing()) == 3
    store.tasks.assert_called_once()


@pytest.fixture
def task(tmp_path):
    run = tmp_path/'task'
    run.mkdir()
    (run/'input.json').write_text('{"reference_size": [1800, 1200]}')
    image = Image.effect_noise((1800, 1200), 64).convert('RGBA')
    image.putalpha(180)
    image.save(run/'final.png')
    return run


def test_preview_size_alpha_original_and_invalidation(task, tmp_path):
    store = Store([task], preview_root=tmp_path/'cache')
    key = next(iter(store.tasks()))
    token = store.image(key, task, 'final.png').split('/')[-1]
    original = (task/'final.png').read_bytes()
    body, mime = store.artifact(key, token, 480)
    assert mime == 'image/webp'
    with Image.open(io.BytesIO(body)) as image:
        assert image.size == (480, 320)
        assert image.mode == 'RGBA'
    assert len(body) < len(original)/5
    assert store.artifact(key, token)[0] == original
    # Restart reuses persisted previews without re-encoding the source.
    restarted = Store([task], preview_root=tmp_path/'cache')
    assert restarted.artifact(key, token, 480)[0] == body
    Image.new('RGB', (1800, 1200), 'red').save(task/'final.png')
    with pytest.raises(KeyError):
        store.artifact(key, token, 480)
    new = store.image(key, task, 'final.png').split('/')[-1]
    assert new != token
    assert store.artifact(key, new, 480)[0] != body


def test_known_task_detail_does_not_rescan_all_tasks(task, monkeypatch):
    store = Store([task])
    key = next(iter(store.tasks()))
    monkeypatch.setattr(store, 'tasks', lambda: pytest.fail('Unnecessary full task scan'))
    assert store.document(key)['id'] == key
    (task/'input.json').unlink()
    with pytest.raises(KeyError):
        store.document(key)


def test_concurrent_cache_reuse_crop_and_disk_failure(task, tmp_path, monkeypatch):
    cache = PreviewCache(tmp_path/'cache')
    def get():
        return cache.get('example', task/'final.png', (0, 0, 900, 900), 480)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: get(), range(6)))
    assert all(body == results[0] for body in results)
    assert len(list(cache.root.glob('*.webp'))) == 1
    assert Image.open(io.BytesIO(results[0])).size == (480, 480)
    import workbench.server.previews as previews
    monkeypatch.setattr(previews.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
    assert cache.get('another', task/'final.png', None, 480)
    assert not list(cache.root.glob('*.tmp'))


def test_cache_prunes_and_rebuilds(task, tmp_path):
    cache = PreviewCache(tmp_path/'cache', max_bytes=1)
    body = cache.get('example', task/'final.png', None, 480)
    assert not list(cache.root.glob('*.webp'))
    assert cache.get('example', task/'final.png', None, 480) == body


def test_http_preview_download_gzip_and_security(task, tmp_path):
    server = create_server([task], port=0, media_root=tmp_path/'service')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def get(path, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        conn.request('GET', path, headers=headers or {})
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result
    try:
        _, _, listing = get('/api/tasks')
        url = json.loads(listing)[0]['thumbnail']
        status, headers, body = get(url+'?size=480')
        assert status == 200 and headers['Content-Type'] == 'image/webp'
        assert 'immutable' in headers['Cache-Control']
        assert get(url+'?size=1280')[0] == 200
        status, _, icon = get(url+'?size=160')
        assert status == 200
        assert max(Image.open(io.BytesIO(icon)).size) == 160
        assert get(url+'?size=9999')[0] == 400
        assert get(url+'?size=480&download=1')[2] == (task/'final.png').read_bytes()
        status, headers, body = get('/', {'Accept-Encoding': 'gzip'})
        assert status == 200 and headers['Content-Encoding'] == 'gzip'
        assert gzip.decompress(body) == get('/')[2]
        assert b'revealAsset-architecture' not in gzip.decompress(body)
        assert 'Content-Encoding' not in get('/', {'Accept-Encoding': 'gzip;q=0'})[1]
        assert get('/reveal-data.png')[0] == 200
        assert get(url, {'Host': 'evil.example'})[0] == 403
        assert get('/api/tasks')[1]['Cache-Control'] == 'no-store'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
