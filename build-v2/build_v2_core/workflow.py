"""Local receipts and verification. No autonomous production or visual decisions."""
from copy import deepcopy
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import sys
import time
import uuid

from PIL import Image

from .common import BuildError, ROOT, fields, fingerprint, read_json, relative_file, sha
from .content_scope import check_scope_review
from .contracts import merge_batches, strings, tasks_for, validate_plan
from .drawing import inspect_resource
from .fonts import discover
from .inputs import load_inputs
from .planner import plan_batch
from .script_api import reproduce_text
from .store import atomic, commit, load, locked, now
from .review_budget import budget, ensure_remaining


def engine_signature():
    return fingerprint({str(p.relative_to(ROOT)).replace('\\', '/'): sha(p)
                        for directory in (ROOT / 'build_v2_core', ROOT / 'prompts')
                        for p in directory.rglob('*') if p.is_file() and p.suffix in ('.py', '.md')})


def environment():
    packages = {}
    for name in ('Pillow', 'numpy', 'fonttools', 'rembg', 'pymatting'):
        try: packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: packages[name] = None
    return {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
            'packages': packages, 'core_path': str(Path(__file__).resolve()), 'node': shutil.which('node')}


def plan_run(args):
    started = time.monotonic()
    inputs = load_inputs(args.draft, args.reference, args.bindings, args.draft_version, args.width, args.objects)
    fonts = discover(args.font_dir)
    output = Path(args.output).resolve()
    with locked(output):
        previous = load(output) if (output / 'state.json').exists() else None
        if previous and not args.revise:
            raise BuildError('existing_run', 'Use status/resume, or --revise with an explicit reason')
        if args.revise and not args.reason:
            raise BuildError('revision_reason', '--revise requires --reason')
        attempt = output / 'planning' / uuid.uuid4().hex
        attempt.mkdir(parents=True)
        if getattr(args, 'unit_plan', None):
            raw_plan = read_json(args.unit_plan)
            atomic(attempt / 'offline-source.json', {'path':str(args.unit_plan.resolve()),'sha256':sha(args.unit_plan)})
        elif args.plan:
            raw_plan = read_json(args.plan)
            atomic(attempt / 'offline-source.json', {'path': str(Path(args.plan).resolve()), 'sha256': sha(args.plan)})
        else:
            batches = []
            for index in range(0, len(inputs['selected']), args.batch_size):
                batch = plan_batch(inputs, inputs['selected'][index:index+args.batch_size], fonts,
                                   attempt / f'batch-{index//args.batch_size+1:03d}', args, args.dry_run)
                if batch is not None:
                    merge_batches([batch], inputs['selected'][index:index+args.batch_size])
                    batches.append(batch)
            if args.dry_run:
                receipt = {'status': 'dry_run_verified', 'remote_calls': 0, 'output': str(attempt),
                           'elapsed_seconds': time.monotonic()-started}
                atomic(attempt / 'result.json', receipt); return receipt
            raw_plan = merge_batches(batches, inputs['selected'])
        if getattr(args, 'unit_plan', None):
            if args.plan or args.dry_run: raise BuildError('unit_mode','--unit-plan cannot combine with --plan/--dry-run')
            from .unit_execution import compile_units
            plan, tasks = compile_units(raw_plan, inputs, fonts)
        else:
            plan = validate_plan(raw_plan, inputs, fonts)
            tasks = tasks_for(plan, inputs, fonts)
        revision = fingerprint({'plan': plan, 'input': inputs['info'], 'engine': engine_signature()})
        # Inputs are snapshots; original hashes remain recorded for explicit change detection.
        snapshot = output / 'revisions' / revision
        snapshot.mkdir(parents=True, exist_ok=True)
        inputs_dir = snapshot / 'inputs'; inputs_dir.mkdir(exist_ok=True)
        inputs['reference'].save(inputs_dir / 'reference.png')
        atomic(snapshot / 'build-plan.json', plan)
        atomic(snapshot / 'draft.json', inputs['draft'])
        atomic(snapshot / 'input.json', inputs['info'])
        for key in inputs['selected']:
            inputs['reference'].crop(inputs['entries'][key]['reference_box']).save(inputs_dir / (key.replace(':', '__') + '.png'))
        for task in tasks:
            for obj in task['objects']:
                inputs['reference'].crop(obj['reference_box']).save(snapshot/obj['crop'])
        state = {'schema_version': 'build-state-v2', 'revision': revision, 'plan': plan,
                 'input': inputs['info'], 'snapshot': str(snapshot.relative_to(output)),
                 'all_entries': inputs['entries'], 'passthrough': inputs['passthrough'], 'draft': inputs['draft'],
                 'selected': inputs['selected'], 'tasks': {}, 'engine': engine_signature(), 'environment': environment(),
                 'limits': {'max_attempts': args.max_attempts, 'max_seconds': args.max_run_seconds,
                            'max_corrections': (previous or {}).get('limits', {}).get('max_corrections', getattr(args, 'max_corrections', 2))},
                 'events': previous['events'] if previous else [], 'sequence': previous['sequence'] if previous else 0,
                 'previous_revision': previous['revision'] if previous else None}
        for task in tasks:
            old = (previous or {}).get('tasks', {}).get(task['id'])
            if old and old['package']['revision'] == task['revision'] and previous['engine'] == state['engine']:
                row = deepcopy(old)
                if row.get('active') and (row['status'] in ('stale', 'machine_failed') or previous['revision'] != revision):
                    row['status'] = 'awaiting_visual'; row['review'] = None; row.pop('stale_reasons', None)
            else:
                row = {'package': task, 'status': 'blocked' if any(o['plan']['method']=='blocked' for o in task['objects']) else 'pending',
                       'attempts': deepcopy(old['attempts']) if old and old['package']['revision']==task['revision'] else [],
                       'active': None, 'review': None}
            state['tasks'][task['id']] = row
            atomic(snapshot / 'tasks' / (task['id']+'.json'), task)
        state['planning'] = {'elapsed_seconds': time.monotonic()-started, 'evidence': str(attempt.relative_to(output)),
                             'source': 'unit-plan' if getattr(args,'unit_plan',None) else ('offline-plan' if args.plan else 'qwen'), 'reason': args.reason}
        refresh(output, state)
        commit(output, state, 'plan_revision', {'reason': args.reason, 'revision': revision})
        export(output, state)
        return summary(state)


