"""Four deterministic local-only controls with exactly the same frozen design inputs."""
import argparse
import shutil
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha,timed
from prepare import prepare
from scene import compile_scene
from render import render
p=argparse.ArgumentParser();p.add_argument('--root',required=True);args=p.parse_args();root=Path(args.root)
for name in ['20260908-192922','拼贴2','拼贴7','海边人像拼图']:
    source=root/name/'run';run=root/'_local_controls'/name/'run'
    if run.exists():continue
    meta=read(source/'input.json');prepare(meta['original_reference']['file'],meta['materials'],run,width=meta['output_width'],cutout_model=meta.get('cutout_model'))
    for f in ['analysis.json','bindings.json']:shutil.copyfile(source/f,run/f)
    started=time.perf_counter()
    with timed(run,'build'):
        compile_scene(run,{'enabled':False});r=render(run)
    save(run.parent/'control-evidence.json',{'case':name,'seconds':round(time.perf_counter()-started,3),'frozen_inputs':{f:sha(run/f) for f in ['analysis.json','bindings.json']},'incomplete':r['incomplete_objects'],'status':'unreviewed_local_control','remote_calls':0})
    print(name,round(time.perf_counter()-started,2),flush=True)
