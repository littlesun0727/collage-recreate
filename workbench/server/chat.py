"""Durable, single-worker revision queue; the browser never runs a production command."""
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from .store import read, clean

REPO=Path(__file__).resolve().parents[2]
ACTIVE={'queued','running'}

def now():return datetime.now(timezone.utc).isoformat()

def process_alive(pid):
    if not isinstance(pid,int) or pid<=0:return True
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle:return ctypes.get_last_error()!=87
        code=wintypes.DWORD()
        try:return not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
        finally:kernel.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8');os.replace(temp,path)

class Conflict(ValueError):pass

class ChatQueue:
    def __init__(self,store,path,agent=None,start=True):
        self.store=store;self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.agent=agent or self.run_agent;self.guard=threading.RLock();self.stop=threading.Event();self.wake=threading.Event()
        self.lock=self.path.with_suffix('.lock').open('a+b');self.lock.seek(0,2)
        if not self.lock.tell():self.lock.write(b'0');self.lock.flush()
        self.lock.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, task TEXT NOT NULL, created REAL NOT NULL, body TEXT NOT NULL)')
            for row in db.execute('SELECT body FROM requests'):
                job=json.loads(row[0])
                if job['status']=='running':
                    job.update(status='failed',message='服务重启中断了本轮修改，可重试；上一版本仍保留。',finished_at=now())
                    db.execute('UPDATE requests SET body=? WHERE id=?',(json.dumps(job,ensure_ascii=False),job['id']))
        self.thread=threading.Thread(target=self.loop,daemon=True,name='collage-revision-worker')
        if start:self.thread.start()

    @contextmanager
    def db(self):
        conn=sqlite3.connect(self.path,timeout=30)
        try:
            with conn:yield conn
        finally:conn.close()

    def close(self):
        self.stop.set();self.wake.set()
        if self.thread.is_alive():self.thread.join(timeout=2)
        if not self.thread.is_alive():self.lock.close()

    def rows(self,task=None):
        with self.db() as db:
            rows=db.execute('SELECT body FROM requests'+(' WHERE task=?' if task else '')+' ORDER BY created,id', (task,) if task else ())
            return [json.loads(r[0]) for r in rows]

    def put(self,job):
        with self.db() as db:db.execute('UPDATE requests SET body=? WHERE id=?',(json.dumps(job,ensure_ascii=False),job['id']))

    def public(self,task):
        rows=self.rows(task);allrows=self.rows()
        for job in rows:
            job.pop('plan',None);job.pop('diagnostic',None)
            job['queue_position']=sum(x['status'] in ACTIVE and x['created_at']<=job['created_at'] for x in allrows) if job['status']=='queued' else 0
            if job.get('started_at'):
                end=job.get('finished_at') or now()
                job['elapsed_seconds']=round((datetime.fromisoformat(end)-datetime.fromisoformat(job['started_at'])).total_seconds(),1)
        return {'enabled':True,'requests':clean(rows),'concurrency':1}

    def submit(self,task,payload):
        run=self.store.tasks().get(task)
        if run is None:raise KeyError('Task not found')
        identifier=payload.get('request_id','');message=payload.get('message','')
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',identifier):raise ValueError('Invalid request ID')
        if not isinstance(message,str) or not 1<=len(message.strip())<=4000:raise ValueError('请输入1到4000字的修改要求')
        with self.guard:
            for old in self.rows():
                if old['id']==identifier:
                    if old['task_id']!=task or old['request']!=payload:raise Conflict('重复请求的内容不一致')
                    return old
            doc=self.store.document(task);latest=doc['versions'][-1] if doc['versions'] else None
            if not latest or latest['id']!=payload.get('base_version_id'):raise Conflict('当前查看的是历史版本，请回到最新版本后修改')
            scene=read(run/'scene.json');ids={o['id'] for o in scene.get('objects',[])}
            selected=payload.get('selected_ids',[])
            if not isinstance(selected,list) or any(not isinstance(i,str) or i not in ids for i in selected) or len(selected)>20:raise ValueError('Invalid selected objects')
            asset=payload.get('asset_id')
            if asset and asset not in {a['id'] for a in read(run/'prepared/catalog.json').get('assets',[])}:raise ValueError('Unknown customer image')
            reply=payload.get('reply_to')
            if reply and not any(j['id']==reply and j['status']=='clarify' for j in self.rows(task)):raise ValueError('Invalid clarification')
            if doc.get('live') or (run/'.write.lock').exists():raise Conflict('任务当前正在制作，请稍后发送修改')
            job={'id':identifier,'task_id':task,'request':payload,'base_render_id':read(run/'result.json')['render_id'],
                 'base_version_id':latest['id'],'created_at':now(),'status':'queued','phase':'queued',
                 'message':'已排队，轮到后开始理解修改。','timings':{},'attempts':0}
            with self.db() as db:db.execute('INSERT INTO requests VALUES (?,?,?,?)',(identifier,task,time.time(),json.dumps(job,ensure_ascii=False)))
            self.wake.set();return job

    def retry(self,task,identifier):
        with self.guard:
            job=next((j for j in self.rows(task) if j['id']==identifier),None)
            if not job:raise KeyError(identifier)
            if job['status']!='failed':raise Conflict('仅执行失败的请求可以重试')
            run=self.store.tasks()[task]
            committed=read(run/'chat/requests'/identifier/'committed.json')
            marker=read(run/'chat/commit.json');interrupted=marker.get('request_id')==identifier and marker.get('status') in ['promoting','done']
            if not committed and not interrupted and read(run/'result.json').get('render_id')!=job['base_render_id']:raise Conflict('版本已变化，请基于最新成图重新发送')
            job.update(status='queued',phase='queued',message='已重新排队，将复用已完成的修改步骤。')
            job.pop('finished_at',None);self.put(job);self.wake.set();return job

    def loop(self):
        while not self.stop.is_set():
            with self.guard:
                job=next((j for j in self.rows() if j['status']=='queued'),None)
                if job:
                    job.update(status='running',started_at=now(),attempts=job['attempts']+1)
                    job['queue_seconds']=round((datetime.fromisoformat(job['started_at'])-datetime.fromisoformat(job['created_at'])).total_seconds(),1)
                    self.put(job)
            if job:self.process(job)
            else:self.wake.wait(1);self.wake.clear()

    def phase(self,job,name,message):
        job.update(phase=name,phase_started_at=now(),message=message);self.put(job)

    def command(self,run,root,command,file,identifier=None):
        lock=run/'.write.lock';owner=read(lock)
        if identifier and owner.get('request_id')==identifier and not process_alive(owner.get('pid')):lock.unlink(missing_ok=True)
        args=[sys.executable,'-B',str(REPO/'skill/scripts/workflow.py'),command,'--run',str(run),'--brief']
        if file:args+=['--file',str(file)]
        if identifier:args+=['--request-id',identifier]
        env=dict(os.environ)
        if identifier:env['COLLAGE_REQUEST_ID']=identifier
        result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=600)
        (root/(command+'-log.txt')).write_bytes(result.stdout)
        if result.returncode:
            output=result.stdout.decode('utf8',errors='replace')[-4000:]
            try:output=json.loads(output)['error']
            except (ValueError,KeyError):pass
            raise RuntimeError(output)

    def run_agent(self,mode,root,context,images):
        input_path=root/(mode+'-input.json');output=root/(mode+'-output.json')
        save(input_path,{'mode':mode,'context':context,'images':[str(i) for i in images]})
        result=subprocess.run(['node',str(REPO/'workbench/agent/revise.mjs'),str(input_path),str(output)],
           stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=660)
        (root/(mode+'-agent.log')).write_bytes(result.stdout)
        if result.returncode:raise RuntimeError('SDK执行失败：'+result.stdout.decode('utf8',errors='replace')[-2000:])
        return read(output)['answer']

    def process(self,job):
        run=self.store.tasks().get(job['task_id']);root=None
        try:
            if run is None:raise ValueError('Task no longer available')
            root=run/'chat/requests'/job['id'];root.mkdir(parents=True,exist_ok=True)
            marker=read(run/'chat/commit.json')
            if marker.get('request_id')==job['id'] and marker.get('status')=='promoting':
                self.command(run,root,'revision-recover',None,job['id'])
            if marker.get('request_id')==job['id'] and marker.get('status')=='done' and marker.get('outcome') and not (root/'committed.json').exists():
                save(root/'committed.json',marker['outcome'])
            if not (root/'committed.json').exists() and read(run/'result.json').get('render_id')!=job['base_render_id']:
                job.update(status='conflict',message='排队期间成图已更新。请查看最新版本，再发送这条修改。');return
            scene=read(run/'scene.json')
            context={'message':job['request']['message'],'selected_ids':job['request'].get('selected_ids',[]),
              'reply_to':job['request'].get('reply_to'),
              'selected_asset_id':job['request'].get('asset_id'),'reference_size':scene['reference_size'],
              'objects':[{k:o[k] for k in ['id','label','kind','bbox','rotation','style','text','method','mode','binding','parent_id','photo_id'] if k in o} |
                         {'protected':bool(o.get('recovered') or o.get('embedded_owner') or o.get('photo_window')),
                          'extracted':bool(o.get('recovered')),'fixed_window':bool(o.get('photo_window')),'generated':bool(o.get('generated'))}
                         for o in scene['objects']], 'layer_order':scene['layer_order'],
              'catalog':[{'id':a['id'],'name':Path(a['file']).name} for a in read(run/'prepared/catalog.json')['assets']],
              'previous_review':read(run/'result.json').get('visual_review'),
              'conversation':[{'request_id':j['id'],'customer':j['request']['message'],'assistant':j['message']} for j in self.rows(job['task_id']) if j['created_at']<job['created_at']][-12:]}
            if not (root/'plan.json').exists():
                self.phase(job,'understanding','正在结合成图和选中对象理解修改。');start=time.monotonic()
                images=[run/'prepared/reference.png',run/'final.png']
                if job['request'].get('asset_id'):
                    images.append(Path(next(a['file'] for a in read(run/'prepared/catalog.json')['assets'] if a['id']==job['request']['asset_id'])))
                answer=self.agent('plan',root,context,images);job['timings']['understanding']=round(time.monotonic()-start,3)
                if answer['decision']!='edit':
                    job.update(status=answer['decision'],message=answer.get('question') or answer['summary']);return
                if not answer['changes'] and not answer['layer_order']:raise ValueError('没有可执行的修改')
                plan={'schema_version':'collage-review-v1','render_id':job['base_render_id'],'verdict':'needs_changes',
                      'summary':answer['summary'],'items':[{'id':c['id'],'action':'adjust','reason':c['reason'],
                          'changes':json.loads(c['changes_json'])} for c in answer['changes']]}
                if answer['layer_order']:plan['layer_order']=answer['layer_order']
                save(root/'plan.json',plan)
            job['summary']=read(root/'plan.json')['summary']
            if not (root/'candidate.json').exists() and not (root/'committed.json').exists():
                self.phase(job,'editing',job['summary']);start=time.monotonic()
                self.command(run,root,'revision-prepare',root/'plan.json',job['id'])
                job['timings']['editing']=round(time.monotonic()-start,3)
            if not (root/'review.json').exists() and not (root/'committed.json').exists():
                self.phase(job,'reviewing','正在查看修改前后，检查裁切、文字和遮挡。');start=time.monotonic()
                candidate=root/'candidate';result=read(candidate/'result.json')
                context.update(actual_scene=read(candidate/'scene.json'),actual_changes=read(root/'plan.json'),
                               incomplete_objects=result['incomplete_objects'])
                answer=self.agent('review',root,context,[run/'prepared/reference.png',run/'final.png',candidate/'final.png']+
                                  [Path(r['file']) for r in result.get('review_regions',[])])
                review={'schema_version':'collage-review-v1','render_id':result['render_id'],'checked_entire_composition':True,
                    'verdict':answer['verdict'],'summary':answer['summary'],
                    'items':[{'id':i['id'],'action':'unresolved','reason':i['reason']} for i in answer['issues']]}
                save(root/'review.json',review);job['timings']['reviewing']=round(time.monotonic()-start,3)
            self.phase(job,'publishing','复核结束，正在保存新版本。');start=time.monotonic()
            self.command(run,root,'revision-commit',root/'review.json',job['id'])
            job['timings']['publishing']=round(time.monotonic()-start,3)
            doc=self.store.document(job['task_id']);committed=read(root/'committed.json')
            latest=next(v for v in doc['versions'] if v['render_id']==committed['render_id'])
            job.update(status='completed',phase='completed',version_id=latest['id'],message=read(root/'review.json')['summary'],
                       verdict=read(root/'review.json')['verdict'])
            # Delivery is recorded only after the actual reviewed version is published.
            args=[sys.executable,'-B',str(REPO/'skill/scripts/workflow.py'),'progress','--run',str(run),'--stage','6','--status','complete','--summary',job['message']]
            if read(run/'result.json').get('render_id')==committed['render_id']:
                subprocess.run(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=30,check=True)
        except Exception as exc:
            if job.get('phase_started_at'):
                job['timings'][job['phase']]=round((datetime.now(timezone.utc)-datetime.fromisoformat(job['phase_started_at'])).total_seconds(),3)
            job.update(status='failed',diagnostic=str(exc),message='本轮修改未完成，上一版本已保留。'+clean(str(exc))[:400])
            if root:save(root/'error.json',{'at':now(),'error':str(exc)})
        finally:
            job['finished_at']=now();self.put(job)
