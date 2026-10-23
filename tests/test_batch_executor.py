from threading import Event, Lock

import pytest

from workbench.server.batch_executor import BatchExecutor


def test_two_batches_overlap_but_a_batch_remains_serial():
    queue = BatchExecutor(2)
    release = Event()
    qwen_started, luna_started = Event(), Event()
    second_qwen = Event()
    order = []
    def first(name, started):
        order.append(name)
        started.set()
        assert release.wait(3)
    try:
        q1 = queue.submit('qwen', first, 'qwen-1', qwen_started)
        assert qwen_started.wait(2)
        q2 = queue.submit('qwen', lambda: (order.append('qwen-2'), second_qwen.set()))
        l1 = queue.submit('luna', first, 'luna-1', luna_started)
        assert luna_started.wait(2), 'Queued Qwen tasks must not occupy the second worker'
        assert not second_qwen.is_set()
        release.set()
        for future in [q1, q2, l1]:
            future.result(3)
        assert order.index('qwen-1') < order.index('qwen-2')
    finally:
        release.set(); queue.shutdown()


def test_failure_releases_batch_and_shutdown_drains_pending():
    queue = BatchExecutor(2)
    failed = queue.submit('qwen', lambda: 1/0)
    following = queue.submit('qwen', lambda: 'continued')
    other = queue.submit('luna', lambda: 'other')
    queue.shutdown()
    with pytest.raises(ZeroDivisionError):
        failed.result()
    assert following.result() == 'continued'
    assert other.result() == 'other'
    with pytest.raises(RuntimeError):
        queue.submit('qwen', lambda: None)


def test_global_concurrency_limit():
    queue = BatchExecutor(2)
    release = Event(); first_two = Event(); guard = Lock()
    active = peak = 0
    def work():
        nonlocal active, peak
        with guard:
            active += 1; peak = max(peak, active)
            if active == 2:
                first_two.set()
        assert release.wait(3)
        with guard:
            active -= 1
    try:
        futures = [queue.submit(str(i), work) for i in range(8)]
        assert first_two.wait(2)
        release.set()
        for future in futures:
            future.result(3)
        assert peak == 2
    finally:
        release.set(); queue.shutdown()
