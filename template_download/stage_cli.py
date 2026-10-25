"""Independent, restartable collect / screen / review commands sharing durable files."""
import argparse
import asyncio
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time

from browser import AccessStopped
from mediacrawler_adapter import import_export
from run import lock
from stage_media import collect_media, screen_local, sync_raw_assets, raw_complete_count
from store import Store
from paths import DATA_ROOT, DEFAULT_DATASET

BASE=Path(__file__).resolve().parent


@contextmanager
def stage_lock(root,stage):
    """One worker per stage per dataset; only collection owns the browser lock."""
    with ExitStack() as stack:
        paths=[root/f'.{stage}.lock']
        if stage=='collect':paths.append(DATA_ROOT/'.worker.lock')
        for path in paths:
            path.parent.mkdir(parents=True,exist_ok=True)
            guard=stack.enter_context(path.open('a+b'))
            try:lock(guard)
            except OSError:raise RuntimeError(f'{stage} worker or browser is already running') from None
        yield


def counts(store):
    from pipeline import candidates, state_get
    sync_raw_assets(store)
    return {'updated_utc':datetime.now(timezone.utc).isoformat(),
            'collection_requires_attention':state_get(store,'collection_requires_attention',False),
            'cursor':state_get(store,'cursor',{'keyword':0,'page':1,'offset':0}),
            'last_collection':state_get(store,'last_collection',None),
            'raw_complete':raw_complete_count(store),
            'raw_assets':[{ 'kind':r[0],'status':r[1],'count':r[2]} for r in store.db.execute(
                'SELECT kind,status,COUNT(*) FROM raw_assets GROUP BY kind,status')],
            'downloaded_waiting_screen':store.db.execute("SELECT COUNT(*) FROM images WHERE status='downloaded'").fetchone()[0],
            'screen_ready':store.db.execute("SELECT COUNT(*) FROM images WHERE status='ready'").fetchone()[0],
            'review_queue':len(candidates(store)),
            'reviews':dict(store.db.execute("SELECT review,COUNT(*) FROM images WHERE status='ready' GROUP BY review")),
            'latest_runs':[dict(r) for r in store.db.execute('''SELECT * FROM stage_runs WHERE id IN
                (SELECT MAX(id) FROM stage_runs GROUP BY stage) ORDER BY id''')],
            'legacy':store.stats()}


def publish(store,stage):
    # Stage-local exports avoid collect/screen overwriting review's accepted manifest.
    snapshot=counts(store)
    tmp=store.root/f'{stage}-status.json.tmp'
    tmp.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(tmp,store.root/f'{stage}-status.json')
    if stage in ('review','export'):
        store.export(store.root/'accepted.jsonl')
        store.export(store.root/'candidates.jsonl',candidates=True)
        from dataset_preview import build
        build(store.root)
    return snapshot


def collect_worker(store,cfg,args,cursor):
    """Bounded recovery of transport/crash failures; access blocks never auto-retry."""
    from pipeline import state_set
    for retry in range(args.worker_retries+1):
        output=store.root/'collections'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        output.mkdir(parents=True)
        state_set(store,'active_collection',{'cursor':cursor,'output':str(output.relative_to(store.root)),
                                          'retry':retry,'started_utc':datetime.now(timezone.utc).isoformat()})
        publish(store,'collect')
        env=os.environ.copy();env['PYTHONUTF8']='1'
        command=[str(BASE/'.venv-mediacrawler/Scripts/python.exe'),str(BASE/'mediacrawler_worker.py'),
            '--keyword',cfg['keywords'][cursor['keyword']],'--notes','5','--page',str(cursor['page']),
            '--offset',str(cursor['offset']),'--delay',str(args.delay),'--output',str(output)]
        proc=subprocess.Popen(command,cwd=BASE,env=env)
        try:
            proc.wait(timeout=args.worker_timeout)
        except (subprocess.TimeoutExpired,KeyboardInterrupt) as exc:
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
            else:proc.kill()
            proc.wait(timeout=15)
            if isinstance(exc,KeyboardInterrupt):raise
        if (output/'notes.jsonl').exists():
            import_export(store,output/'notes.jsonl',recover_partial=True)
            state_set(store,'imported_collection:'+output.name,True)
        report=output/'collection-report.json'
        result=json.loads(report.read_text(encoding='utf-8')) if report.exists() else {'status':'error','reason':'WorkerExitedWithoutReport'}
        if not report.exists():report.write_text(json.dumps(result),encoding='utf-8')
        state_set(store,'last_collection',{'output':str(output.relative_to(store.root)),
                                         'status':result['status'],'cursor':cursor,'retry':retry})
        if result['status']=='blocked' or any(r.get('http_status') in (401,403,429,461,471)
                for r in result.get('http_responses',[])):
            state_set(store,'collection_requires_attention',True)
            raise AccessStopped('Collection requires platform verification; see '+str(report))
        if result['status']=='empty' or (proc.returncode==0 and result['status']=='success'):return result
        if retry==args.worker_retries:raise RuntimeError('Collection recovery exhausted; cursor preserved; see '+str(report))
        print(f'Collection worker failed; restarting at same cursor ({retry+1}/{args.worker_retries}).',flush=True)
        time.sleep(args.delay)


