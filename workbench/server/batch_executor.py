"""Run up to two batches concurrently, preserving serial order within each batch."""
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Condition


class BatchExecutor:
    def __init__(self, max_workers=2):
        self.limit = max_workers
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='live-create')
        self.condition = Condition()
        self.pending = deque()
        self.running = set()
        self.closed = False

    def submit(self, group, function, *args):
        future = Future()
        with self.condition:
            if self.closed:
                raise RuntimeError('Creation queue is closed')
            self.pending.append((group, future, function, args))
            self._dispatch()
        return future

    def _dispatch(self):
        while len(self.running) < self.limit:
            item = next((item for item in self.pending if item[0] not in self.running), None)
            if item is None:
                break
            self.pending.remove(item)
            self.running.add(item[0])
            self.pool.submit(self._run, *item)

    def _run(self, group, future, function, args):
        try:
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(function(*args))
                except BaseException as error:
                    future.set_exception(error)
        finally:
            with self.condition:
                self.running.remove(group)
                self._dispatch()
                self.condition.notify_all()

    def shutdown(self, wait=True):
        if not wait:
            raise ValueError('Creation queue must drain before shutdown')
        with self.condition:
            self.closed = True
            self.condition.wait_for(lambda: not self.pending and not self.running)
        self.pool.shutdown(wait=True)
