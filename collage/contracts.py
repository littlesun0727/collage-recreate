import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRING = {'type': 'string'}


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties,
            'required': list(properties) if required is None else required, 'additionalProperties': False}


def array(items):
    return {'type': 'array', 'items': items}


def load(name):
    return json.loads((ROOT/'contracts'/f'{name}.json').read_text(encoding='utf-8'))

ANALYSIS = load('analysis')
BINDING_PLAN = load('binding-plan')
STYLE = ANALYSIS['properties']['objects']['items']['properties']['style']