def run_collect(store,cfg,args):
    from pipeline import state_get, state_set
    if state_get(store,'collection_requires_attention',False) and not args.resume_access:
        raise AccessStopped('Collection is paused for access verification. Complete it before --resume-access.')
    if args.resume_access:
        with store.db:
            store.db.execute("UPDATE raw_assets SET status='pending',attempts=0,error=NULL WHERE status='blocked'")
        state_set(store,'collection_requires_attention',False)
    if args.input:print(import_export(store,args.input),flush=True)
    # Recover partial JSONL exports left by a terminated worker. The unique note/image
    # keys make this idempotent; do not advance the search cursor on an incomplete call.
    for folder in sorted((store.root/'collections').glob('*')):
        if not (folder/'notes.jsonl').exists():continue
        marker='imported_collection:'+folder.name
        if state_get(store,marker,False):continue
        import_export(store,folder/'notes.jsonl',recover_partial=True)
        if (folder/'collection-report.json').exists():state_set(store,marker,True)
    for attempt in range(args.collect_calls+1):
        while True:
            processed=asyncio.run(collect_media(store,cfg,args.limit))
            if not processed:break
            publish(store,'collect')
        if raw_complete_count(store)>=cfg['target']:
            print('Raw material target reached (before screening).',flush=True);break
        saved=store.db.execute("SELECT COUNT(*) FROM raw_assets WHERE kind='cover' AND status='ready'").fetchone()[0]
        if saved>=cfg['target']:
            print('Cover target reached; incomplete Live pairs require attention.',flush=True);break
        if attempt==args.collect_calls:break
        cursor=state_get(store,'cursor',{'keyword':0,'page':1,'offset':0})
        if cursor['keyword']>=len(cfg['keywords']):break
        result=collect_worker(store,cfg,args,cursor)
        cursor['offset']+=5
        if cursor['offset']>=result['search_results']:
            cursor['offset']=0;cursor['page']+=1
            if not result.get('has_more') or cursor['page']>cfg.get('scrolls_per_keyword',30):
                cursor={'keyword':cursor['keyword']+1,'page':1,'offset':0}
        state_set(store,'cursor',cursor)
        time.sleep(args.delay)  # Existing request cadence; optimization is a separate task.


def run_screen(store,cfg,args):
    total=0
    while True:
        done=screen_local(store,cfg,args.limit);total+=done
        if done:publish(store,'screen')
        if done>=args.limit:continue
        if not args.watch:break
        time.sleep(args.poll_interval)
    return total


