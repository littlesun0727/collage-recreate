"""Check frozen JSON, sources, real SDK image/model evidence and analysis-only scope."""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha,now
from validate import analysis_check,bindings_check
spec=importlib.util.spec_from_file_location('session_evidence',Path(__file__).with_name('summarize-analysis.py'))
evidence=importlib.util.module_from_spec(spec);spec.loader.exec_module(evidence)

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);args=p.parse_args();root=Path(args.root)
    manifest=read(root/'manifest.json');cases=[]
    changed=[name for name,h in manifest['skill_files'].items() if sha(Path(manifest['skill'])/name)!=h]
    for name in manifest['names']:
        case=root/Path(name).stem;run=case/'run';sf=case/'controller/state.json'
        if not sf.exists():continue
        state=read(sf)
        review_path=case/'analysis-review.json'
        if not review_path.exists() and (run/'analysis-review.json').exists():review_path=run/'analysis-review.json'
        alternate=review_path.parent==run
        if state['status']!='completed' and not (state['status']=='incomplete' and alternate and (run/'analysis-freeze.json').exists()):continue
        flags=[];photo_count=0
        try:
            inputs=read(run/'input.json');a=analysis_check(read(run/'analysis-first.json'),inputs)
            bindings_check(read(run/'bindings-first.json'),a,read(run/'prepared/catalog.json'))
            frozen=read(run/'analysis-freeze.json')
            for kind in ['analysis','bindings']:
                if sha(run/f'{kind}.json')!=frozen[kind+'_sha256'] or sha(run/f'{kind}-first.json')!=frozen[kind+'_sha256']:flags.append(kind+' changed after freeze')
            if sha(run/'previews/boxes-first.png')!=frozen['boxes_sha256']:flags.append('frozen box image changed')
            ids={o['id'] for o in a['objects']};review=read(review_path)
            if set(review.get('checked_ids',[]))!=ids:flags.append('self-check ID coverage differs')
            if review.get('model')!='gpt-5.6-sol' or review.get('effort')!='medium':flags.append('review metadata mismatch')
            if review.get('verdict') not in ['pass','needs_changes']:flags.append('unknown self-check verdict')
            if any(i.get('id') is not None and i['id'] not in ids for i in review.get('issues',[])):flags.append('self-check issue references unknown object')
            photo_count=sum(o['kind']=='photo' for o in a['objects'])
            forbidden=[p for p in ['assets','scene.json','result.json','final.png','reveal-config.json','generation','previews/first.png','previews/comparison.png'] if (run/p).exists()]
            if forbidden:flags.append('unexpected production artifacts: '+str(forbidden))
            workflow_events=[json.loads(line) for line in (run/'events.jsonl').read_text(encoding='utf-8').splitlines()] if (run/'events.jsonl').exists() else []
            bad_events=[e['event'] for e in workflow_events if e['event'] not in ['prepare_started','prepare_finished']]
            if bad_events:flags.append('non-prepare workflow events: '+str(bad_events))
        except Exception as exc:flags.append(type(exc).__name__+': '+str(exc))
        ef=case/'controller/session-evidence.json'
        ev=read(ef) if ef.exists() else evidence.session_evidence(state['thread_id'])
        if not ef.exists():save(ef,ev)
        if not ev['model_contexts'] or any(c!={'model':'gpt-5.6-sol','effort':'medium'} for c in ev['model_contexts']):flags.append('actual model/effort differs')
        sizes=[x['size'] for x in ev['images']]
        if sizes.count(read(run/'input.json')['reference_size'])<2:flags.append('original reference and box image payload evidence missing')
        events=[json.loads(x) for x in (case/'controller/events.jsonl').read_text(encoding='utf-8').splitlines()]
        if state.get('sdk_run_calls')!=1 or sum(e['type']=='thread.started' for e in events)!=1 or sum(e['type']=='turn.started' for e in events)!=1:flags.append('more than one SDK run/thread/turn')
        commands=[e['item'].get('command','') for e in events if e['type']=='item.completed' and e.get('item',{}).get('type')=='command_execution']
        if any(re.search(r'workflow\.py[\"\x27]?\s+(?:build|apply|generate|review)\b',c) for c in commands):flags.append('production command in SDK trace')
        cases.append({'name':case.name,'errors':flags,'photo_slots':photo_count,'model_evidence':ev,'thread_id':state['thread_id'],
                      'controller_state':state['status'],'review_path':str(review_path),'alternate_review_path':alternate})
    out={'checked_at':now(),'checked':len(cases),'skill_files_changed':changed,'errors':sum(len(c['errors']) for c in cases),
         'photo_slots':sum(c['photo_slots'] for c in cases),'cases':cases}
    save(root/'verification.json',out);print(json.dumps({k:v for k,v in out.items() if k!='cases'},ensure_ascii=False))
    for c in cases:
        if c['errors']:print(c['name'],c['errors'])
if __name__=='__main__':main()
