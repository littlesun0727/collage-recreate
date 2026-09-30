"""Small deterministic IO helpers. No model calls."""
import hashlib
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
deps = ROOT / ('.deps314' if sys.version_info[:2] == (3,14) else '.deps')
if deps.is_dir():
    sys.path.insert(0, str(deps))


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique,
                      parse_constant=lambda s: (_ for _ in ()).throw(ValueError(f'Invalid number: {s}')))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def event(run, name, **data):
    path = Path(run) / 'events.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'at': now(), 'event': name, **data}, ensure_ascii=False) + '\n')


@contextmanager
def timed(run, name):
    started = time.monotonic()
    event(run, name + '_started')
    try:
        yield
    except Exception as exc:
        event(run, name + '_failed', elapsed_seconds=round(time.monotonic()-started, 3), error=str(exc))
        raise
    else:
        event(run, name + '_finished', elapsed_seconds=round(time.monotonic()-started, 3))


@contextmanager
def locked(run):
    """Only one writer per task; never silently remove a stale lock."""
    path = Path(run) / '.write.lock'
    with path.open('x', encoding='utf-8') as f:
        f.write(json.dumps({'pid': os.getpid(), 'at': now()}))
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def verify_source(record):
    p = Path(record['file'])
    if not p.is_file() or sha(p) != record['sha256']:
        raise ValueError(f'Source changed or missing: {p}')
    return p


def inside(run, relative):
    root = Path(run).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Output escapes task directory')
    return path
