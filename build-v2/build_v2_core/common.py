"""Small local IO helpers. No provider configuration or credentials in artifacts."""
import hashlib
import json
import math
from pathlib import Path
import re

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'build-v2'


class BuildError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def read_json(path):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise BuildError('invalid_json', f'Duplicate key: {key}')
            result[key] = value
        return result
    def constant(value):
        raise BuildError('invalid_json', f'Non-finite JSON value: {value}')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=pairs, parse_constant=constant)


def model_json(path):
    text = Path(path).read_text(encoding='utf-8-sig').strip()
    fence = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', text, re.S)
    if fence:
        text = fence.group(1)
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise BuildError('invalid_json', f'Duplicate key: {key}')
            result[key] = value
        return result
    def constant(value):
        raise BuildError('invalid_json', f'Non-finite JSON value: {value}')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def oriented(path, mode='RGBA'):
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert(mode)


def number(value, label, low=0, high=1):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise BuildError('invalid_parameter', f'{label}: expected number in [{low}, {high}]')
    return value


def text_value(value, label):
    if not isinstance(value, str) or not value.strip():
        raise BuildError('invalid_parameter', f'{label}: expected nonempty text')
    return value


def fields(value, required, optional=(), label='object'):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise BuildError('invalid_fields', f'{label}: required={sorted(required)}, optional={sorted(optional)}')


def box(value, label='box', limit=1):
    if not isinstance(value, list) or len(value) != 4:
        raise BuildError('invalid_parameter', f'{label}: expected xyxy')
    for v in value:
        number(v, label, 0, limit)
    if value[0] >= value[2] or value[1] >= value[3]:
        raise BuildError('invalid_parameter', f'{label}: empty box')
    return value


def relative_file(root, name):
    if not isinstance(name, str):
        raise BuildError('invalid_path', 'Artifact path must be text')
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise BuildError('invalid_path', f'Missing or escaping artifact: {name}')
    return path
