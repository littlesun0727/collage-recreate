"""Verify frozen-input identity and actual live extraction request geometry."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha,now
from reveal import request_object


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);args=p.parse_args();root=Path(args.root)
    manifest=read(root/'build-manifest.json');source=Path(manifest['analysis_root']);cases=[]
    changed=[f for f,h in manifest['skill_files'].items() if sha(Path(manifest['skill'])/f)!=h]
    for name in manifest['names']:
        run=root/name/'run'
        if not (run/'previews/first.png').exists():continue
        errors=[];tasks=[]
        try:
            for kind in ['analysis','bindings']:
                if sha(run/f'{kind}.json')!=sha(source/name/'run'/f'{kind}-first.json'):
                    errors.append('changed frozen input: '+kind)
            inputs=read(run/'input.json');result=read(run/'result.json');config=read(run/'reveal-config.json')
            if sha(run/'final.png')!=sha(run/'previews/first.png') or sha(run/'final.png')!=result['final_sha256']:
                errors.append('first render identity differs')
            if not config.get('remote') or config.get('cache') or config.get('padding')!=.1:
                errors.append('expected live 10% request configuration')
            for d in sorted((run/'assets/reveal/api').glob('batch_*')):
                meta=read(d/'request_meta.json');submit=read(d/'submit_response.json') if (d/'submit_response.json').exists() else {}
                q=read(d/'query_response.json') if (d/'query_response.json').exists() else {};flags=[]
                if len(meta['objects'])>20:flags.append('more than 20 targets')
                for o in meta['objects']:
                    if o!=request_object({'id':o['id'],'bbox':o['original_bbox']},inputs['reference_size'],.1):
                        flags.append('request padding mismatch: '+o['id'])
                if meta['reference_sha256']!=sha(run/'assets/reveal/api/input.png'):flags.append('reference hash mismatch')
                if not submit.get('task_id') or submit.get('response_status')!=0:flags.append('submission not accepted')
                if q.get('status')!='done':flags.append('task not done')
                output=q.get('output',{});boxes=[o['bbox'] for o in meta['objects']];mapping=output.get('boxes_mapping_index',[])
                mapped=[boxes[i] for i in mapping]
                if output.get('image_boxes') is not None and output['image_boxes'] not in [boxes,mapped]:flags.append('service box echo mismatch')
                tasks.append({'task_id':submit.get('task_id'),'status':q.get('status'),
                    'requested':len(boxes),'returned':len(mapping),'errors':flags})
        except Exception as exc:errors.append(f'{type(exc).__name__}: {exc}')
        cases.append({'name':name,'errors':errors,'tasks':tasks})
    ids=[t['task_id'] for c in cases for t in c['tasks'] if t.get('task_id')]
    out={'checked_at':now(),'cases_checked':len(cases),'skill_files_changed':changed,
        'input_errors':sum(len(c['errors']) for c in cases),'api_errors':sum(len(t['errors']) for c in cases for t in c['tasks']),
        'accepted_tasks':len(ids),'unique_tasks':len(set(ids)),'cases':cases}
    save(root/'api-verification.json',out);print(json.dumps({k:v for k,v in out.items() if k!='cases'},ensure_ascii=False))


if __name__=='__main__':main()
