"""Short lived, bounded snapshots coalesce concurrent read-only UI polling."""
from collections import OrderedDict
import threading
import time


class ReadCache:
    def __init__(self, capacity=64):
        self.capacity = capacity
        self._entries = OrderedDict()
        self._lock = threading.Lock()
        self._workers = [threading.Lock() for _ in range(16)]

    def get(self, key, ttl, build):
        def cached():
            with self._lock:
                entry = self._entries.get(key)
                if entry and time.monotonic() - entry[0] < ttl:
                    self._entries.move_to_end(key)
                    return entry[1]
            return None
        value = cached()
        if value is not None:
            return value
        with self._workers[hash(key) % len(self._workers)]:
            value = cached()
            if value is not None:
                return value
            value = build()
            with self._lock:
                self._entries[key] = (time.monotonic(), value)
                self._entries.move_to_end(key)
                while len(self._entries) > self.capacity:
                    self._entries.popitem(last=False)
            return value
