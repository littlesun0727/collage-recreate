import argparse
import json
from pathlib import Path
import subprocess
import sys

from .config import Config, ENGINE, ROOT
from .storage import save


def main():
    parser = argparse.ArgumentParser(description='Collage v6: VLM + fixed workflow + 360')
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check', help='Offline dependency and credential configuration check')
    check.add_argument('--model-config', help='JSON stage policy (no credentials)')
    probe = sub.add_parser('probe', help='One real provider image/JSON probe (billable)')
    probe.add_argument('--out', required=True)
    probe.add_argument('--model-config')
    for name in ['run', 'create']:
        p = sub.add_parser(name)
        p.add_argument('--run', required=True)
        p.add_argument('--control')
        p.add_argument('--attempt', type=int, default=1)
        p.add_argument('--no-reveal', action='store_true')
        p.add_argument('--model-config')
        if name == 'create':
            p.add_argument('--reference', required=True)
            p.add_argument('--materials', nargs='+', required=True)
            p.add_argument('--width', type=int, default=1200)
            p.add_argument('--instructions', default='')
            p.add_argument('--cutout-model')
    args = parser.parse_args()
    try:
        config = Config.load(args.model_config)
        if args.command == 'run' and not args.model_config:
            config = config.for_run(args.run)
        if args.command == 'check':
            config.check_keys()
            import PIL, numpy, jsonschema, cv2, onnxruntime  # noqa: F401
            print(json.dumps({'ready': True, 'model': config.model, 'model_policy': config.model_policy(), 'engine': 'workflow-v6'}, ensure_ascii=False))
            return 0
        if args.command == 'probe':
            from PIL import Image, ImageDraw
            from .client import Client
            from .contracts import obj
            root = Path(args.out); root.mkdir(parents=True, exist_ok=True)
            im = Image.new('RGB', (240, 180), 'white'); ImageDraw.Draw(im).rectangle((60, 30, 180, 150), fill='red')
            im.save(root/'probe.png')
            client = Client(root/'vlm', config)
            answer = client.ask('probe', 'Inspect the image. Return only JSON, in English.', {}, [root/'probe.png'],
                                 obj({'color': {'type': 'string'}, 'shape': {'type': 'string'}}))
            result = {'answer': answer, 'usage': client.usage(), 'model': config.model}
            save(root/'result.json', result); print(json.dumps(result, ensure_ascii=False)); return 0
        if args.command == 'create':
            command = [sys.executable, '-B', str(ENGINE/'workflow.py'), 'prepare', '--run', args.run,
                       '--reference', args.reference, '--materials', *args.materials, '--width', str(args.width),
                       '--instructions', args.instructions]
            if args.cutout_model:
                command += ['--cutout-model', args.cutout_model]
            subprocess.run(command, cwd=ROOT, check=True)
        from .runner import Runner
        runner = Runner(args.run, args.control, config, attempt=args.attempt, reveal=not args.no_reveal)
        result = runner.execute()
        print(json.dumps({'run': str(runner.run), 'final': str(runner.run/'final.png'),
                          'verdict': result.get('visual_review', {}).get('verdict'),
                          'summary': result.get('visual_review', {}).get('summary'),
                          'usage': runner.client.usage()}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)[:1800]}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
