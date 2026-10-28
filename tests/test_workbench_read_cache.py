from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from workbench.server.read_cache import ReadCache


def test_concurrent_readers_build_once_and_expired_values_refresh():
    cache = ReadCache()
    calls = []
    def build():
        calls.append(1)
        time.sleep(.02)
        return {'value': len(calls)}
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: cache.get('tasks', 10, build), range(8)))
    assert len(calls) == 1
    assert all(value == {'value': 1} for value in values)
    assert cache.get('tasks', 0, build) == {'value': 2}


def test_errors_retry_and_lru_capacity():
    cache = ReadCache(capacity=2)
    def fail():
        raise ValueError('temporary')
    with pytest.raises(ValueError):
        cache.get('a', 10, fail)
    assert cache.get('a', 10, lambda: []) == []
    cache.get('b', 10, lambda: [2])
    cache.get('c', 10, lambda: [3])
    assert cache.get('a', 10, lambda: [4]) == [4]
