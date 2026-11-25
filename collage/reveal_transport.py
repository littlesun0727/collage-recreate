import base64
import json as jsonlib
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from PIL import Image
from .storage import read, save, now, fingerprint, sha

BASE = 'https://api.research.360.cn/v1'
_submission_budget_lock=threading.Lock()
SUBMIT_RETRY_CODES={502,503,504}
QUERY_RETRY_CODES={408,429,500,502,503,504}


def reserve_submission(task, budget_root, limit=None):
    """Reserve each new submission durably, including retries and interrupted submits."""
    MAX_REQUESTS = 32
    task=Path(task);budget_root=Path(budget_root)
    if limit is None:
        budget_file=budget_root/'submission-budget.json'
        limit=read(budget_file)['limit'] if budget_file.exists() else MAX_REQUESTS
    if type(limit) is not int or limit < 1:raise ValueError('Reveal submission limit must be a positive integer')
    with _submission_budget_lock:
        marker=task/'budget-reserved.json'
        if marker.exists():
            reservation=read(marker)
            if 'budget_root' not in reservation:
                save(marker,{**reservation,'budget_root':str(budget_root.resolve())})
            return
        used={p.parent for name in ['budget-reserved.json','submission_started.json','submit_response.json'] for p in budget_root.rglob(name)}
        if task not in used and len(used)>=limit:raise ValueError(f'Hard limit: at most {limit} Reveal submissions per run')
        save(marker,{'reserved_at':now(),'limit':limit,'budget_root':str(budget_root.resolve())})


def http(url, json=None, headers=None, timeout=120):
    """Small standard-library transport, shared with injectable HTTP test doubles."""
    payload=jsonlib.dumps(json).encode('utf-8') if json is not None else None
    request=Request(url,data=payload,headers={**(headers or {}),**({'Content-Type':'application/json'} if payload else {})})
    with urlopen(request,timeout=max(timeout) if isinstance(timeout,tuple) else timeout) as response:
        content=response.read()
    class Response:
        def raise_for_status(self):pass
        def json(self):return jsonlib.loads(self.content)
    result=Response();result.content=content;return result