def summary(state):
    statuses = {key: row['status'] for key, row in state['tasks'].items()}
    return {'revision': state['revision'], 'tasks': statuses,
            'visual_budget': {key: budget(state, key) for key in statuses},
            'next_actions': {key: {'pending': 'produce and submit', 'machine_failed': 'correct delivery and resubmit',
                                  'awaiting_visual': 'preview, inspect images, submit review', 'needs_changes': 'correct and resubmit',
                                  'accepted': 'reuse', 'deferred': 'continue to renders with recorded issues',
                                  'blocked': 'resolve recorded gap', 'stale': 'revise plan or resubmit changed dependencies'}[status]
                             for key, status in statuses.items()}}


def input_changes(state):
    changes = []
    for path_name, hash_name in [('draft_path', 'draft_sha256'), ('reference_path', 'reference_sha256'), ('bindings_path', 'bindings_sha256')]:
        path, digest = state['input'].get(path_name), state['input'].get(hash_name)
        if path and (not Path(path).is_file() or sha(path) != digest): changes.append(path_name)
    return changes


def refresh(root, state):
    """Every read that can authorize reuse checks current evidence, not a passed flag."""
    upstream = input_changes(state)
    changed = []
    for ident, row in state['tasks'].items():
        issues = []
        if upstream: issues += ['upstream_changed:' + key for key in upstream]
        if engine_signature() != state['engine']: issues.append('engine_changed')
        for obj in row['package']['objects']:
            source = obj.get('source_asset')
            if source and (not Path(source['path']).is_file() or sha(source['path']) != source['sha256']): issues.append('customer_source_changed')
        active = row.get('active')
        if active:
            current_packages = environment()['packages']
            if any(current_packages.get(k) != v for k, v in active.get('runtime_packages', {}).items()):
                issues.append('runtime_dependency_changed')
            for dep in active['dependencies']:
                path = Path(dep['path']) if dep.get('external') else root / dep['path']
                if not path.is_file() or sha(path) != dep['sha256']: issues.append('dependency_changed:' + dep['path'])
        if issues:
            if row['status'] != 'stale' or row.get('stale_reasons') != issues: changed.append(ident)
            row['status'] = 'stale'; row['stale_reasons'] = issues; row['review'] = None
    # Accepted groups sharing explicit context must be rechecked if their dependency changes.
    changed_keys = {key for row in state['tasks'].values() if row['status'] == 'stale' for key in row['package']['owned_keys']}
    for ident, row in state['tasks'].items():
        if set(row['package']['dependency_keys']) & changed_keys and row['status'] != 'stale':
            row['status'] = 'stale'; row['review'] = None; row['stale_reasons'] = ['related_context_changed']; changed.append(ident)
    return changed


