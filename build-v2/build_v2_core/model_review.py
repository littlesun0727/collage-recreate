"""One explicit visual-model review; code owns receipt metadata and registration."""
from copy import deepcopy
from pathlib import Path
import json
import time
import uuid

from .common import BuildError, fields, read_json, sha
from .inspection import preview
from .planner import request, section
from .store import atomic, commit, load, locked
from .workflow import context_id, export, refresh, review, task_for
from .review_budget import ensure_remaining
from .review_selection import select_review


def review_with_model(args):
    root = Path(args.run).resolve()
    # Separate OS lock avoids duplicate paid reviews while allowing normal status reads.
    with locked(root / 'model-reviews'):
        return _review_with_model(root, args)


def _review_with_model(root, args):
    started = time.monotonic()
    reviewer = 'model:' + args.provider + '/' + args.model
    with locked(root):
        state = load(root); refresh(root, state); row = task_for(state, args.task)
        if row['status'] not in ('awaiting_visual', 'needs_changes', 'accepted', 'deferred'):
            raise BuildError('review_state', 'Submit a valid current task version before review')
        previous = row.get('model_review')
        if (previous and row.get('review') and row['review']['reviewer'] == reviewer
                and previous['submission_id'] == row['active']['id']
                and previous['context_id'] == context_id(state)):
            report_path = root / previous['report']
            if report_path.is_file() and sha(report_path) == previous['sha256']:
                return {'status': row['status'], 'issues': row['review']['effective_issues'],
                        'report': str(report_path), 'reused': True, 'model_calls': 0}
        ensure_remaining(state, args.task)
        attempts = sum(e['event'] == 'model_review_failed' and e['details'].get('task') == args.task for e in state['events'])
        if attempts >= state['limits']['max_attempts']:
            raise BuildError('attempt_limit', 'Model review technical failure budget exhausted; inspect evidence')
    evidence = preview(root, args.task)
    with locked(root):
        state = load(root); refresh(root, state); row = task_for(state, args.task)
        if (row['status'] not in ('awaiting_visual', 'needs_changes', 'accepted')
                or context_id(state) != evidence['context_id']
                or row['active']['id'] != evidence['submission_id']):
            raise BuildError('stale_preview', 'Task changed during preview preparation')
        package = deepcopy(row['package'])
        required, reused = select_review(state, row)
        reference = root / state['snapshot'] / 'inputs/reference.png'
        delivery = read_json(root / row['active']['delivery'])
        metadata = {'schema_version': 'build-review-v2', 'task_id': args.task,
                    'submission_id': evidence['submission_id'], 'context_id': evidence['context_id'],
                    'reviewer': reviewer, 'evidence': evidence['evidence']}
        data = {'required_keys': required, 'objects': [o for o in package['objects'] if o['key'] in required],
                'unchanged_passes_reused': list(reused),
                'previous_issues': [r for r in row.get('review_cache', {}).get('receipt', {}).get('reviews', [])
                                    if r['key'] in required and r['status'] != 'passed'],
                'neighbors_are_context_only': True, 'canvas_size': package['canvas_size'],
                'actual_outputs': [o for o in row['active']['outputs'] if o['key'] in required], 'approximations': delivery['approximations'],
                'unresolved': delivery['unresolved']}
        folder = root / 'model-reviews' / uuid.uuid4().hex
        folder.mkdir()
        atomic(folder / 'context.json', metadata)
        atomic(folder / 'selection.json', {'reviewed_keys': required, 'reused_keys': list(reused)})
        commit(root, state, 'model_review_started', {'task': args.task, 'folder': str(folder.relative_to(root)),
                                                    'model': reviewer, 'dry_run': args.dry_run})
    try:
        images = [{'path': str(reference), 'label': '完整参考图；仅评 required_keys 所列对象。'}]
        for path, digest in evidence['evidence'].items():
            if Path(path).name != 'combined.png' and Path(path).stem not in {k.replace(':', '__') for k in required}:
                continue
            if sha(root / path) != digest:
                raise BuildError('stale_preview', 'Preview changed before model dispatch')
            images.append({'path': str(root / path),
                           'label': '实际提交的组合对照；照片为占位。' if Path(path).name == 'combined.png'
                           else '实际提交的单件对照：左参考，右产物。' + Path(path).stem})
        answer = request(section('review') + '\n\n任务数据：\n' + json.dumps(data, ensure_ascii=False),
                         images, folder / 'model', args, args.dry_run, task='review')
        if args.dry_run:
            result = {'status': 'dry_run_verified', 'model_calls': 0, 'evidence': str(folder)}
        else:
            fields(answer, {'reviews', 'combination_issues'}, label='model visual judgment')
            if len(answer['reviews']) != len(required) or {r.get('key') for r in answer['reviews']} != set(required):
                raise BuildError('review_coverage', 'Model must review exactly the requested changed/affected units')
            merged = {**reused, **{r['key']: r for r in answer['reviews']}}
            answer['reviews'] = [merged[k] for k in package['owned_keys']]
            receipt = {**metadata, **answer, 'elapsed_seconds': time.monotonic() - started}
            report = folder / 'review.json'; atomic(report, receipt)
            result = {**review(root, args.task, report), 'report': str(report), 'reused': False, 'model_calls': 1}
            result.update(reviewed_keys=required, reused_keys=list(reused), image_count=len(images))
            with locked(root):
                state = load(root); row = task_for(state, args.task)
                if row.get('review') == {**receipt, 'effective_issues': result['issues'], 'effective_status': result['status']}:
                    row['model_review'] = {'report': str(report.relative_to(root)), 'sha256': sha(report),
                                           'submission_id': metadata['submission_id'], 'context_id': metadata['context_id']}
                commit(root, state, 'model_review_finished', {'task': args.task, **result})
                export(root, state)
        atomic(folder / 'result.json', result)
        return result
    except Exception as exc:
        with locked(root):
            state = load(root)
            commit(root, state, 'model_review_failed', {'task': args.task, 'code': getattr(exc, 'code', 'model_review_failed'),
                                                       'elapsed_seconds': time.monotonic() - started})
            export(root, state)
        raise
