"""Prepare bounded visual calls. The outer controller gets one module result."""
import json
from pathlib import Path
import re
import shutil
import subprocess

from .common import BuildError, ROOT, model_json, read_json, save
from .content_scope import allowed_text, neighbor_candidates


def section(name):
    if name not in ('plan', 'generate', 'review'):
        raise BuildError('missing_prompt', 'Unknown prompt')
    return (ROOT / 'prompts' / (name + '.md')).read_text(encoding='utf-8')


def request(prompt, images, folder, args, dry_run=False, task='plan'):
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'prompt.txt').write_text(prompt, encoding='utf-8')
    save(folder / 'images.json', images)
    if not args.config:
        raise BuildError('missing_config', 'Model planning requires --config or an explicit --plan')
    node = shutil.which('node')
    if not node:
        raise BuildError('missing_runtime', 'Node.js is required')
    cmd = [node, str(ROOT / 'model_request.mjs'), '--images', str(folder / 'images.json'), '--prompt', str(folder / 'prompt.txt'),
           '--config', str(args.config), '--output', str(folder / 'request'), '--model', args.model,
           '--provider', args.provider, '--reasoning', args.reasoning, '--timeout-seconds', str(args.timeout_seconds), '--task', task]
    if args.openclaw_root:
        cmd += ['--openclaw-root', str(args.openclaw_root)]
    if dry_run:
        cmd.append('--dry-run')
    with (folder / 'stdout.txt').open('wb') as out, (folder / 'stderr.txt').open('wb') as err:
        try:
            process = subprocess.run(cmd, stdout=out, stderr=err, timeout=args.timeout_seconds + 30,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired as exc:
            raise BuildError('model_timeout', 'Model deadline exceeded; inspect evidence before retrying') from exc
    audit_path = folder / 'request/call.json'
    audit = read_json(audit_path) if audit_path.exists() else {'status': 'failed'}
    if process.returncode or audit['status'] not in ('completed', 'dry_run_verified'):
        raise BuildError('model_failed', f'Model request failed; inspect {folder / "request/call.json"}')
    return None if dry_run else model_json(folder / 'request/raw-response.txt')


def plan_batch(inputs, keys, fonts, folder, args, dry_run=False):
    reference_path = folder.parent / 'reference.png'
    if not reference_path.exists():
        inputs['reference'].save(reference_path)
    images = [{'path': str(reference_path), 'label': '完整参考图，朝向归正，未缩放。'}]
    objects = []
    for key in keys:
        entry = inputs['entries'][key]
        item = dict(entry)
        item['allowed_text'] = allowed_text(entry)
        item['neighbor_candidates'] = neighbor_candidates(entry, inputs)
        x, y, right, bottom = entry['target_box']
        item['related_boxes_in_asset_space'] = {
            other_key: [(v - origin) / span for v, origin, span in zip(other['target_box'], (x, y, x, y), (right-x, bottom-y, right-x, bottom-y))]
            for other_key, other in inputs['entries'].items() if other_key != key}
        item['photo_windows_in_asset_space'] = {
            other['id']: [(v - origin) / span for v, origin, span in zip(other['target_box'], (x, y, x, y), (right-x, bottom-y, right-x, bottom-y))]
            for other in inputs['passthrough']}
        objects.append(item)
        crop_path = folder.parent / (key.replace(':', '__') + '.png')
        inputs['reference'].crop(entry['reference_box']).save(crop_path)
        images.append({'path': str(crop_path), 'label': f'{key} 参考裁图，仅制作该对象。'})
        if entry['type'] == 'slot' and entry.get('source_asset'):
            from .common import oriented
            asset_path = folder.parent / (key.replace(':', '__') + '-customer.png')
            oriented(entry['source_asset']['path']).save(asset_path)
            images.append({'path': str(asset_path), 'label': f'{key} 客户源图；selection_box 使用此图坐标。'})
    data = {'required_keys_this_batch': list(keys), 'context_policy': 'Only objects in this batch require output. draft, neighbors, related boxes and user instructions are context, never extra deliverables.', 'objects': objects, 'draft': inputs['draft'], 'canvas_size': inputs['info']['canvas_size'], 'font_ids': list(fonts),
            'user_instructions': args.instructions or '', 'revision_feedback': inputs.get('revision_feedback', {})}
    return request(section('plan') + '\n\n任务数据：\n' + json.dumps(data, ensure_ascii=False), images, folder, args, dry_run)
