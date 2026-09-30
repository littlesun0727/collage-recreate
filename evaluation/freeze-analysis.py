"""Validate/freeze joint analysis and bindings without compiling or contacting services."""
import argparse
import shutil
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha,now
from validate import analysis_check,bindings_check
from prepare import boxes

def freeze(run):
    run=Path(run)
    if any((run/p).exists() for p in ['analysis-first.json','bindings-first.json','analysis-freeze.json']):
        raise ValueError('First analysis already frozen; do not overwrite it')
    analysis=analysis_check(read(run/'analysis.json'),read(run/'input.json'))
    bindings_check(read(run/'bindings.json'),analysis,read(run/'prepared/catalog.json'))
    image=boxes(run,analysis)
    for name in ['analysis','bindings']:
        shutil.copyfile(run/f'{name}.json',run/f'{name}-first.json')
    shutil.copyfile(image,run/'previews/boxes-first.png')
    result={'frozen_at':now(),'schema_valid':True,'bindings_valid':True,'objects':len(analysis['objects']),
            'analysis_sha256':sha(run/'analysis-first.json'),'bindings_sha256':sha(run/'bindings-first.json'),
            'boxes_sha256':sha(run/'previews/boxes-first.png'),'boxes':str(run/'previews/boxes-first.png')}
    save(run/'analysis-freeze.json',result)
    return result

if __name__=='__main__':
    import json
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True)
    print(json.dumps(freeze(parser.parse_args().run),ensure_ascii=False))
