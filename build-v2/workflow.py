"""Independent build-v2 CLI. Can be launched from any working directory."""
import argparse
import json
from pathlib import Path
import sys

from build_v2_core.common import BuildError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan')
    p.add_argument('--draft', type=Path, required=True); p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--bindings', type=Path); p.add_argument('--draft-version', default='auto')
    p.add_argument('--width', type=int); p.add_argument('--objects', nargs='+'); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--unit-plan', type=Path, help='Compile an existing lightweight plan into production tasks'); p.add_argument('--plan', type=Path); p.add_argument('--batch-size', type=int, default=4)
    p.add_argument('--font-dir', action='append', default=[]); p.add_argument('--instructions', default='')
    p.add_argument('--credentials', '--config', dest='config', type=Path, default=Path('D:/codes/yibu_credentials.local.json')); p.add_argument('--openclaw-root', type=Path)
    p.add_argument('--model', default='gpt-5.6-sol'); p.add_argument('--provider', default='yibu')
    p.add_argument('--reasoning', choices=['off','low','high'], default='high'); p.add_argument('--timeout-seconds', type=int, default=600)
    p.add_argument('--dry-run', action='store_true'); p.add_argument('--revise', action='store_true'); p.add_argument('--reason')
    p.add_argument('--units-only', action='store_true', help='Experimental whole-image unit planning; does not create executable tasks')
    p.add_argument('--max-attempts', type=int, default=5); p.add_argument('--max-run-seconds', type=int, default=600)
    p.add_argument('--max-corrections', type=int, default=2, help='Visual rework rounds after the first review; technical failures are separate')
    for name in ('status','resume'):
        p = sub.add_parser(name); p.add_argument('--run', type=Path, required=True)
    for name, argument in [('submit','delivery')]:
        p = sub.add_parser(name); p.add_argument('--run', type=Path, required=True); p.add_argument('--task', required=True)
        p.add_argument('--'+argument, type=Path, required=True)
    p = sub.add_parser('review', help='Read review.md, call the visual model, save and register its report')
    p.add_argument('--run', type=Path, required=True); p.add_argument('--task', required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--credentials', '--config', dest='config', type=Path, help='Local yibu credentials for automatic visual review')
    mode.add_argument('--receipt', type=Path, help='Import an existing review report; no model call')
    p.add_argument('--openclaw-root', type=Path)
    p.add_argument('--model', default='gpt-5.6-sol'); p.add_argument('--provider', default='yibu')
    p.add_argument('--reasoning', choices=['off','low','high'], default='high')
    p.add_argument('--timeout-seconds', type=int, default=600); p.add_argument('--dry-run', action='store_true')
    p = sub.add_parser('preview'); p.add_argument('--run', type=Path, required=True); p.add_argument('--task', required=True)
    sub.add_parser('doctor')
    p = sub.add_parser('run-script'); p.add_argument('--run', type=Path, required=True); p.add_argument('--task', required=True)
    p.add_argument('--script', required=True); p.add_argument('--timeout', type=int, default=120); p.add_argument('--image')
    p.add_argument('--trusted-host', action='store_true'); p.add_argument('--approval-reason')
    p = sub.add_parser('tool'); p.add_argument('--run', type=Path, required=True); p.add_argument('--task', required=True)
    p.add_argument('--key', required=True); p.add_argument('--parameters', type=Path); p.add_argument('--allow-remote', action='store_true')
    p.add_argument('--image-provider', choices=['yibu'], default='yibu'); p.add_argument('--credentials', type=Path, default=Path('D:/codes/yibu_credentials.local.json'))
    p.add_argument('--members', nargs='+', help='For compose only: members of this generated component')
    p.add_argument('--image-model', default='gemini-3-pro-image'); p.add_argument('--generation-timeout', type=int, default=300)
    args = parser.parse_args(argv)
    try:
        if hasattr(args, 'max_corrections') and args.max_corrections < 0:
            raise BuildError('invalid_limit', 'max_corrections must be nonnegative')
        for key in ('batch_size','max_attempts','max_run_seconds','timeout_seconds','timeout'):
            if hasattr(args,key) and getattr(args,key)<=0: raise BuildError('invalid_limit', key+' must be positive')
        from build_v2_core import workflow
        if args.command=='plan':
            if args.units_only:
                from build_v2_core.unit_planner import plan_units
                result=plan_units(args)
            else:
                result=workflow.plan_run(args)
        elif args.command in ('status','resume'): result=workflow.status(args.run)
        elif args.command=='submit': result=workflow.submit(args.run,args.task,args.delivery)
        elif args.command=='review':
            if args.receipt:
                if args.dry_run: raise BuildError('invalid_mode', '--dry-run applies to model review only')
                result=workflow.review(args.run,args.task,args.receipt)
            else:
                from build_v2_core.model_review import review_with_model
                result=review_with_model(args)
        elif args.command=='preview':
            from build_v2_core.inspection import preview
            result=preview(args.run,args.task)
        elif args.command=='doctor':
            from build_v2_core.runner import capability
            result={'environment':workflow.environment(),'execution':capability()}
        elif args.command=='run-script':
            from build_v2_core.runner import run_script
            result=run_script(args.run,args.task,args.script,timeout=args.timeout,image=args.image,
                              trusted_host=args.trusted_host,approval_reason=args.approval_reason)
        elif args.command=='tool':
            from build_v2_core.tools import produce
            result=produce(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('status') in ('machine_failed','failed','timeout') else 0
    except (BuildError,OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'status':'error','code':getattr(exc,'code','invalid_input'),'message':str(exc)},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
