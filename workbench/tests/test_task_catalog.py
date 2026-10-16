"""Curated demos keep stable task IDs and exclude removed tasks from all routes."""
import json
from pathlib import Path
import sys
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from workbench.server.store import Store
from workbench.server.http import create_server


def make_task(root, name, identifier, *, category=None, created='2026-10-16T00:00:00Z'):
    run = root / name
    run.mkdir(parents=True)
    (run / 'input.json').write_text(json.dumps({'created_at': created}), encoding='utf-8')
    (run / 'portable-task.json').write_text(json.dumps({'id': identifier}), encoding='utf-8')
    if category:
        (run / 'workbench-task.json').write_text(json.dumps({'category': category}), encoding='utf-8')
    return run


def catalog():
    return {'groups': [{'id': 'collage-current', 'label': '本次拼贴类测试'},
                       {'id': 'history', 'label': '历史测试'}],
            'default_group': 'history', 'excluded_categories': ['creative'],
            'excluded_tasks': ['4444444444444444'],
            'tasks': {'1111111111111111': {'group': 'collage-current', 'reference_name': 'first.jpg', 'order': 1},
                      '2222222222222222': {'group': 'collage-current', 'reference_name': 'second.jpg', 'order': 2}}}


def test_catalog_orders_current_batch_and_keeps_unknown_history(tmp_path):
    first = make_task(tmp_path, 'first', '1111111111111111', created='2026-10-01T00:00:00Z')
    make_task(tmp_path, 'second', '2222222222222222')
    make_task(tmp_path, 'previous', '3333333333333333')
    before = {p: p.read_bytes() for p in first.iterdir()}
    for _ in range(2):
        listing = Store([tmp_path], task_catalog=catalog()).listing()
        assert [row['id'] for row in listing] == ['1111111111111111', '2222222222222222', '3333333333333333']
        assert [row['group'] for row in listing] == ['collage-current', 'collage-current', 'history']
        assert listing[0]['reference_name'] == 'first.jpg'
        assert listing[2]['group_label'] == '历史测试'
    assert {p: p.read_bytes() for p in first.iterdir()} == before


def test_exclusion_covers_known_ids_and_creative_metadata(tmp_path):
    make_task(tmp_path, 'known-removed', '4444444444444444')
    make_task(tmp_path, 'other-creative', '5555555555555555', category='creative')
    make_task(tmp_path, 'kept', '3333333333333333')
    store = Store([tmp_path], task_catalog=catalog())
    assert set(store.tasks()) == {'3333333333333333'}
    for identifier in ['4444444444444444', '5555555555555555']:
        with pytest.raises(KeyError):
            store.document(identifier)


def test_regular_workbench_without_catalog_keeps_existing_behavior(tmp_path):
    make_task(tmp_path, 'creative', '5555555555555555', category='creative')
    listing = Store([tmp_path]).listing()
    assert listing[0]['id'] == '5555555555555555'
    assert 'group' not in listing[0]


def test_removed_task_cannot_be_opened_by_bookmark(tmp_path):
    make_task(tmp_path, 'removed', '4444444444444444')
    server = create_server([tmp_path], 0, task_catalog=catalog())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base + '/api/tasks') as response:
            assert json.load(response) == []
        with pytest.raises(HTTPError) as error:
            urlopen(base + '/api/tasks/4444444444444444')
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
