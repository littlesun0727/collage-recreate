"""Verify live extraction requests, frozen outputs, actual SDK model/image evidence."""
import argparse
import importlib.util
import json
import sys
from pathlib import Path
from collections import Counter
from PIL import Image,ImageDraw,ImageFont
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha,verify_source,now
from scene import load_scene,checked_review
from validate import analysis_check,bindings_check
from reveal import request_object,matches_request
spec=importlib.util.spec_from_file_location('evidence',Path(__file__).with_name('summarize-analysis.py'))
evidence=importlib.util.module_from_spec(spec);spec.loader.exec_module(evidence)

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);args=p.parse_args();root=Path(args.root);manifest=read(root/'manifest.json');cases=[]
    changed=[f for f,h in manifest['skill_files'].items() if sha(Path(manifest['skill'])/f)!=h]
    for name in manifest['names']:
        case=root/Path(name).stem;run=case/'run';sf=case/'controller/state.json'
        if not sf.exists():continue
        state=read(sf)
        if state['status']!='completed':continue
        errors=[];tasks=[];photos=0;window_count=0
        try:
            inputs=read(run/'input.json');analysis=read(run/'analysis.json');scene=load_scene(run);result=read(run/'result.json')
            analysis_check(analysis,inputs);bindings_check(read(run/'bindings.json'),analysis,read(run/'prepared/catalog.json'))
            checked_review(run,run/'review.json');review=read(run/'review.json');assessment=read(case/'evaluation.json')
            if assessment['visual_verdict']!=review['verdict']:errors.append('assessment disagrees with registered review')
            if sha(run/'final.png')!=result['final_sha256'] or sha(run/'previews/first.png')!=result['final_sha256']:errors.append('frozen first/final hash mismatch')
            if read(run/'previews/first-scene.json')!=scene:errors.append('scene changed after first render')
            if not result['coverage_complete'] or not result['customer_photos_verified']:errors.append('object coverage or customer source failed')
            photos=sum(o['kind']=='photo' for o in scene['objects'])
            if len(result['photo_visible_fractions'])!=photos:errors.append('photo visibility count mismatch')
            for r in result['resources']:
                verify_source(r)
                if r.get('source'):verify_source(r['source'])
                if r['kind']=='photo' and not any(Path(r['source']['file']).resolve().is_relative_to(Path(x).resolve()) for x in manifest['materials']):errors.append('photo outside customer source directories')
            config=read(run/'reveal-config.json')
            if not config.get('remote') or config.get('padding')!=.1 or config.get('cache'):errors.append('not a fresh live 10% extraction configuration')
            for task in sorted((run/'assets/reveal/api').glob('batch_*')):
                meta=read(task/'request_meta.json');submit=read(task/'submit_response.json') if (task/'submit_response.json').exists() else {};q=read(task/'query_response.json') if (task/'query_response.json').exists() else {}
                flags=[]
                if len(meta['objects'])>20:flags.append('more than 20 targets')
                for obj in meta['objects']:
                    expected=request_object({'id':obj['id'],'bbox':obj['original_bbox']},inputs['reference_size'],.1)
                    if obj!=expected:flags.append('bad padding: '+obj['id'])
                if meta['reference_sha256']!=sha(run/'assets/reveal/api/input.png'):flags.append('submitted reference identity changed')
                if not submit.get('task_id') or submit.get('response_status')!=0:flags.append('submission not accepted')
                if q.get('status')!='done':flags.append('service did not complete')
                else:
                    submitted_boxes=[o['bbox'] for o in meta['objects']];output=q.get('output',{});returned_boxes=output.get('image_boxes')
                    mapped_boxes=[submitted_boxes[i] for i in output.get('boxes_mapping_index',[])]
                    if returned_boxes is not None and returned_boxes not in [submitted_boxes,mapped_boxes]:flags.append('service box echo differs from request')
                tasks.append({'task':str(task),'task_id':submit.get('task_id'),'requested':len(meta['objects']),'status':q.get('status'),'errors':flags,'objects':meta['objects']})
            for rec in read(run/'assets/reveal/index.json')['records']:
                if rec['action']=='clear_photo_window':
                    mask=np.asarray(Image.open(rec['window']['file']).convert('L'));alpha=np.asarray(Image.open(rec['clean']['file']).getchannel('A'))
                    if np.any(alpha[mask>0]):errors.append('old pixels left within window mask: '+rec['id'])
                    window_count+=1
                if rec.get('clean'):
                    with Image.open(rec['clean']['file']) as im:
                        if list(im.size)!=inputs['reference_size']:errors.append('recovered full canvas size mismatch')
            with Image.open(run/'prepared/reference.png') as im:box_image=im.convert('RGB')
            draw=ImageDraw.Draw(box_image);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',max(13,box_image.width//65));seen=set()
            for task in tasks:
                for obj in task['objects']:
                    key=(obj['id'],tuple(obj['bbox']))
                    if key in seen:continue
                    seen.add(key);draw.rectangle(obj['bbox'],outline='#e82a91',width=3);draw.rectangle(obj['original_bbox'],outline='#00b96c',width=2)
                    draw.text((obj['bbox'][0]+2,obj['bbox'][1]+2),obj['id'],fill='#e82a91',font=font,stroke_width=1,stroke_fill='white')
            box_image.save(case/'request-boxes.png')
        except Exception as exc:errors.append(type(exc).__name__+': '+str(exc))
        evidence_path=case/'controller/session-evidence.json'
        cached_evidence=read(evidence_path) if evidence_path.exists() else {}
        ev=cached_evidence if cached_evidence.get('thread_id')==state.get('thread_id') else evidence.session_evidence(state.get('thread_id'))
        if not cached_evidence:
            ev['thread_id']=state.get('thread_id');save(evidence_path,ev)
        if not ev['model_contexts'] or any(x!={'model':'gpt-5.6-sol','effort':'medium'} for x in ev['model_contexts']):errors.append('actual SDK model/effort mismatch')
        if state.get('sdk_run_calls')!=1:errors.append('more than one SDK run')
        sdk_events=[json.loads(x) for x in (case/'controller/events.jsonl').read_text(encoding='utf-8').splitlines()]
        if sum(x['type']=='thread.started' for x in sdk_events)!=1 or sum(x['type']=='turn.started' for x in sdk_events)!=1:errors.append('SDK event stream has more than one thread/turn')
        if (run/'input.json').exists() and read(run/'input.json')['reference_size'] not in [x['size'] for x in ev['images']]:errors.append('original-size reference payload missing')
        if (run/'previews/comparison.png').exists():
            with Image.open(run/'previews/comparison.png') as im:comparison_size=list(im.size)
            if comparison_size not in [x['size'] for x in ev['images']]:errors.append('original-size comparison payload missing')
        cases.append({'case':case.name,'errors':errors,'tasks':tasks,'photo_slots':photos,'cleared_windows':window_count,'sdk_evidence':ev})
    task_ids=[t['task_id'] for c in cases for t in c['tasks'] if t['task_id']]
    output={'checked_at':now(),'completed_cases_checked':len(cases),'skill_files_changed':changed,'mechanical_errors':sum(len(c['errors']) for c in cases),
            'api_task_errors':sum(len(t['errors']) for c in cases for t in c['tasks']),'accepted_task_ids':len(task_ids),'unique_task_ids':len(set(task_ids)),
            'photo_slots':sum(c['photo_slots'] for c in cases),'cleared_windows':sum(c['cleared_windows'] for c in cases),'cases':cases}
    save(root/'verification.json',output);print(json.dumps({k:v for k,v in output.items() if k!='cases'},ensure_ascii=False))
    for c in cases:
        if c['errors']:print(c['case'],c['errors'])
if __name__=='__main__':main()