def fetch_batch(task, reference, objects, key, timeout=360, post=None, get=None, sleep=time.sleep):
    """Retry explicit gateway failures within the run budget; retain uncertain submits."""
    post=post or http;get=get or http
    task=Path(task);task.mkdir(parents=True,exist_ok=True)
    identity={'reference_sha256':sha(reference),'objects':objects,'model':'ecommerce_layer',
              'version':'v1.1','seed':42,'steps':28}
    digest=fingerprint(identity);mp=task/'request_meta.json'
    if mp.exists() and read(mp)['sha256']!=digest:raise ValueError('Reveal request changed in an existing task')
    save(mp,{**identity,'sha256':digest})
    deadline=time.monotonic()+timeout
    timing_file=task/'timing.json';timings=read(timing_file) if timing_file.exists() else {'started_at':now()}
    def elapsed(stage,started):
        timings[stage]=round(timings.get(stage,0)+time.monotonic()-started,3);timings['updated_at']=now();save(timing_file,timings)
    def request(endpoint,payload):
        left=deadline-time.monotonic()
        if left<=0:raise TimeoutError('Reveal task timeout; resume with the same cache')
        started=time.monotonic()
        try:
            response=post(BASE+'/'+endpoint,json=payload,headers={'Authorization':'Bearer '+key},timeout=(min(30,left),min(120,left)))
            response.raise_for_status();return response.json()
        finally:elapsed('submit_seconds' if endpoint=='submit_task' else 'query_seconds',started)
    def pause_retry(endpoint,attempt,code=None):
        delay=2**attempt
        if deadline-time.monotonic()<=delay:
            raise TimeoutError('Reveal retry deadline reached; resume with the same cache')
        report=task/'http-retries.json'
        records=read(report) if report.exists() else []
        records.append({'endpoint':endpoint,'after_attempt':attempt,'http_status':code,'wait_seconds':delay,'at':now()})
        save(report,records);sleep(delay)
    submitted=task/'submit_response.json'
    if not submitted.exists():
        payload={'model':'ecommerce_layer','version':'v1.1','pipeline_type':'crop','time_out':3600,
                 'seed':42,'steps':28,'image_boxes':[o['bbox'] for o in objects],
                 'input':[{'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(Path(reference).read_bytes()).decode()}]}
        marker=task/'submission_started.json'
        reservation=task/'budget-reserved.json'
        budget_root=Path(read(reservation).get('budget_root',task)) if reservation.exists() else task
        while not submitted.exists():
            previous=read(marker) if marker.exists() else None
            attempt=1
            if previous:
                code=previous.get('http_status')
                if code not in SUBMIT_RETRY_CODES:
                    raise ValueError('Reveal submit outcome unknown or non-retryable; do not resubmit automatically')
                attempt=previous.get('attempt',1)+1
                if attempt>3:raise ValueError('Reveal gateway retries exhausted; at most 3 submission attempts')
                # Each new HTTP submit consumes a durable slot, including gateway failures.
                retry_slot=task/'submit-retries'/f'attempt-{attempt:02d}'
                pause_retry('submit_task',attempt-1,code)
                try:reserve_submission(retry_slot,budget_root)
                except ValueError as exc:
                    raise ValueError(f'Reveal submit HTTP {code}; retry stopped: {exc}') from exc
            else:reserve_submission(task,budget_root)
            started={'request_sha256':digest,'attempt':attempt,'started_at':now()}
            save(marker,started)
            history=task/'submit-history'/f'attempt-{attempt:02d}.json'
            save(history,started)
            try:
                response=request('submit_task',payload)
                save(submitted,response)
                save(history,{**started,'finished_at':now(),'task_id':response.get('task_id'),
                              'response_status':response.get('response_status')})
            except (OSError,ValueError) as exc:
                code=getattr(exc,'code',None) if isinstance(exc,HTTPError) else None
                failure={**started,'finished_at':now(),'http_status':code,'error_type':type(exc).__name__}
                save(marker,failure);save(history,failure)
                if code not in SUBMIT_RETRY_CODES or attempt>=3:raise
    result=read(submitted)
    if result.get('response_status')!=0 or not result.get('task_id'):raise ValueError('Reveal submission rejected; inspect saved response')
    query={'model':'ecommerce_layer','version':'v1.1','task_id':result['task_id']}
    qp=task/'query_response.json';result=read(qp) if qp.exists() else {}
    while result.get('status')!='done':
        if result.get('status') in ['failed','not_found','timeout'] or result.get('response_status')==-1:
            raise ValueError('Reveal task failed; saved task will not be resubmitted')
        for attempt in range(1,4):
            try:
                result=request('query_task',query);save(qp,result);break
            except (OSError,ValueError) as exc:
                code=getattr(exc,'code',None)
                transient=(isinstance(exc,HTTPError) and code in QUERY_RETRY_CODES) or (
                    isinstance(exc,(URLError,TimeoutError,ConnectionError)) and not isinstance(exc,HTTPError))
                if not transient or attempt>=3:raise
                pause_retry('query_task',attempt,code)
        if result.get('status')!='done':
            started=time.monotonic();sleep(min(5,max(0,deadline-time.monotonic())));elapsed('poll_wait_seconds',started)
    download_errors=[]
    for field in ['layers_base','layers_aug']:
        for i,url in enumerate(result['output'].get(field,[])):
            path=task/f'{field}_{i:02d}.png'
            if path.exists():
                try:
                    with Image.open(path) as im:im.verify()
                    continue
                except (OSError, ValueError):pass
            for attempt in range(3):
                started=time.monotonic()
                try:
                    left=deadline-time.monotonic()
                    if left<=0:raise TimeoutError('Reveal download timeout; resume existing task')
                    response=get(url,timeout=(min(30,left),min(120,left)));response.raise_for_status()
                    tmp=path.with_suffix('.part');tmp.write_bytes(response.content)
                    with Image.open(tmp) as im:im.verify()
                    tmp.replace(path)
                    break
                except (OSError, ValueError) as exc:
                    code=getattr(exc,'code',None)
                    transient=(isinstance(exc,HTTPError) and code in {408,429,500,502,503,504}) or (isinstance(exc,(URLError,TimeoutError,ConnectionError)) and not isinstance(exc,HTTPError))
                    if transient and attempt<2 and deadline-time.monotonic()>attempt+1:
                        sleep(attempt+1)
                        continue
                    download_errors.append({'file':path.name,'error':type(exc).__name__+(f' HTTP {code}' if code else ''),'attempts':attempt+1})
                    break
                finally:elapsed('download_seconds',started)
    save(task/'download-report.json',{'errors':download_errors,'complete':not download_errors,'at':now()})
    if download_errors:
        raise RuntimeError('Reveal download incomplete: '+'; '.join(e['file']+': '+e['error'] for e in download_errors))


