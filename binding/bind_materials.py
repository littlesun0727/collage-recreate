"""Default binding workflow: one command owns catalog, selection and output."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from binding_contract import read_json,digest,load_slots,resolve_overrides
from matching_step import save
import matching_step
from catalog import prepare_catalog,complete_catalog
from prompt_sections import read_stage
from text_completion import complete_texts


def describe_pending(workspace,config,openclaw_root,timeout,dry_run):
    pending=read_json(workspace/'pending.json')
    prompt=read_stage('describe')+'\n\n待描述资源 ID：\n'+json.dumps([a['asset_id'] for a in pending['pending']],ensure_ascii=False)
    (workspace/'prompt.txt').write_text(prompt,encoding='utf-8')
    save(workspace/'images.json',[{'path':p,'label':'客户素材联系表，asset_id 已标注；等比缩略图。'} for p in pending['contact_sheets']])
    if not config:raise ValueError('--config is required to describe new customer images')
    node=shutil.which('node')
    if not node:raise ValueError('Node.js is required')
    cmd=[node,str(ROOT/'match_request.mjs'),'--task','describe','--images',str(workspace/'images.json'),
         '--prompt',str(workspace/'prompt.txt'),'--config',str(Path(config).resolve()),'--output',str(workspace/'request'),
         '--timeout-seconds',str(timeout),'--output-tokens','32768']
    if openclaw_root:cmd+=['--openclaw-root',str(Path(openclaw_root).resolve())]
    if dry_run:cmd+=['--dry-run']
    with (workspace/'runner.stdout.txt').open('wb') as out,(workspace/'runner.stderr.txt').open('wb') as err:
        process=subprocess.run(cmd,stdout=out,stderr=err,timeout=timeout+30)
    call=read_json(workspace/'request/call.json')
    if process.returncode or call['status'] not in ('completed','dry_run_verified'):raise ValueError('Customer description request failed; inspect catalog/request/call.json')
    return workspace/'request/raw-response.txt'


def run(args):
    output=args.output.resolve()
    if any(output.is_relative_to(p.resolve()) for p in args.materials):raise ValueError('Output must be outside customer materials')
    if args.jobs:
        jobs=read_json(args.jobs)
        if not isinstance(jobs,list) or not jobs:raise ValueError('--jobs must contain a nonempty array')
    else:
        if not args.draft or not args.reference:raise ValueError('Provide --draft and --reference, or --jobs')
        jobs=[{'id':'matching','draft':str(args.draft),'reference':str(args.reference),'draft_version':args.draft_version}]
        if args.overrides:jobs[0]['overrides']=str(args.overrides)
        if args.text_overrides:jobs[0]['text_overrides']=str(args.text_overrides)
        if args.instructions:jobs[0]['instructions']=str(args.instructions)
    seen=set()
    for job in jobs:
        if not isinstance(job,dict) or not isinstance(job.get('id'),str) or not re.fullmatch('[a-z0-9][a-z0-9_-]*',job['id']) or job['id'] in seen:raise ValueError('Job IDs must be unique path-safe names')
        seen.add(job['id'])
        for field in ('draft','reference'):
            if not Path(job[field]).is_file():raise ValueError(f'Missing {field}: {job[field]}')
        meta_path=Path(job['draft']).parent/'validation.json'
        meta=read_json(meta_path) if meta_path.is_file() else {}
        version=job.get('draft_version','auto')
        if version=='auto':version=meta.get('contract_version','v2-2')
        load_slots(read_json(job['draft']),version)
        if meta.get('valid') is False:raise ValueError('Upstream draft validation failed')
        expected=meta.get('image',{}).get('sha256')
        if expected and digest(job['reference'])!=expected:raise ValueError('Reference hash differs from upstream analysis')
    output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();result={'status':'running','started_at':datetime.now(timezone.utc).isoformat(),'reasoning':'high','jobs':[]}
    save(output/'jobs.json',jobs)
    try:
        phase=time.monotonic()
        prepared=prepare_catalog(args.materials,output/'catalog',args.catalog)
        result.update(catalog_reused=prepared['reused'],catalog_pending=prepared['pending'])
        indexed=read_json(output/'catalog/catalog-input.json')['assets']
        all_bound=True
        for job in jobs:
            slots=read_json(job['draft'])['slots']
            fixed=resolve_overrides(read_json(job['overrides']) if job.get('overrides') else {},slots,indexed)
            all_bound=all_bound and all(s['id'] in fixed for s in slots if 'source_slot_id' not in s)
        if all_bound:
            save(output/'catalog/result.json',{'status':'not_needed','reason':'All independent slots explicitly bound'})
        elif prepared['pending']:
            raw=describe_pending(output/'catalog',args.config,args.openclaw_root,args.timeout_seconds,args.dry_run)
            if args.dry_run:
                result['status']='dry_run_verified';return result
            complete_catalog(output/'catalog',raw)
        else:
            complete_catalog(output/'catalog')
        result['catalog_seconds']=round(time.monotonic()-phase,3)
        result['catalog']=str(output/'catalog/catalog.json') if not all_bound else None
        for job in jobs:
            phase=time.monotonic()
            cmd=['--draft',job['draft'],'--reference',job['reference'],'--materials',*[str(p) for p in args.materials],
                 '--output',str(output/job['id']),'--draft-version',job.get('draft_version','auto'),
                 '--timeout-seconds',str(args.timeout_seconds)]
            if result['catalog']:cmd+=['--catalog',result['catalog']]
            if args.config:cmd+=['--config',str(args.config)]
            if args.openclaw_root:cmd+=['--openclaw-root',str(args.openclaw_root)]
            if args.dry_run:cmd+=['--dry-run']
            if job.get('overrides'):cmd+=['--overrides',str(job['overrides'])]
            # Same process: no agent round trips for preparation, validation or writing files.
            with redirect_stdout(StringIO()):matching_step.main(cmd)
            job_result=read_json(output/job['id']/'result.json')
            if not args.dry_run and job_result['status'] in ('completed','partial'):
                try:
                    job_result['text_completion']=complete_texts(
                        job_result['bindings'],args.config,args.timeout_seconds,args.openclaw_root,
                        Path(job['instructions']).read_text(encoding='utf-8-sig') if job.get('instructions') else '',
                        read_json(job['text_overrides']) if job.get('text_overrides') else None)
                except (OSError,ValueError,TypeError,KeyError,subprocess.TimeoutExpired) as exc:
                    job_result.update(status='failed',error=str(exc),text_completion={'status':'failed'})
                save(output/job['id']/'result.json',job_result)
            result['jobs'].append({'id':job['id'],**job_result,'wall_seconds':round(time.monotonic()-phase,3)})
            save(output/'result.json',result)
        statuses={j['status'] for j in result['jobs']}
        result['status']='dry_run_verified' if args.dry_run and statuses<={'dry_run_verified','dry_run_no_request_needed'} else 'completed' if statuses=={'completed'} else 'partial' if statuses<={'completed','partial'} else 'failed'
    except (OSError,ValueError,TypeError,KeyError,subprocess.TimeoutExpired) as exc:
        result.update(status='failed',error=str(exc))
    finally:
        calls=[]
        for file in output.rglob('call.json'):
            try:calls.append(read_json(file))
            except (OSError,ValueError):pass
        result.update(wall_seconds=round(time.monotonic()-started,3),finished_at=datetime.now(timezone.utc).isoformat(),
                      business_http_requests=sum(c.get('http_dispatches',0) for c in calls),
                      business_request_seconds=round(sum(c.get('elapsed_seconds',0) for c in calls),3),
                      effective_thinking=[c.get('effective_thinking') for c in calls])
        save(output/'result.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description='Describe, bind photos, then adapt unreadable copy. GPT-5.6-sol/high.')
    p.add_argument('--draft',type=Path);p.add_argument('--reference',type=Path);p.add_argument('--jobs',type=Path)
    p.add_argument('--materials',nargs='+',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--catalog',type=Path,help='Reuse unchanged descriptions from this previous catalog')
    p.add_argument('--overrides',type=Path);p.add_argument('--draft-version',choices=('auto','v2-1','v2-2'),default='auto')
    p.add_argument('--text-overrides',type=Path,help='JSON mapping unreadable text/overlay keys to user-supplied text')
    p.add_argument('--instructions',type=Path,help='UTF-8 user requirements for replacement copy')
    p.add_argument('--credentials','--config',dest='config',type=Path,default=Path('D:/codes/yibu_credentials.local.json'));p.add_argument('--openclaw-root',type=Path);p.add_argument('--timeout-seconds',type=int,default=600);p.add_argument('--dry-run',action='store_true')
    a=p.parse_args()
    if a.timeout_seconds<=0:p.error('timeout must be positive')
    if a.jobs and (a.draft or a.reference or a.overrides or a.text_overrides or a.instructions):p.error('--jobs cannot be combined with single-reference inputs')
    try:
        result=run(a);print(json.dumps(result,ensure_ascii=False));return 0 if result['status'] in ('completed','dry_run_verified') else 2
    except (OSError,ValueError,TypeError,KeyError) as exc:
        print(json.dumps({'status':'failed','error':str(exc)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
