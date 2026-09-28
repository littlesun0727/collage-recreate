"""Replacement copy is explicit provenance, never an OCR correction."""
from copy import deepcopy


def candidates(draft):
    result = {}
    for collection, kind, field in (('texts', 'text', 'default_text'),
                                    ('overlays', 'overlay', 'text_content')):
        for item in draft.get(collection, []):
            missing = not item.get(field)
            if kind == 'overlay':
                missing = missing and item.get('requires_exact_content') is True
            if missing:
                result[f'{kind}:{item["id"]}'] = (item, field)
    return result


def validate_records(rows, expected, allowed_statuses=('generated', 'user', 'unresolved')):
    if not isinstance(rows, list):
        raise ValueError('text_bindings must be an array')
    found = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'key', 'text', 'status', 'reason'}:
            raise ValueError('Text record requires only key, text, status, reason')
        key = row['key']
        if not isinstance(key, str) or key not in expected or key in found:
            raise ValueError('Unexpected or duplicate replacement text key')
        if row['status'] not in allowed_statuses:
            raise ValueError('Invalid replacement text status')
        if not isinstance(row['reason'], str) or not row['reason'].strip():
            raise ValueError('Replacement text requires a reason')
        if row['status'] == 'unresolved':
            if row['text'] is not None:
                raise ValueError('Unresolved text must be null')
        elif not isinstance(row['text'], str) or not row['text'].strip():
            raise ValueError('Resolved replacement text must be nonempty')
        found[key] = dict(row)
    if set(found) != set(expected):
        raise ValueError('Text records must cover every requested key exactly once')
    return found


def effective_draft(draft, rows):
    result = deepcopy(draft)
    wanted = candidates(result)
    records = validate_records(rows, wanted)
    for key, row in records.items():
        if row['status'] != 'unresolved':
            item, field = wanted[key]
            item[field] = row['text']
    return result