def export(root, state):
    assets = []; accepted_keys = set()
    for ident, row in state['tasks'].items():
        if row.get('active'):
            for asset in row['active']['outputs']:
                obj=next(o for o in row['package']['objects'] if o['key']==asset['key'])
                if 'member_keys' in obj:
                    asset={**asset,'member_keys':obj['member_keys'],'method':obj['plan']['method'], 'layer_index':obj['layer_index']}
                assets.append({**asset, 'task_id': ident, 'status': row['status'], 'submission_id': row['active']['id'],
                               'visual_review': row.get('review')})
                if row['status'] == 'accepted': accepted_keys.update(asset.get('member_keys',[asset['key']]))
    selected = set(state['selected']); all_keys = set(state['all_entries'])
    binding_complete = all(e.get('source_asset') for e in state['passthrough'])
    ready = (bool(selected) or not all_keys) and accepted_keys == selected
    result = {**summary(state), 'schema_version': 'build-result-v2', 'scope_usable': ready,
              'all_build_resources_accepted': ready and selected == all_keys,
              'template_ready': False, 'renders_verified': False, 'photo_bindings_complete': binding_complete,
              'unselected_keys': sorted(all_keys-selected), 'planning': state['planning'],
              'environment': state['environment'], 'events': state['events'],
              'limitation': 'Inspection previews and text parameters are not a final render or native editable text layers.'}
    atomic(root / 'build-plan.json', state['plan'])
    atomic(root / 'assets.json', {'schema_version': 'build-unit-assets-v1' if state['plan'].get('units') is not None else 'build-assets-v2', 'revision': state['revision'], 'assets': assets,
                                 'passthrough': state['passthrough']})
    atomic(root / 'result.json', result)
    for ident, row in state['tasks'].items():
        atomic(root / 'tasks' / (ident + '.json'), {**row['package'], 'input_root': str(root/state['snapshot'])})
    return result


def status(root):
    with locked(root) as root:
        state = load(root); changed = refresh(root, state)
        if changed: commit(root, state, 'invalidated', {'tasks': changed})
        return export(root, state)


def task_for(state, ident, revision=None):
    if ident not in state['tasks']: raise BuildError('unknown_task', 'Task does not belong to this run')
    row = state['tasks'][ident]
    if revision and revision != row['package']['revision']:
        raise BuildError('task_conflict', 'Delivery targets a different task revision')
    return row


