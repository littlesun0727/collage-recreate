
import argparse
import json
from pathlib import Path
import subprocess
import sys
from collage.config import Config, ENGINE, ROOT
from collage import prompts
from collage.storage import save


def main():
    parser = argparse.ArgumentParser(description='Collage: VLM + fixed workflow + 360')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('workflow', help='Print the ordered workflow; no model calls')
    prompt_cmd = sub.add_parser('prompt', help='Preview a prompt template; no model calls')
    from collage.prompts.builder import TEMPLATES
    prompt_cmd.add_argument('stage', choices=list(TEMPLATES))
    prompt_cmd.add_argument('--mode', choices=['pixel', 'normalized_1000'], default='normalized_1000')
    prompt_cmd.add_argument('--out', type=Path)
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
        p.add_argument('--bbox-refinement', action=argparse.BooleanOptionalAction, default=None,
                       help='开启 bbox 校准；新任务默认关闭，续跑沿用任务设置')
        p.add_argument('--screen', action=argparse.BooleanOptionalAction, default=None,
                       help='开启提取相框审核；新任务默认关闭，续跑沿用任务设置')
        p.add_argument('--model-config')
        if name == 'create':
            p.add_argument('--reference', required=True)
            p.add_argument('--materials', nargs='+', required=True)
            p.add_argument('--width', type=int, default=1200)
            p.add_argument('--instructions', default='')
            p.add_argument('--cutout-model')
    args = parser.parse_args()
    try:
        if args.command == 'workflow':
            from collage.workflow.pipeline import STAGES
            for index, (stage, description, uses_vlm) in enumerate(STAGES, 1):
                print(f'{index}. {stage}: {description}' + (' [VLM]' if uses_vlm else ' [local]'))
            return 0
        if args.command == 'prompt':
            text = prompts.render(args.stage, mode=args.mode)
            if args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(text + '\n', encoding='utf-8')
                print(args.out)
            else:
                print(text)
            return 0
        config = Config.load(args.model_config)
        if args.command == 'run' and not args.model_config:
            config = config.for_run(args.run)
        if args.command == 'check':
            config.check_keys()
            import PIL, numpy, jsonschema, cv2, onnxruntime  # noqa: F401
            print(json.dumps({'ready': True, 'model': config.model, 'model_policy': config.model_policy(), 'engine': 'workflow-v9'}, ensure_ascii=False))
            return 0
        if args.command == 'probe':
            from PIL import Image, ImageDraw
            from collage.vlm.client import Client
            from collage.contracts import obj
            root = Path(args.out); root.mkdir(parents=True, exist_ok=True)
            im = Image.new('RGB', (240, 180), 'white'); ImageDraw.Draw(im).rectangle((60, 30, 180, 150), fill='red')
            im.save(root/'probe.png')
            client = Client(root/'vlm', config)
            answer = client.ask('probe', prompts.render('probe'), {}, [root/'probe.png'],
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
        from collage.workflow.pipeline import Workflow
        runner = Workflow(args.run, args.control, config, attempt=args.attempt, reveal=not args.no_reveal,
                        bbox_refinement=args.bbox_refinement, screen=args.screen)
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
