"""Frozen-analysis integration experiment: same customer bindings, no analysis/model/API calls."""
import argparse
import shutil
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha,timed,now
from prepare import prepare
from scene import compile_scene
from render import render

p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--names',nargs='*');args=p.parse_args()
root=Path(args.root);base=Path('D:/codes/collage_outputs/v5-eval-merged-first-20260928');cache=base/'360_overlay_results'
priority=['20260908-192922','拼贴2','拼贴7','海边人像拼图']
names=args.names or priority+[d.name for d in sorted(cache.iterdir()) if d.is_dir() and (base/d.name/'run/analysis.json').exists() and d.name not in priority]
root.mkdir(parents=True,exist_ok=True)
for name in names:
    old=base/name/'run';run=root/name/'run'
    if (root/name/'build-evidence.json').exists():
        print('EXISTS',name,flush=True);continue
    started=time.perf_counter();meta=read(old/'input.json')
    if not run.exists():
        prepare(meta['original_reference']['file'],meta['materials'],run,width=meta['output_width'],instructions='Frozen-analysis Reveal integration comparison; offline existing extraction cache; no generation.',cutout_model=meta.get('cutout_model'))
    for file in ['analysis.json','bindings.json']:shutil.copyfile(old/file,run/file)
    start=time.perf_counter()
    with timed(run,'build'):
        compile_scene(run,{'enabled':True,'remote':False,'cache':str(cache/name)})
        result=render(run)
    duration=time.perf_counter()-start
    index=read(run/'assets/reveal/index.json')
    evidence={'case':name,'built_at':now(),'experiment':'frozen-analysis-and-bindings','old_run':str(old),'cache':str(cache/name),'remote_submissions':0,
              'build_seconds':round(duration,3),'prepare_and_build_seconds':round(time.perf_counter()-started,3),
              'frozen_inputs':{f:sha(run/f) for f in ['analysis.json','bindings.json']},
              'actions':{action:sum(r['action']==action for r in index['records']) for action in {r['action'] for r in index['records']}},
              'unconfirmed_text':index['unconfirmed_embedded_text'],'incomplete':result['incomplete_objects'],
              'photos_verified':result['customer_photos_verified'],'visible_photos':result['photo_visible_fractions']}
    save(root/name/'build-evidence.json',evidence)
    print(name,round(duration,2),evidence['actions'],'incomplete',len(evidence['incomplete']),flush=True)