def check_delivery(root, state, row, delivery_path):
    delivery_path = Path(delivery_path).resolve()
    # Task code may only deliver inside this task's work area.
    workspace = root / 'work' / row['package']['id']
    if not delivery_path.is_relative_to(workspace) or not delivery_path.is_file():
        raise BuildError('invalid_path', 'Delivery must live in its task work directory')
    delivery = read_json(delivery_path)
    fields(delivery, {'schema_version', 'task_id', 'task_revision', 'script', 'outputs', 'parameters',
                      'seed', 'approximations', 'unresolved', 'timing'}, {'dependencies', 'reason'}, 'delivery')
    if delivery['schema_version'] != 'build-delivery-v2' or delivery['task_id'] != row['package']['id']:
        raise BuildError('delivery_version', 'Wrong delivery contract or owner')
    task_for(state, delivery['task_id'], delivery['task_revision'])
    strings(delivery['approximations'], 'approximations'); strings(delivery['unresolved'], 'unresolved')
    if not isinstance(delivery['parameters'], dict): raise BuildError('parameters', 'Actual parameters required')
    if not isinstance(delivery['timing'], dict) or any(type(v) not in (float, int) or v < 0 for v in delivery['timing'].values()):
        raise BuildError('timing', 'Nonnegative phase timings required')
    objects = {o['key']: o for o in row['package']['objects']}
    outputs = delivery['outputs']
    if not isinstance(outputs, list) or len(outputs) != len(objects) or {o.get('key') for o in outputs} != set(objects):
        raise BuildError('partial_delivery', 'An atomic task version must cover all owned objects exactly once')
    files = {}; checks = []; dependencies = []
    script = relative_file(workspace, delivery['script'])
    files['script'] = script
    for name in delivery.get('dependencies', []):
        file = relative_file(workspace, name); files['dependency:'+name] = file
    paths = set()
    for output in outputs:
        fields(output, {'key', 'file', 'raster_size', 'target_box'}, {'text', 'masks', 'source', 'paint_box', 'tool_receipt'}, 'output')
        obj = objects[output['key']]
        file = relative_file(workspace, output['file'])
        if file in paths or file == script: raise BuildError('output_conflict', 'Each owned object or production unit requires its own file')
        paths.add(file)
        if 'source' in output:
            source = output['source']; fields(source, {'path', 'sha256'}, label='explicit import source')
            if not Path(source['path']).is_file() or sha(source['path']) != source['sha256'] or sha(file) != source['sha256']:
                raise BuildError('import_source', 'Explicit import must preserve source bytes and digest')
            dependencies.append({'path': str(Path(source['path']).resolve()), 'sha256': source['sha256'], 'external': True})
        if obj.get('unit_id') and obj['plan']['method']=='generate':
            receipt_path=relative_file(workspace, output.get('tool_receipt',''))
            receipt=read_json(receipt_path)
            if (receipt.get('status')!='produced_unreviewed' or receipt.get('output',{}).get('key')!=obj['key']
                    or receipt.get('sha256')!=sha(file)):
                raise BuildError('generation_receipt','Unit generation requires the actual tool receipt and unchanged pixels')
            files['tool_receipt:'+obj['key']]=receipt_path
        if output['target_box'] != obj['target_box']:
            raise BuildError('mapping_changed', 'Declared target bbox differs from input')
        paint_box = output.get('paint_box', output['target_box'])
        if 'paint_box' in output:
            original = obj['target_box']
            if obj['plan']['method'] != 'draw':
                raise BuildError('mapping_changed', 'Expanded paint_box is for draw outputs only')
            if (not isinstance(paint_box, list) or len(paint_box) != 4
                    or any(type(v) is not int for v in paint_box)
                    or paint_box[2] <= paint_box[0] or paint_box[3] <= paint_box[1]
                    or paint_box[0] > original[0] or paint_box[1] > original[1]
                    or paint_box[2] < original[2] or paint_box[3] < original[3]):
                raise BuildError('mapping_changed', 'paint_box must contain the original target_box')
        size = output['raster_size']
        if (not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v < 1 or v > 16384 for v in size)
                or size[0]*size[1] > 40000000): raise BuildError('invalid_size', 'Invalid raster size')
        if obj['plan']['method'] in ('draw', 'text', 'feather') and (size[0]*(paint_box[3]-paint_box[1]) != size[1]*(paint_box[2]-paint_box[0])):
            raise BuildError('mapping_changed', 'Raster resolution must preserve layout aspect ratio')
        with Image.open(file) as image:
            if image.format != 'PNG' or image.mode != 'RGBA' or list(image.size) != size:
                raise BuildError('invalid_image', 'Delivery must be RGBA PNG at declared dimensions')
            image.load(); check = inspect_resource(image)
            if obj['type'] != 'background' and check['alpha_extrema'][0] == 255:
                raise BuildError('opaque_asset', 'Independent resource requires transparent pixels')
            for mask_spec in output.get('masks', []):
                fields(mask_spec, {'file', 'slot_id', 'max_alpha'}, label='mask')
                if type(mask_spec['max_alpha']) is not int or not 0 <= mask_spec['max_alpha'] <= 8:
                    raise BuildError('mask_alpha', 'Photo-window alpha threshold must be 0..8')
                if mask_spec['slot_id'] not in {v['id'] for v in state['draft']['slots']}:
                    raise BuildError('mask_slot', 'Unknown photo slot')
                mask_path = relative_file(workspace, mask_spec['file'])
                with Image.open(mask_path) as mask:
                    if mask.size != image.size or mask.mode != 'L': raise BuildError('mask_size', 'Mask dimensions/mode differ')
                    import numpy as np
                    core = np.asarray(mask) > 250
                    if not core.any() or np.asarray(image.getchannel('A'))[core].max() > mask_spec['max_alpha']:
                        raise BuildError('opaque_opening', 'Required photo window is occluded')
                files['mask:'+mask_spec['file']] = mask_path
            if obj['type'] == 'text':
                record = output.get('text')
                if not isinstance(record, dict) or record.get('text') != obj['draft']['default_text']:
                    raise BuildError('exact_text', 'Independent text parameters must match exact wording')
                font = record.get('font', {})
                if font.get('id') not in row['package']['fonts'] or font != row['package']['fonts'][font['id']]:
                    raise BuildError('font_changed', 'Final font must be from task registry')
                reproduced, actual = reproduce_text(size, record)
                if reproduced.tobytes() != image.tobytes() or actual != record:
                    raise BuildError('text_preview_mismatch', 'PNG/metrics do not match measured text parameters')
                dependencies.append({'path': font['path'], 'sha256': font['sha256'], 'external': True})
            elif 'text' in output:
                raise BuildError('text_ownership', 'Text metadata belongs only to original text objects')
        files[output['key']] = file
        checks.append({'key': output['key'], 'sha256': sha(file), **check})
    for key, file in files.items():
        dependencies.append({'path': str(file.relative_to(root)), 'sha256': sha(file), 'role': key, 'external': False})
    identity = fingerprint({'delivery': delivery, 'dependencies': dependencies, 'engine': state['engine'],
                            'runtime_packages': environment()['packages']})
    return delivery, files, checks, dependencies, identity


