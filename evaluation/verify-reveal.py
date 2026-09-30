"""Read-only verification of artifacts and actual SDK model/image evidence."""
import argparse
import importlib.util
import sys
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha,verify_source
from scene import load_scene,checked_review
from validate import analysis_check,bindings_check
spec=importlib.util.spec_from_file_location('analysis_evidence',Path(__file__).with_name('summarize-analysis.py'))
evidence_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(evidence_module)
p=argparse.ArgumentParser();p.add_argument('--root',required=True);args=p.parse_args();root=Path(args.root);cases=[]
manifest=read(root/'review-manifest.json');skill=Path(__file__).resolve().parents[1]
code_unchanged=all(sha(skill/f)==h for f,h in manifest['skill_files'].items())
for ep in sorted(root.glob('*/build-evidence.json')):
    case=ep.parent;run=case/'run';errors=[];e=read(ep);r=read(run/'result.json')
    try:
        scene=load_scene(run);analysis_check(read(run/'analysis.json'),read(run/'input.json'))
        bindings_check(read(run/'bindings.json'),read(run/'analysis.json'),read(run/'prepared/catalog.json'))
        if r.get('visual_review'):checked_review(run,run/'review.json')
        for resource in r['resources']:
            if sha(resource['file'])!=resource['sha256']:errors.append('changed resource: '+resource['id'])
            if resource.get('source'):verify_source(resource['source'])
        windows=0
        for record in read(run/'assets/reveal/index.json')['records']:
            if record['action']!='clear_photo_window':continue
            mask=np.asarray(Image.open(record['window']['file']).convert('L'))
            alpha=np.asarray(Image.open(record['clean']['file']).getchannel('A'))
            if np.any(alpha[mask>0]):errors.append('old photo not cleared: '+record['id'])
            windows+=1
        for f,h in e['frozen_inputs'].items():
            if sha(run/f)!=h or sha(Path(e['old_run'])/f)!=h:errors.append('changed analysis/binding: '+f)
        if sha(run/'previews/first.png')!=r['final_sha256'] or sha(run/'final.png')!=r['final_sha256']:errors.append('first render changed')
        if not r['coverage_complete'] or not r['customer_photos_verified']:errors.append('photo source or object coverage missing')
        if any(v<.005 for v in r['photo_visible_fractions'].values()):errors.append('photo almost fully hidden')
    except Exception as exc:errors.append(type(exc).__name__+': '+str(exc));windows=None
    statefile=case/'review-controller/state.json';state=read(statefile) if statefile.exists() else {}
    evidence={}
    if state.get('status')=='completed':
        evidence=evidence_module.session_evidence(state['thread_id'])
        import json
        events=[json.loads(line) for line in (case/'review-controller/events.jsonl').read_text(encoding='utf-8').splitlines()]
        evidence['sdk_thread_starts']=sum(x['type']=='thread.started' for x in events)
        evidence['sdk_turn_starts']=sum(x['type']=='turn.started' for x in events)
        if evidence['sdk_thread_starts']!=1 or evidence['sdk_turn_starts']!=1:errors.append('more than one SDK thread/turn')
        if not evidence['model_contexts'] or any(c!={'model':'gpt-5.6-sol','effort':'medium'} for c in evidence['model_contexts']):errors.append('actual model/effort mismatch')
        with Image.open(run/'previews/comparison.png') as im:comparison_size=list(im.size)
        if comparison_size not in [i['size'] for i in evidence['images']]:errors.append('original comparison image missing in SDK payload')
        if state['sdk_run_calls']!=1 or not state.get('inputs_unchanged'):errors.append('review altered frozen inputs or was rerun')
    cases.append({'case':case.name,'errors':errors,'windows':windows,'photo_slots':len(r['photo_visible_fractions']),'status':r['status'],'review_state':state.get('status'),'actual_sdk_evidence':evidence})
result={'cases':len(cases),'code_snapshot_unchanged':code_unchanged,'mechanical_errors':sum(len(c['errors']) for c in cases),'photo_slots':sum(c['photo_slots'] for c in cases),'cleared_windows':sum(c['windows'] or 0 for c in cases),'completed_sdk_reviews':sum(c['review_state']=='completed' for c in cases),'details':cases}
save(root/'verification.json',result)
print({k:v for k,v in result.items() if k!='details'})
for c in cases:
    if c['errors']:print(c['case'],c['errors'])
