import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value, *, indent=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(uuid4().hex + ".tmp")
    content = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=indent)
    temp.write_text(content + ("\n" if indent is not None else ""), encoding="utf-8")
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()