def submit(root, ident, delivery_path):
    started = time.monotonic()
    with locked(root) as root:
        state = load(root); refresh(root, state); row = task_for(state, ident)
        if input_changes(state) or state['engine'] != engine_signature():
            raise BuildError('stale_plan', 'Inputs or implementation changed; explicitly revise planning first')
        if row['status'] == 'blocked': raise BuildError('blocked', 'Resolve the plan gap before producing')
        # Exact resubmissions remain idempotent even at the visual limit.
        try:
            delivery, files, checks, dependencies, identity = check_delivery(root, state, row, delivery_path)
            if row.get('active') and row['active']['id'] == identity:
                if row['status'] in ('stale', 'machine_failed'): row['status'] = 'awaiting_visual'; row['review'] = None
                commit(root, state, 'duplicate_submission', {'task': ident, 'id': identity}); export(root, state)
                return {'status': 'duplicate', 'id': identity}
            ensure_remaining(state, ident)
            if sum(a['status'] == 'machine_failed' for a in row['attempts']) >= state['limits']['max_attempts']:
                raise BuildError('attempt_limit', 'Technical submission failure budget exhausted')
            if sum(a.get('elapsed_seconds', 0) for a in row['attempts']) >= state['limits']['max_seconds']:
                raise BuildError('time_limit', 'Task time budget exhausted')
            evidence = root / 'submissions' / identity
            evidence.mkdir(parents=True, exist_ok=True)
            outputs = []
            for output in delivery['outputs']:
                target = evidence / (output['key'].replace(':', '__') + '.png')
                shutil.copyfile(files[output['key']], target)
                if sha(target) != sha(files[output['key']]): raise BuildError('concurrent_change', 'Output changed during snapshot')
                copied = deepcopy(output); copied['file'] = str(target.relative_to(root)).replace('\\', '/')
                copied['sha256'] = sha(target)
                for index, mask in enumerate(copied.get('masks', [])):
                    original_mask = files['mask:' + mask['file']]
                    mask_target = evidence / (output['key'].replace(':', '__') + f'-mask-{index}.png')
                    shutil.copyfile(original_mask, mask_target)
                    mask['file'] = str(mask_target.relative_to(root)).replace('\\', '/')
                    mask['sha256'] = sha(mask_target)
                    dependencies.append({'path': mask['file'], 'sha256': mask['sha256'], 'external': False})
                outputs.append(copied)
                dependencies.append({'path': copied['file'], 'sha256': copied['sha256'], 'external': False})
            shutil.copyfile(files['script'], evidence / 'script.py')
            for index, (role, file) in enumerate(files.items()):
                if role.startswith(('mask:', 'dependency:')): shutil.copyfile(file, evidence / f'dependency-{index}{file.suffix}')
            atomic(evidence / 'delivery.json', delivery); atomic(evidence / 'machine-check.json', checks)
            for snapshot_file in evidence.iterdir():
                if snapshot_file.is_file():
                    dependencies.append({'path': str(snapshot_file.relative_to(root)), 'sha256': sha(snapshot_file), 'external': False})
            elapsed = time.monotonic()-started
            row['active'] = {'id': identity, 'outputs': outputs, 'dependencies': dependencies, 'checks': checks,
                             'delivery': str((evidence/'delivery.json').relative_to(root)), 'task_revision': row['package']['revision'],
                             'runtime_packages': environment()['packages']}
            row['status'] = 'awaiting_visual'; row['review'] = None; row.pop('stale_reasons', None)
            for other in state['tasks'].values():
                if other is not row and other['status'] == 'accepted':
                    other['status'] = 'awaiting_visual'; other['review'] = None

            row['attempts'].append({'status': 'machine_passed', 'id': identity, 'elapsed_seconds': elapsed,
                                    'timing': delivery['timing'], 'reason': delivery.get('reason'), 'at': now()})
            commit(root, state, 'submitted', {'task': ident, 'id': identity, 'machine_seconds': elapsed})
            export(root, state); return {'status': row['status'], 'id': identity, 'checks': checks}
        except (BuildError, OSError, ValueError, KeyError, TypeError) as exc:
            if getattr(exc, 'code', '') == 'visual_budget_exhausted':
                raise
            row['status'] = 'machine_failed'; row['review'] = None
            failure = {'status': 'machine_failed', 'error': str(exc), 'code': getattr(exc, 'code', 'invalid_delivery'),
                       'at': now(), 'elapsed_seconds': time.monotonic()-started}
            row['attempts'].append(failure); row['package']['failure_feedback'].append(failure)
            commit(root, state, 'submission_failed', {'task': ident, **failure}); export(root, state)
            return failure


