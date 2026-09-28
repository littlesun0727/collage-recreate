"""Freeze the local runtime and run a bounded batch, one request per reference."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
RUNTIME=['analysis.md','analyze_reference.py','vision_request.mjs','review_draft.py','draft_contract.py']


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--credentials','--config',dest='config',type=Path,default=Path('D:/codes/yibu_credentials.local.json'))
    p.add_argument('--openclaw-root',type=Path,help=argparse.SUPPRESS)
    p.add_argument('--workers',type=int,choices=(1,2,4),default=2)
    a=p.parse_args()
    cases=json.loads(a.manifest.read_text(encoding='utf-8-sig'))
    seen=set()
    for case in cases:
        ident=case['id']
        if not re.fullmatch('[a-z0-9][a-z0-9_-]*',ident) or ident in seen:
            raise ValueError('Invalid or duplicate case ID')
        seen.add(ident)
        reference=Path(case['reference'])
        if not reference.is_file(): raise ValueError(f'Missing reference: {reference}')
        case['reference_sha256']=hashlib.sha256(reference.read_bytes()).hexdigest()
    a.output.mkdir(parents=True,exist_ok=False)
    frozen=a.output/'frozen'
    hashes={}
    runtime_files=[(Path('analysis')/rel,ROOT/rel) for rel in RUNTIME]
    runtime_files += [(Path('structured_output.mjs'),ROOT.parent/'structured_output.mjs')]
    runtime_files += [(Path('model_api')/p.name,p) for p in (ROOT.parent/'model_api').glob('*.mjs')]
    for rel,source in runtime_files:
        target=frozen/rel;target.parent.mkdir(parents=True,exist_ok=True)
        data=source.read_bytes();target.write_bytes(data);hashes[rel.as_posix()]=hashlib.sha256(data).hexdigest()
    def save(name,value):
        (a.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    save('manifest.json',cases)
    config_digest=hashlib.sha256(a.config.read_bytes()).hexdigest()
    save('freeze.json',{'created':datetime.now(timezone.utc).isoformat(),'files':hashes,'workers':a.workers,
      'model':'yibu/gpt-5.6-sol','reasoning':'high','max_completion_tokens':32768,'timeout_seconds':600,'automatic_retries':0,
      'config_sha256':config_digest,'source_manifest':str(a.manifest)})
    results=[]
    def execute(case):
        if hashlib.sha256(a.config.read_bytes()).hexdigest()!=config_digest:
            raise ValueError('Configuration changed after freeze; request not sent')
        if hashlib.sha256(Path(case['reference']).read_bytes()).hexdigest()!=case['reference_sha256']:
            raise ValueError('Reference changed after freeze; request not sent')
        if any(hashlib.sha256((frozen/rel).read_bytes()).hexdigest()!=sha for rel,sha in hashes.items()):
            raise ValueError('Frozen runtime changed; request not sent')
        case_dir=a.output/'cases'/case['id']
        cmd=[sys.executable,'-B','-X','utf8',str(frozen/'analysis/analyze_reference.py'),'--reference',case['reference'],
          '--output',str(case_dir),'--config',str(a.config)]
        (a.output/'logs').mkdir(exist_ok=True)
        with (a.output/'logs'/f"{case['id']}.stdout.txt").open('wb') as out,(a.output/'logs'/f"{case['id']}.stderr.txt").open('wb') as err:
            process=subprocess.run(cmd,stdout=out,stderr=err)
        result_path=case_dir/'result.json'
        result=json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {'status':'runner_failed'}
        return {**case,**result,'exit_code':process.returncode}
    print(f'Starting frozen batch: {len(cases)} references, {a.workers} workers, no retries',flush=True)
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        futures={pool.submit(execute,case):case for case in cases}
        for future in as_completed(futures):
            case=futures[future]
            try: result=future.result()
            except Exception as exc: result={**case,'status':'runner_failed','error':str(exc)}
            results.append(result);save('progress.json',{'complete':len(results),'total':len(cases),'results':results})
            print(json.dumps({'case':case['id'],'status':result['status'],'seconds':result.get('elapsed_seconds'),'complete':len(results),'total':len(cases)},ensure_ascii=False),flush=True)
    changed=[rel for rel,sha in hashes.items() if hashlib.sha256((frozen/rel).read_bytes()).hexdigest()!=sha]
    save('batch-result.json',{'results':results,'frozen_files_changed':changed,'complete':len(results),'total':len(cases)})
    return 0 if not changed and all(x['status']=='completed' for x in results) else 2

if __name__=='__main__':raise SystemExit(main())
