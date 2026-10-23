"""Run the requested two-lane comparison and publish review artifacts to GitHub.

Lane 1: yibu/Qwen, serial. Lane 2: Codex/Luna then Codex/Sol, serial.
Uses the unmodified v9 workbench API. Resume by invoking this script again.
"""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

REPORT = Path(__file__).resolve().parent
ROOT = REPORT.parents[1]
sys.path.insert(0, str(ROOT))
from batch_run import IMAGES, MEDIA, Workbench, source_files
from collage.config import Config, env_value
from collage.storage import exclusive, save
from PIL import Image, ImageOps

LOCAL = ROOT.with_name(ROOT.name + '-output') / 'model-comparison-20261023'
DATA = LOCAL / 'runtime'
STATE = LOCAL / 'campaign.json'
SERVER = 'http://127.0.0.1:8810'
BRANCH = 'codex/collage-recreate-v9-20261023'
REMOTE = 'https://github.com/littlesun0727/collage-recreate.git'
MODELS = [
    {'key': 'qwen', 'label': 'yibuapi Qwen3.8-flash / thinking off',
     'provider': 'yibu', 'model': 'qwen3.8-flash', 'reasoning': 'off'},
    {'key': 'luna', 'label': 'Codex gpt-6-luna / medium',
     'provider': 'codex', 'model': 'gpt-6-luna', 'reasoning': 'medium'},
    {'key': 'sol', 'label': 'Codex gpt-6.1-sol / medium',
     'provider': 'codex', 'model': 'gpt-6.1-sol', 'reasoning': 'medium'},
]
STATUSES = {'pending': '待提交', 'queued': '排队', 'running': '运行中',
            'completed': '已交付', 'failed': '失败'}
SAFE_JSON = ['structure.json', 'analysis.json', 'text-units.json',
             'photo-windows.json', 'bindings.json', 'scene.json', 'result.json',
             'workflow-models.json', 'workflow-options.json', 'workflow-timing.json']
SECRETS = [value for name in ('YIBU_API_KEY', 'OPENROUTER_API_KEY', '360_API_KEY')
           if (value := os.getenv(name) or env_value(ROOT / '.env', name))]
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def log(message):
    print(f'[{now()}] {message}', flush=True)


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {} if default is None else default


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()
                if k.lower() not in {'authorization', 'api_key', 'access_token',
                                     'refresh_token', 'id_token', 'token', 'headers'}}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        for secret in SECRETS:
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'(?i)Bearer\s+[A-Za-z0-9._-]+', 'Bearer [REDACTED]', value)
    return value


def write_text(path, value):
    if path.is_file() and path.read_text(encoding='utf-8') == value:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(value, encoding='utf-8')
    temporary.replace(path)


def write_json(path, value):
    write_text(path, json.dumps(scrub(value), ensure_ascii=False, indent=2) + '\n')


def command(args, timeout=180):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout,
                            creationflags=FLAGS, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    if result.returncode:
        raise RuntimeError(scrub((result.stderr or result.stdout)[-2000:]))
    return result.stdout.strip()


def initialize():
    if STATE.is_file():
        campaign = read(STATE)
        assert campaign['models'] == MODELS, 'Existing campaign has different model settings'
        return campaign
    references = source_files(['D:/datas/covers'], ROOT, IMAGES, 'references')
    materials = source_files(['D:/datas/live动图素材'], ROOT, MEDIA, 'materials')
    for index, reference in enumerate(references, 1):
        reference['case'] = f'{index:03d}'
    groups = [references[:1]] + [references[i:i+20] for i in range(1, len(references), 20)]
    campaign = {'created_at': now(), 'source_commit': command(['git', 'rev-parse', 'HEAD']),
                'models': MODELS, 'references': references, 'materials': materials,
                'batches': [], 'scheduling': 'yibu serial || (codex luna serial -> sol serial)'}
    for model in MODELS:
        config = Config(provider=model['provider'], model=model['model'],
                        reasoning_enabled=model['reasoning'] != 'off',
                        reasoning_effort=None if model['reasoning'] == 'off' else 'medium',
                        coordinate_mode='normalized_1000', response_format='json_object')
        config.check_ready()
        for index, group in enumerate(groups, 1):
            campaign['batches'].append({'id': 'run-' + uuid.uuid4().hex,
                'model': model['key'], 'part': index, 'references': [r['case'] for r in group],
                'policy': config.model_policy(), 'status': 'pending'})
    save(STATE, campaign)
    return campaign


