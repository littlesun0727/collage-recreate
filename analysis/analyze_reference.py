"""Prepare one reference, run independent Kimi/high vision, then review its raw JSON."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageOps
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from review_draft import run as review, save_json


def prepare_image(reference, output):
    raw = reference.read_bytes()
    with Image.open(reference) as image:
        stored_size = list(image.size)
        orientation = image.getexif().get(274, 1)
        image_format = image.format
        if image_format in ('JPEG', 'PNG') and orientation == 1:
            prepared = output / ('reference.jpg' if image_format == 'JPEG' else 'reference.png')
            prepared.write_bytes(raw)
            size = stored_size
            conversion = 'none; original bytes preserved'
        else:
            prepared = output / 'reference.png'
            oriented = ImageOps.exif_transpose(image).convert('RGBA')
            oriented.save(prepared)
            size = list(oriented.size)
            conversion = 'EXIF orientation and/or format normalization to PNG; no resize'
    return prepared, {'original_sha256': hashlib.sha256(raw).hexdigest(), 'prepared_sha256': hashlib.sha256(prepared.read_bytes()).hexdigest(),
                      'stored_size': stored_size, 'oriented_size': size, 'exif_orientation': orientation, 'conversion': conversion, 'resized': False}


def main():
    parser = argparse.ArgumentParser(description='One Kimi/high vision request, no agent or automatic repair/retry.')
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--credentials', '--config', dest='config', type=Path, default=Path('D:/codes/yibu_credentials.local.json'), help='Local yibu credentials; --config is a compatibility alias')
    parser.add_argument('--openclaw-root', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--provider', default='yibu')
    parser.add_argument('--model', default='gpt-5.6-sol')
    parser.add_argument('--timeout-seconds', type=int, default=600)
    parser.add_argument('--dry-run', action='store_true', help='Build and verify audited yibu request without network transmission')
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error('timeout must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    result = {'status': 'preparing', 'model_called': False, 'review': None}
    try:
        reference, metadata = prepare_image(args.reference, args.output)
        save_json(args.output / 'input.json', metadata)
        prompt = args.output / 'prompt.txt'
        prompt.write_bytes((ROOT / 'analysis.md').read_bytes())
        node = shutil.which('node')
        if not node:
            raise ValueError('Node.js is not installed or not on PATH')
        command = [node, str(ROOT/'vision_request.mjs'), '--image', str(reference), '--prompt', str(prompt),
                   '--config', str(args.config), '--output', str(args.output/'request'), '--provider', args.provider,
                   '--model', args.model, '--timeout-seconds', str(args.timeout_seconds)]
        if args.openclaw_root:
            command += ['--openclaw-root', str(args.openclaw_root)]
        if args.dry_run:
            command.append('--dry-run')
        result['model_called'] = None  # Unknown until the dispatch audit is available.
        with (args.output/'runner.stdout.txt').open('wb') as stdout, (args.output/'runner.stderr.txt').open('wb') as stderr:
            process = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=args.timeout_seconds+30)
        call = json.loads((args.output/'request/call.json').read_text(encoding='utf-8'))
        result.update(status=call['status'], model_called=call['http_dispatches'] > 0, elapsed_seconds=call['elapsed_seconds'])
        if process.returncode == 0 and call['status'] == 'completed':
            report = review(reference, args.output/'request/raw-response.txt', args.output/'review')
            result['review'] = str(args.output/'review/index.html')
            result['draft_valid'] = report['valid']
            result['visual_status'] = 'unreviewed'
            if not report['valid']:
                result['status'] = 'draft_invalid'
        elif (args.output/'request/raw-response.txt').exists():
            # Do not promote a truncated/incomplete model response into a successful draft.
            result['raw_response'] = str(args.output/'request/raw-response.txt')
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result.update(status='failed', error=str(exc))
        call_path=args.output/'request/call.json'
        if call_path.exists():
            try:
                last_call=json.loads(call_path.read_text(encoding='utf-8'))
                result['model_called']=last_call.get('http_dispatches',0)>0
                result['last_call_status']=last_call.get('status')
            except (OSError,ValueError):
                pass
        result['outcome_unknown']=result['model_called'] is not False
    save_json(args.output/'result.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('completed', 'dry_run_verified') else 2


if __name__ == '__main__':
    raise SystemExit(main())