def context_id(state):
    return fingerprint({'revision': state['revision'], 'assets': {k: r['active']['id'] if r.get('active') else None
                                                               for k, r in state['tasks'].items()}})


def review(root, ident, review_path):
    with locked(root) as root:
        state = load(root); refresh(root, state); row = task_for(state, ident)
        if row['status'] not in ('awaiting_visual', 'needs_changes', 'accepted', 'deferred'):
            raise BuildError('review_state', 'Only current machine-checked delivery can be reviewed')
        receipt = read_json(review_path)
        fields(receipt, {'schema_version', 'task_id', 'submission_id', 'context_id', 'reviewer', 'evidence',
                         'reviews', 'combination_issues', 'elapsed_seconds'}, label='review receipt')
        if (receipt['schema_version'] != 'build-review-v2' or receipt['task_id'] != ident or
                receipt['submission_id'] != row['active']['id'] or receipt['context_id'] != context_id(state)):
            raise BuildError('stale_review', 'Review must bind current files and current composition')
        if not isinstance(receipt['reviewer'], str) or not receipt['reviewer'].strip(): raise BuildError('reviewer', 'Reviewer identity required')
        preview = row.get('preview')
        if not preview or preview['context_id'] != context_id(state): raise BuildError('missing_preview', 'Create current preview first')
        if receipt['evidence'] != preview['evidence']: raise BuildError('review_evidence', 'Use exact preview evidence and digests')
        for file, digest in receipt['evidence'].items():
            if sha(relative_file(root, file)) != digest: raise BuildError('stale_preview', 'Preview evidence changed')
        old = row.get('review')
        if old and all(old.get(k) == v for k, v in receipt.items()):
            return {'status': row['status'], 'issues': old['effective_issues'], 'reused': True}
        if old and old.get('submission_id') == receipt['submission_id'] and old.get('context_id') == receipt['context_id']:
            raise BuildError('already_reviewed', 'Unchanged submission already reviewed; reuse it or submit a correction')
        ensure_remaining(state, ident)
        reviews = receipt['reviews']; objects = {o['key']: o for o in row['package']['objects']}
        if len(reviews) != len(objects) or {r.get('key') for r in reviews} != set(objects):
            raise BuildError('review_coverage', 'Review every owned object exactly once')
        issues = strings(receipt['combination_issues'], 'combination_issues')[:]
        for result in reviews:
            fields(result, {'key', 'status', 'issues', 'observed_text', 'unexpected_content'}, label='object review')
            if result['status'] not in ('passed', 'needs_changes'): raise BuildError('review_status', 'Unknown review status')
            strings(result['issues'], 'issues'); strings(result['unexpected_content'], 'unexpected_content')
            _, failures = check_scope_review(objects[result['key']]['content_scope'], result)
            issues.extend(result['issues']); issues.extend(failures)
            if result['status'] != 'passed': issues.append(result['key']+': needs_changes')
        delivery = read_json(root / row['active']['delivery'])
        issues.extend(delivery['unresolved'])
        if type(receipt['elapsed_seconds']) not in (int, float) or receipt['elapsed_seconds'] < 0:
            raise BuildError('review_time', 'Nonnegative elapsed_seconds required')
        last_round = budget(state, ident)['completed_reviews'] + 1 >= budget(state, ident)['max_reviews']
        row['status'] = ('deferred' if last_round else 'needs_changes') if issues else 'accepted'
        row['review'] = {**receipt, 'effective_issues': issues, 'effective_status': row['status']}
        atomic(root / 'reviews' / (uuid.uuid4().hex+'.json'), row['review'])
        from .review_selection import signatures
        row['review_cache'] = {'receipt': deepcopy(row['review']), 'signatures': signatures(state, row),
                               'boxes': {o['key']: o['target_box'] for o in row['package']['objects']}}
        commit(root, state, 'visual_review', {'task': ident, 'status': row['status'],
               'submission_id': receipt['submission_id'], 'context_id': receipt['context_id'],
               'member_keys': row['package'].get('member_keys', row['package']['owned_keys']),
               'elapsed_seconds': receipt['elapsed_seconds']})
        export(root, state); return {'status': row['status'], 'issues': issues}