def connect():
    client = Workbench(SERVER)
    client.connect(start=True, data=DATA)
    # Refuse to attach to a different service's data on the same port.
    if not (DATA / 'server.pid').is_file():
        raise RuntimeError('Port 8810 is in use by a different workbench')
    return client


def submit(client, campaign, batch):
    existing = DATA / 'service/batches' / (batch['id'] + '.json')
    if existing.exists():
        batch['status'] = 'submitted'
        save(STATE, campaign)
        return
    if not batch.get('upload_id'):
        batch['upload_id'] = client.request('/api/uploads', {'multiple_references': True})['id']
        save(STATE, campaign)
    uploaded = read(DATA / 'service/uploads' / batch['upload_id'] / 'batch.json')
    frozen = {(f['role'], f['name'], f['sha256']) for f in uploaded.get('files', [])}
    selected = [r for r in campaign['references'] if r['case'] in batch['references']]
    for role, item in [('reference', r) for r in selected] + [('material', m) for m in campaign['materials']]:
        if (role, item['name'], item['sha256']) in frozen:
            continue
        reply = client.request(f"/api/uploads/{batch['upload_id']}/files", file=Path(item['path']), role=role)
        if reply['sha256'] != item['sha256'] or reply['bytes'] != item['bytes']:
            raise RuntimeError('Input changed since campaign snapshot: ' + item['name'])
    payload = {'id': batch['id'], 'upload_id': batch['upload_id'],
               'name': f"20261023 三模型对比 | {batch['model']} | {batch['part']:02d}",
               'model_policy': batch['policy'], 'width': 1200, 'instructions': '',
               'bbox_refinement': False, 'screen': False}
    client.request('/api/batches', payload)
    batch['status'] = 'submitted'
    save(STATE, campaign)
    log(f"Submitted {batch['model']} part {batch['part']}: {len(selected)} references")


def rows(campaign):
    result = {}
    for batch in campaign['batches']:
        record = read(DATA / 'service/batches' / (batch['id'] + '.json'))
        jobs = record.get('jobs', [])
        for index, case in enumerate(batch['references']):
            row = {'case': case, 'model': batch['model'], 'batch': batch['id'],
                   'status': 'pending', 'step': None, 'error': None}
            if index < len(jobs):
                job = jobs[index]
                imported = read(DATA / 'service/jobs' / (job['id'] + '.json'))
                creation_id = job['id'].replace('import-', 'create-', 1)
                creation = read(DATA / 'service/jobs' / (creation_id + '.json'))
                status = creation.get('status') or imported.get('status', 'queued')
                if not creation and status == 'completed':
                    status = 'queued'
                run = Path(job['run'])
                progress = read(run / 'workflow-progress.json')
                row.update(status=status, run=str(run),
                           control=str(DATA / 'service/creation' / creation_id),
                           task_id=creation.get('task_id') or imported.get('task_id'),
                           step=progress.get('current_step'),
                           error=scrub(creation.get('error') or imported.get('error')),
                           elapsed_seconds=creation.get('elapsed_seconds'),
                           delivery_category=creation.get('delivery_category'),
                           review_status=creation.get('review_status'))
            result[(case, batch['model'])] = row
    return result


def next_batches(campaign, current):
    """At most one batch in each lane; Sol starts only after all Luna tasks end."""
    selected = []
    for lane in [('qwen',), ('luna', 'sol')]:
        for batch in (b for b in campaign['batches'] if b['model'] in lane):
            statuses = [current[(case, batch['model'])]['status'] for case in batch['references']]
            if all(s in {'completed', 'failed'} for s in statuses):
                continue
            if all(s == 'pending' for s in statuses):
                selected.append(batch)
            break
    return selected


def thumbnail(source, target, size=320):
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((size, size * 2), Image.Resampling.LANCZOS)
        image.save(target, quality=86, optimize=True)


