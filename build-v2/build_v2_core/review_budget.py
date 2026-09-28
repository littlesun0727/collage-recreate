"""Visual rounds are separate from technical failures and survive plan revisions."""
from .common import BuildError


def rounds_used(state, ident):
    row = state['tasks'][ident]
    members = set(row['package'].get('member_keys', row['package']['owned_keys']))
    counts = dict.fromkeys(members, 0)
    seen = set()
    for event in state['events']:
        if event['event'] != 'visual_review':
            continue
        data = event['details']
        affected = set(data.get('member_keys', members if data.get('task') == ident else [])) & members
        token = (data.get('task'), data.get('submission_id', event.get('sequence')),
                 data.get('context_id'))
        if not affected or token in seen:
            continue
        seen.add(token)
        for key in affected:
            counts[key] += 1
    return max(counts.values(), default=0)


def budget(state, ident):
    used = rounds_used(state, ident)
    limit = 1 + state['limits'].get('max_corrections', 2)
    return {'completed_reviews': used, 'max_reviews': limit,
            'corrections_remaining': max(0, limit - max(1, used))}


def ensure_remaining(state, ident):
    info = budget(state, ident)
    if info['completed_reviews'] >= info['max_reviews']:
        raise BuildError('visual_budget_exhausted',
                         'Visual correction limit reached; continue to renders with recorded issues')
