"""One batch of copy adaptation after photo selection, with audited requests."""
import json
from pathlib import Path
import shutil
import subprocess

from binding_contract import digest, read_json, read_model_json
from matching_step import save, make_sheet
from prompt_sections import read_stage
from text_contract import candidates, validate_records

ROOT = Path(__file__).resolve().parent


def complete_texts(binding_path, config, timeout=600, openclaw_root=None,
                   instructions='', overrides=None, dry_run=False):
    binding_path = Path(binding_path)
    document = read_json(binding_path)
    info = document['input']
    draft = read_json(info['draft_path'])
    wanted = candidates(draft)
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, dict) or any(k not in wanted for k in overrides):
        raise ValueError('Text overrides must map unreadable text/overlay keys to final text')
    fixed = validate_records([
        {'key': k, 'text': v, 'status': 'user', 'reason': '用户指定替代文案'}
        for k, v in overrides.items()], overrides, ('user',))
    pending = {k: v for k, v in wanted.items() if k not in fixed}
    selected_ids = {r['asset_id'] for r in document['bindings']} - {None}
    selected = [a for a in document['assets'] if a['asset_id'] in selected_ids]

    def check_inputs():
        if digest(info['draft_path']) != info['draft_sha256'] or digest(info['reference_path']) != info['reference_sha256']:
            raise ValueError('Draft/reference changed during text completion')
        for asset in selected:
            if digest(asset['path']) != asset['sha256']:
                raise ValueError('Selected photo changed during text completion')

    check_inputs()
    records = dict(fixed)
    if pending:
        workspace = binding_path.parent / 'text-completion'
        workspace.mkdir(exist_ok=False)
        data = {'candidates': [{'key': k, **item} for k, (item, _) in pending.items()],
                'slots': draft['slots'], 'selected_bindings': document['bindings'],
                'known_texts': [t for t in draft.get('texts', []) if t.get('default_text')],
                'fixed_replacements': list(fixed.values()), 'user_instructions': instructions}
        save(workspace / 'input.json', data)
        prompt = read_stage('text') + '\n\n本次数据：\n' + json.dumps(data, ensure_ascii=False)
        (workspace / 'prompt.txt').write_text(prompt, encoding='utf-8')
        images = [{'path': info['reference_path'], 'label': '完整模板：判断文字用途、语言、行数、长度与风格；不可辨认的文字不是事实依据。'}]
        for start in range(0, len(selected), 12):
            sheet = workspace / f'selected-{start // 12}.jpg'
            make_sheet([(a['asset_id'], a['path']) for a in selected[start:start+12]], sheet)
            images.append({'path': str(sheet), 'label': '实际已绑定的客户照片，标注 asset_id；只根据这些照片适配文案。'})
        save(workspace / 'images.json', images)
        node = shutil.which('node')
        if not node or not config:
            raise ValueError('Node.js and credentials config are required for text completion')
        cmd = [node, str(ROOT / 'match_request.mjs'), '--task', 'text',
               '--images', str(workspace / 'images.json'), '--prompt', str(workspace / 'prompt.txt'),
               '--config', str(Path(config).resolve()), '--output', str(workspace / 'request'),
               '--timeout-seconds', str(timeout), '--output-tokens', '32768']
        if openclaw_root:
            cmd += ['--openclaw-root', str(Path(openclaw_root).resolve())]
        if dry_run:
            cmd += ['--dry-run']
        with (workspace / 'runner.stdout.txt').open('wb') as out, (workspace / 'runner.stderr.txt').open('wb') as err:
            process = subprocess.run(cmd, stdout=out, stderr=err, timeout=timeout+30)
        call = read_json(workspace / 'request/call.json')
        if process.returncode or call['status'] not in ('completed', 'dry_run_verified'):
            raise ValueError('Text completion request failed; inspect text-completion/request/call.json')
        if dry_run:
            return {'status': 'dry_run_verified'}
        response = read_model_json(workspace / 'request/raw-response.txt')
        if not isinstance(response, dict) or set(response) != {'text_bindings'}:
            raise ValueError('Expected only text_bindings from copy adaptation')
        records.update(validate_records(response['text_bindings'], pending, ('generated', 'unresolved')))
    if dry_run:
        return {'status': 'dry_run_no_request_needed'}
    check_inputs()
    rows = [records[k] for k in wanted]
    validate_records(rows, wanted)
    document['text_bindings'] = rows
    document['text_completion'] = {'status': 'partial' if any(r['status'] == 'unresolved' for r in rows) else 'completed',
                                   'generated': sum(r['status'] == 'generated' for r in rows),
                                   'unresolved': [r['key'] for r in rows if r['status'] == 'unresolved']}
    save(binding_path, document)
    return document['text_completion']