def export_case(row):
    if 'run' not in row:
        return
    run, control = Path(row['run']), Path(row['control'])
    dest = REPORT / 'cases' / row['case'] / row['model']
    # Snapshot stable outputs only after completion/failure, preserving first-run comparison.
    if row['status'] not in {'completed', 'failed'}:
        return
    marker = dest / 'exported.json'
    if marker.is_file():
        return
    for filename in SAFE_JSON:
        source = run / filename
        if source.is_file():
            write_json(dest / filename, read(source))
    for filename in ['state.json', 'warnings.json']:
        if (control / filename).is_file():
            write_json(dest / 'workflow' / filename, read(control / filename))
    for source in (control / 'vlm').glob('*/parameters.json'):
        write_json(dest / 'vlm' / source.parent.name / source.name, read(source))
    for filename in ['final.png', 'previews/comparison.png', 'previews/analysis-boxes.png',
                     'previews/photo-windows.png']:
        source = run / filename
        if source.is_file():
            target = dest / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    if (dest / 'final.png').is_file():
        thumbnail(dest / 'final.png', dest / 'thumb.jpg')
    write_json(marker, {'status': row['status'], 'exported_at': now(),
                        'final_sha256': hashlib.sha256((dest / 'final.png').read_bytes()).hexdigest()
                        if (dest / 'final.png').is_file() else None})


