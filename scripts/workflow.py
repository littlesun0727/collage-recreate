"""First-preview pipeline with optional Reveal extraction and yibu generation."""
import argparse
import json
import sys
from pathlib import Path
from common import read, timed, locked, save


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('--reference',required=True);a.add_argument('--materials',nargs='+',required=True);a.add_argument('--run',required=True);a.add_argument('--width',type=int,default=1200);a.add_argument('--instructions',default='');a.add_argument('--cutout-model')
    for name in ['validate','build','reveal-plan','render','apply','review','generate','recover']:
        a=sub.add_parser(name);a.add_argument('--run',required=True)
        if name in ['apply','review','recover']:a.add_argument('--file',required=True)
        if name in ['build','reveal-plan']:
            a.add_argument('--reveal',action='store_true',help='Enable remote Reveal requests for missing complex assets')
            a.add_argument('--reveal-cache',help='Existing downloaded Reveal sample folder; offline unless --reveal is also set')
            a.add_argument('--reveal-key-file',default='D:/codes/.env')
            a.add_argument('--reveal-timeout',type=int,default=360)
            a.add_argument('--reveal-padding',type=float,default=.1,help='Expand each request edge by this fraction of original width/height (default .1)')
            a.add_argument('--no-reveal',action='store_true')
            a.add_argument('--reveal-layout',choices=['grouped','full','legacy'],default='grouped',help='Spatial crops (default), full-image context control, or historical overlay-only requests')
        if name=='generate':
            a.add_argument('--ids',nargs='+',required=True);a.add_argument('--credentials',default='D:/codes/yibu_credentials.local.json');a.add_argument('--allow-remote',action='store_true');a.add_argument('--timeout',type=int,default=300);a.add_argument('--workers',type=int,default=2);a.add_argument('--dry-run',action='store_true');a.add_argument('--group',action='store_true',help='Generate selected overlay/text members as one fused unit')
    args=p.parse_args()
    try:
        if args.command=='prepare':
            from prepare import prepare
            result=prepare(args.reference,args.materials,args.run,args.width,args.instructions,args.cutout_model)
        else:
            run=Path(args.run).resolve()
            if not (run/'input.json').exists():raise ValueError('Run prepare first')
            with locked(run),timed(run,args.command):
                if args.command=='validate':
                    from validate import analysis_check, bindings_check
                    from prepare import boxes
                    analysis=analysis_check(read(run/'analysis.json'),read(run/'input.json'))
                    result={'analysis_valid':True,'boxes':boxes(run,analysis)}
                    if (run/'bindings.json').exists():
                        bindings_check(read(run/'bindings.json'),analysis,read(run/'prepared/catalog.json'));result['bindings_valid']=True
                elif args.command=='reveal-plan':
                    from validate import analysis_check
                    from effects import resolve_style
                    from reveal_assets import targets
                    from reveal_plan import plan,preview
                    a=analysis_check(read(run/'analysis.json'),read(run/'input.json'))
                    for o in a['objects']:o['style']=resolve_style(o)
                    if args.reveal_layout=='legacy':raise ValueError('Plan preview supports grouped or full')
                    result=preview(read(run/'input.json')['reference']['file'],plan(a,targets(a),args.reveal_padding,args.reveal_layout),run/'previews/reveal-plan')
                elif args.command=='build':
                    from scene import compile_scene
                    from render import render
                    config=None
                    if args.no_reveal and (args.reveal or args.reveal_cache):raise ValueError('Conflicting Reveal options')
                    if args.no_reveal:config={'enabled':False}
                    elif args.reveal or args.reveal_cache:
                        config={'enabled':True,'remote':args.reveal,'cache':str(Path(args.reveal_cache).resolve()) if args.reveal_cache else None,'key_file':args.reveal_key_file,'timeout':args.reveal_timeout,'padding':args.reveal_padding,'layout':args.reveal_layout}
                    compile_scene(run,config);result=render(run)
                elif args.command=='render':
                    from render import render
                    result=render(run)
                elif args.command=='recover':
                    from recovery import recover
                    result=recover(run,args.file)
                elif args.command=='apply':
                    from scene import apply_review
                    from render import render
                    apply_review(run,args.file);result=render(run)
                elif args.command=='review':
                    from render import accept_review
                    result=accept_review(run,args.file)
                elif args.command=='generate':
                    from generation.generate import generate
                    result=generate(run,args)
        print(json.dumps(result,ensure_ascii=True,indent=2));return 0
    except Exception as exc:
        print(json.dumps({'status':'failed','error':str(exc),'type':type(exc).__name__},ensure_ascii=True));return 2


if __name__=='__main__':sys.exit(main())