def run_review(store,cfg,args):
    from pipeline import candidates, review_cycle
    from vlm_batch import CodexServer
    server=None;total=0
    try:
        while True:
            waiting=len(candidates(store))
            if waiting<100 and not (args.flush and waiting):
                if not args.watch:break
                time.sleep(args.poll_interval);continue
            # No network/model initialization until an eligible review batch exists.
            if server is None:
                runtime=store.root/'vlm-runtime';runtime.mkdir(exist_ok=True)
                server=CodexServer(runtime)
                models={m['model']:m for m in server.models()}
                if args.model not in models:raise RuntimeError('Requested model unavailable: '+args.model)
                if 'low' not in [e['reasoningEffort'] for e in models[args.model]['supportedReasoningEfforts']]:
                    raise RuntimeError('Selected model does not support low reasoning')
            total+=review_cycle(store,server,args.model,100,args.concurrency,cfg)
            publish(store,'review')
            if args.cooldown:time.sleep(args.cooldown)
    finally:
        if server:server.close()
    return total


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['login','collect','screen','review','status','export'])
    parser.add_argument('--data-dir',type=Path,default=DEFAULT_DATASET)
    parser.add_argument('--config',type=Path,default=BASE/'config.1000.json')
    parser.add_argument('--input',type=Path,help='collect: import existing note exports')
    parser.add_argument('--collect-calls',type=int,default=0,help='collect: bounded search calls (5 notes each); 0 downloads existing imports only')
    parser.add_argument('--worker-timeout',type=float,default=360,help='Hard deadline per collection subprocess in seconds')
    parser.add_argument('--worker-retries',type=int,default=2,help='Restarts after worker failure; never retries access restrictions')
    parser.add_argument('--limit',type=int,default=1000,help='Local work chunk size (not the total dataset target)')
    parser.add_argument('--target',type=int,help='collect: total raw cover limit, before quality/dedup/VLM')
    parser.add_argument('--model',default='gpt-6-luna')
    parser.add_argument('--concurrency',type=int,default=2,help='review: existing VLM concurrency')
    parser.add_argument('--delay',type=float,default=15,help='collect: existing note and batch delay')
    parser.add_argument('--cooldown',type=float,default=0,help='review: extra delay, default disabled')
    parser.add_argument('--flush',action='store_true',help='review: also process the remaining <100 candidates')
    parser.add_argument('--watch',action='store_true',help='screen/review: wait for new eligible local files')
    parser.add_argument('--poll-interval',type=float,default=5,help='Idle local queue polling; not a request delay')
    parser.add_argument('--resume-access',action='store_true',help='collect: resume after the user resolves access verification')
    args=parser.parse_args()
    if args.worker_timeout<30 or not 0<=args.worker_retries<=5:parser.error('worker-timeout >=30; worker-retries 0..5')
    if args.limit<1 or args.collect_calls<0 or args.delay<10 or args.cooldown<0 or args.poll_interval<1 or not 1<=args.concurrency<=4:
        parser.error('Invalid bounds; existing delay/concurrency limits are unchanged')
    if args.target is not None and args.target<1:parser.error('target must be positive')
    if args.watch and args.stage not in ('screen','review'):parser.error('watch is only for screen/review')
    if args.input and args.stage!='collect':parser.error('input is only for collect')
    root=args.data_dir.resolve()
    root.mkdir(parents=True,exist_ok=True)
    cfg=json.loads(args.config.read_text(encoding='utf-8-sig'))
    if args.target is not None:cfg['target']=args.target
    if not 1<=cfg['download_concurrency']<=8 or cfg['max_attempts']<1 or cfg['download_delay_seconds']<0:
        parser.error('Invalid download limits')
    if not -1<=cfg['near_duplicate_distance']<=4:parser.error('near_duplicate_distance must be -1..4')
    # Export uses review's lock to avoid two writers producing gallery/accepted.jsonl.
    with stage_lock(root,'review' if args.stage=='export' else 'collect' if args.stage=='login' else args.stage):
        store=Store(root);run_id=None;state='completed';error=None
        try:
            sync_raw_assets(store)
            if args.stage in ('status','export'):
                print(json.dumps(publish(store,args.stage),ensure_ascii=False,indent=2));return
            with store.db:
                run_id=store.db.execute('INSERT INTO stage_runs(stage,state) VALUES(?,?)',(args.stage,'running')).lastrowid
            if args.stage=='login':
                from browser import browse
                cfg.update(browser_channel='chrome',browser_profile=str(DATA_ROOT/'browser-profile'))
                asyncio.run(browse(store,cfg,'login',1))
            elif args.stage=='collect':run_collect(store,cfg,args)
            elif args.stage=='screen':run_screen(store,cfg,args)
            else:run_review(store,cfg,args)
        except AccessStopped:
            state='blocked';error='AccessStopped'
            if args.stage=='collect':
                from pipeline import state_set
                state_set(store,'collection_requires_attention',True)
            raise
        except KeyboardInterrupt:
            state='interrupted';error='KeyboardInterrupt';raise
        except Exception as exc:
            state='error';error=type(exc).__name__;raise
        finally:
            try:
                if run_id is not None:
                    with store.db:
                        store.db.execute('UPDATE stage_runs SET state=?,error=?,finished=CURRENT_TIMESTAMP WHERE id=?',(state,error,run_id))
                    print(json.dumps(publish(store,args.stage),ensure_ascii=False),flush=True)
            finally:store.db.close()


if __name__=='__main__':main()
