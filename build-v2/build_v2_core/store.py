"""A single atomic state commit; derived manifests can always be recovered."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from .common import BuildError, read_json


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(root):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    # OS lock releases on process death; no stale PID guessing or lock deletion.
    with (root / '.state.lock').open('a+b') as stream:
        if stream.tell() == 0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BuildError('write_conflict', 'Another command owns this run; retry after it finishes') from exc
        try: yield root
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def load(root):
    state = read_json(Path(root) / 'state.json')
    if state.get('schema_version') != 'build-state-v2':
        raise BuildError('state_version', 'Expected independent v2 state')
    return state


def commit(root, state, event, details=None):
    state['sequence'] = state.get('sequence', 0) + 1
    state.setdefault('events', []).append({'sequence': state['sequence'], 'at': now(), 'event': event, 'details': details or {}})
    atomic(Path(root) / 'state.json', state)