def publish_report(campaign, current):
    for reference in campaign['references']:
        case = reference['case']
        thumbnail(Path(reference['path']), REPORT / 'cases' / case / 'reference.jpg', 1000)
        thumbnail(Path(reference['path']), REPORT / 'cases' / case / 'reference-thumb.jpg')
        for model in MODELS:
            export_case(current[(case, model['key'])])
    counts = {m['key']: dict(Counter(r['status'] for r in current.values() if r['model'] == m['key']))
              for m in MODELS}
    snapshot = {'created_at': campaign['created_at'], 'source_commit': campaign['source_commit'],
                'updated_at': now(), 'references': len(campaign['references']),
                'total': len(current), 'counts': counts, 'scheduling': campaign['scheduling'],
                'items': [{k: v for k, v in r.items() if k not in {'run', 'control'}} for r in current.values()]}
    write_json(REPORT / 'progress.json', snapshot)
    write_json(REPORT / 'dataset.json', {
        'references': [{k: v for k, v in r.items() if k != 'path'} for r in campaign['references']],
        'materials': [{k: v for k, v in r.items() if k != 'path'} for r in campaign['materials']],
        'models': MODELS, 'width': 1200, 'coordinate_mode': 'normalized_1000',
        'bbox_refinement': False, 'screen': False, 'response_format': 'json_object'})
    lines = ['# 116 张参考图 · 三模型对比', '',
        f"最后更新：{snapshot['updated_at']}。共 {len(current)} 个任务。", '',
        '执行顺序：**yibuapi Qwen 串行**与**Codex 串行队列（全部 Luna → 全部 Sol）**并行。', '',
        '统一使用 18 段客户视频作为素材，输出宽度 1200；首次分析及后续步骤均使用各组指定模型。',
        '沿用 v9 首版静态成图工作流：范围校准和相框审核关闭，JSON Schema 接口约束关闭。',
        '“已交付”表示工作流及文件检查完成；视觉质量请看成图，告警与简化交付均保留。', '',
        '| 模型 | 已交付 | 失败 | 运行中 | 排队 | 待提交 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for model in MODELS:
        count = counts[model['key']]
        lines.append('| ' + model['label'] + ' | ' + ' | '.join(str(count.get(s, 0)) for s in
                     ['completed', 'failed', 'running', 'queued', 'pending']) + ' |')
    lines += ['', '点击参考图编号打开该案例；点击成图缩略图打开原尺寸 PNG。刷新本页可查看新结果。', '',
              '| 参考图 | Qwen / off | Luna / medium | Sol / medium |', '| --- | --- | --- | --- |']
    for reference in campaign['references']:
        case = reference['case']
        cells = [f"[{case} · {reference['name']}](cases/{case}/README.md)<br>![参考](cases/{case}/reference-thumb.jpg)"]
        detail = [f"# {case} · {reference['name']}", '', '[返回总览](../../README.md)', '',
                  '![参考图](reference.jpg)', '']
        for model in MODELS:
            row = current[(case, model['key'])]
            status = STATUSES[row['status']]
            if row['step'] and row['status'] == 'running':
                status += ' · ' + row['step']
            relative = f"cases/{case}/{model['key']}"
            has_final = (REPORT / relative / 'final.png').exists()
            cell = status
            if has_final:
                cell += f'<br>[![成图]({relative}/thumb.jpg)]({relative}/final.png)'
            cells.append(cell)
            detail += [f"## {model['label']}", '', status, '']
            if row.get('elapsed_seconds') is not None:
                detail += [f"耗时：{row['elapsed_seconds']:.1f} 秒；交付类别：{row.get('delivery_category')}。", '']
            if row.get('error'):
                detail += ['失败信息：', '', '```text', scrub(row['error']).replace('```', "'''"), '```', '']
            if has_final:
                detail += [f"![成图]({model['key']}/final.png)", '']
            if (REPORT / relative).exists():
                detail += [f"[查看参数、中间 JSON、告警及对照图]({model['key']}/)", '']
        lines.append('| ' + ' | '.join(cells) + ' |')
        write_text(REPORT / 'cases' / case / 'README.md', '\n'.join(detail))
    lines += ['', '原尺寸成图和关键中间产物逐项同步。原始客户视频、凭证、请求图片载荷不进入报告。', '',
              '本机工作台：http://127.0.0.1:8810/ 。完整运行产物位于相邻输出目录的 `model-comparison-20261023/runtime/`。',
              '后台进程每 60 秒检查进度并尝试 Git 推送；网络失败会保留本地提交并继续重试。',
              '恢复运行：在工程目录用已有 Python 环境执行 `python -B reports/model-comparison-20261023/run_campaign.py`。', '']
    write_text(REPORT / 'README.md', '\n'.join(lines))
    return snapshot


def sync_git(snapshot):
    if command(['git', 'branch', '--show-current']) != BRANCH:
        raise RuntimeError('Current branch changed; refusing to push to a different branch')
    if command(['git', 'remote', 'get-url', '--push', 'origin']).rstrip('/') != REMOTE:
        raise RuntimeError('Origin changed; refusing to push')
    relative = REPORT.relative_to(ROOT).as_posix()
    # Check literal credential values before staging only this campaign's directory.
    for path in REPORT.rglob('*'):
        if path.is_file():
            if path.stat().st_size > 90 * 1024**2:
                raise RuntimeError('Artifact exceeds publication size limit: ' + str(path))
            if path.suffix in {'.json', '.md', '.txt'}:
                content = path.read_text(encoding='utf-8')
                if any(secret in content for secret in SECRETS):
                    raise RuntimeError('Credential found in report; publication stopped')
    command(['git', 'add', '--', relative])
    if command(['git', 'diff', '--cached', '--name-only', '--', relative]):
        completed = sum(c.get('completed', 0) for c in snapshot['counts'].values())
        failed = sum(c.get('failed', 0) for c in snapshot['counts'].values())
        command(['git', 'commit', '--only', '-m',
                 f'Comparison progress: {completed}/{snapshot["total"]} delivered, {failed} failed',
                 '--', relative])
    command(['git', 'push', 'origin', f'HEAD:refs/heads/{BRANCH}'], timeout=180)
    save(LOCAL / 'last-push.json', {'at': now(), 'commit': command(['git', 'rev-parse', 'HEAD']),
                                  'counts': snapshot['counts']})
    log('GitHub synced: ' + json.dumps(snapshot['counts']))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--snapshot-only', action='store_true')
    args = parser.parse_args()
    LOCAL.mkdir(parents=True, exist_ok=True)
    with exclusive(LOCAL / 'supervisor.lock'):
        campaign = initialize()
        if args.prepare_only or args.snapshot_only:
            snapshot = publish_report(campaign, rows(campaign))
            log(json.dumps(snapshot['counts']))
            return
        save(LOCAL / 'supervisor.json', {'pid': os.getpid(), 'started_at': now(), 'server': SERVER})
        if os.name == 'nt':
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        last_signature = None
        try:
            while True:
                try:
                    client = connect()
                    current = rows(campaign)
                    for batch in next_batches(campaign, current):
                        submit(client, campaign, batch)
                    current = rows(campaign)
                    signature = json.dumps(list(current.values()), ensure_ascii=False, sort_keys=True)
                    if signature != last_signature:
                        snapshot = publish_report(campaign, current)
                        sync_git(snapshot)
                        last_signature = signature
                    finished = all(r['status'] in {'completed', 'failed'} for r in current.values())
                    save(LOCAL / 'heartbeat.json', {'at': now(), 'pid': os.getpid(),
                         'finished': finished, 'counts': {m['key']: dict(Counter(r['status'] for r in current.values()
                          if r['model'] == m['key'])) for m in MODELS}})
                    if finished:
                        log('All 348 task attempts finished and results pushed.')
                        return
                except Exception as error:
                    log('Retrying supervisor operation in 60 seconds: ' + scrub(str(error)))
                    save(LOCAL / 'supervisor-error.json', {'at': now(), 'error': scrub(str(error))})
                time.sleep(60)
        finally:
            if os.name == 'nt':
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


if __name__ == '__main__':
    main()
