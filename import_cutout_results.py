"""Validate offline masks, seed existing task caches, optionally repair fallbacks.

No inference or paid model calls. Original source pixels are always retained.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'engine' / 'scripts'))
from common import fingerprint, locked, read, save, sha
from photos import resolve_cutout_model


def validate(results_zip, inputs_zip):
    accepted, rejected = {}, []
    with zipfile.ZipFile(results_zip) as results, zipfile.ZipFile(inputs_zip) as inputs:
        manifest = json.loads(inputs.read('manifest.json'))
        if json.loads(results.read('manifest.json')) != manifest:
            raise ValueError('Returned manifest differs from original export')
        report = json.loads(results.read('processing-report.json'))
        if sha(inputs_zip) != report['input_zip_sha256']:
            raise ValueError('Original archive checksum mismatch')
        reports = {item['source_sha256']: item for item in report['items']}
        for item in manifest['items']:
            name, digest = item['filename'], item['source_sha256']
            raw = inputs.read('images/' + name)
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('Original photo checksum mismatch: ' + name)
            mask_bytes = results.read('masks/' + name)
            if hashlib.sha256(mask_bytes).hexdigest() != reports[digest]['mask_sha256']:
                raise ValueError('Mask checksum mismatch: ' + name)
            with Image.open(io.BytesIO(raw)) as source, Image.open(io.BytesIO(mask_bytes)) as mask:
                if mask.mode != 'L' or mask.size != source.size or list(mask.size) != [item['width'], item['height']]:
                    raise ValueError('Mask must be full-size 8-bit grayscale: ' + name)
                if not mask.point(lambda x: 255 if x > 8 else 0).getbbox():
                    rejected.append({'filename': name, 'source_sha256': digest, 'reason': 'empty_foreground'})
                    continue
                rgba = source.convert('RGBA')
                rgba.putalpha(ImageChops.multiply(mask, rgba.getchannel('A')))
                output = io.BytesIO()
                rgba.save(output, format='PNG')
                accepted[digest] = output.getvalue()
    return accepted, rejected, report['model_sha256']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--tasks', type=Path, required=True)
    parser.add_argument('--task-glob', default='task-*')
    parser.add_argument('--report-dir', type=Path, required=True)
    parser.add_argument('--repair', action='store_true')
    args = parser.parse_args()
    accepted, rejected, model_sha = validate(args.results, args.inputs)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    summary = {'accepted_masks': len(accepted), 'rejected': rejected, 'tasks': []}
    save(args.report_dir / 'report.json', summary)
    print(f'Validated {len(accepted)} masks; rejected {len(rejected)} empty masks', flush=True)
    for run in sorted(args.tasks.glob(args.task_glob)):
        if not (run / 'scene.json').exists() or not (run / 'result.json').exists():
            continue
        scene, result = read(run / 'scene.json'), read(run / 'result.json')
        if not result.get('exported'):
            continue
        sources = {o.get('source', {}).get('sha256') for o in scene['objects']}
        available = sources & accepted.keys()
        if not available:
            continue
        model = resolve_cutout_model(scene.get('cutout_model'))
        if not model or sha(model) != model_sha:
            raise ValueError('Task model differs from offline model: ' + run.name)
        repair_ids = [r['id'] for r in result.get('resources', [])
                      if r.get('metadata', {}).get('fallback')
                      and r.get('metadata', {}).get('resource_type') == 'customer_photo'
                      and (r.get('source') or {}).get('sha256') in accepted]
        with locked(run):
            cache = run / 'assets/photos/.cache'
            cache.mkdir(parents=True, exist_ok=True)
            for digest in available:
                path = cache / (fingerprint([digest, model_sha, 'v5-birefnet-1']) + '.png')
                temp = path.with_suffix('.import.tmp')
                temp.write_bytes(accepted[digest])
                temp.replace(path)
                save(path.with_suffix('.json'), {'sha256': sha(path), 'source_sha256': digest,
                     'model_sha256': model_sha, 'imported_from': str(args.results.resolve())})
            if args.repair and repair_ids:
                backup = args.report_dir / run.name
                backup.mkdir(exist_ok=True)
                for name in ('result.json', 'final.png'):
                    if not (backup / name).exists():
                        shutil.copy2(run / name, backup / name)
                for oid in repair_ids:
                    receipt = run / 'assets/photos' / (oid + '.json')
                    if receipt.exists():
                        shutil.copy2(receipt, backup / (oid + '.json'))
                        receipt.unlink()
        entry = {'task': run.name, 'cached_masks': len(available), 'repair_ids': repair_ids}
        if args.repair and repair_ids:
            print('Rendering ' + run.name + ': ' + ', '.join(repair_ids), flush=True)
            command = [sys.executable, str(ROOT / 'engine/scripts/workflow.py'), 'render', '--run', str(run), '--brief']
            with (args.report_dir / (run.name + '.log')).open('w', encoding='utf-8') as log:
                completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            entry['exit_code'] = completed.returncode
            current = read(run / 'result.json')
            entry['remaining_photo_fallbacks'] = [r['id'] for r in current.get('resources', [])
                if r.get('metadata', {}).get('fallback') and r.get('metadata', {}).get('resource_type') == 'customer_photo']
            if completed.returncode:
                summary['tasks'].append(entry)
                save(args.report_dir / 'report.json', summary)
                raise RuntimeError('Render failed; inspect log and preserved backup: ' + run.name)
        summary['tasks'].append(entry)
        save(args.report_dir / 'report.json', summary)
    print(json.dumps({'cached_tasks': len(summary['tasks']), 'repaired_tasks': sum('exit_code' in t for t in summary['tasks'])}), flush=True)


if __name__ == '__main__':
    main()
