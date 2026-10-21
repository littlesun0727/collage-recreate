"""Local manual editing, serialized without loading the model client or rendering engine in the server."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from .store import read,clean
from .chat import save,now,process_alive,Conflict

REPO=Path(__file__).resolve().parents[2]


class EditorError(ValueError):
    def __init__(self,message,log):
        super().__init__(message)
        self.log=log


class Editor:
    def __init__(self,store):
        self.store=store;self.guard=threading.RLock();self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='manual-editor')
        self.active=set()

    def close(self):self.executor.shutdown(wait=True)

    def location(self,task):
        run=self.store.tasks().get(task)
        if run is None:raise KeyError('Task')
        return run

    def base(self,task):
        run=self.location(task);doc=self.store.document(task)
        if not doc['versions']:raise ValueError('尚无可编辑成图')
        result=read(run/'result.json');version=doc['versions'][-1]
        scene_hash=hashlib.sha256((run/'scene.json').read_bytes()).hexdigest()
        if version['render_id']!=result.get('render_id') or result.get('scene_sha256')!=scene_hash:raise Conflict('成图正在更新，请稍后重试')
        return run,version,scene_hash

    def command(self,run,action,request):
        identifier=request['request_id'];root=run/'chat/requests'/identifier;root.mkdir(parents=True,exist_ok=True)
        saved=root/'editor-input.json'
        if saved.exists() and read(saved)!=request:raise Conflict('请求编号已用于其他内容')
        save(saved,request)
        lock=run/'.write.lock';owner=read(lock)
        if owner.get('request_id')==identifier and not process_alive(owner.get('pid')):lock.unlink(missing_ok=True)
        output=root/'editor-output.json';env=dict(os.environ);env['COLLAGE_REQUEST_ID']=identifier
        log=root/'editor-log.txt'
        log_hint=log.relative_to(run).as_posix()
        try:
            outcome=subprocess.run([sys.executable,'-B',str(REPO/'engine/scripts/manual_edit.py'),'--run',str(run),
                '--action',action,'--request',str(saved),'--output',str(output)],
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=300)
        except subprocess.TimeoutExpired as exc:
            log.write_bytes((exc.output or b'')+b'\nEditor process timed out after 300 seconds.\n')
            raise EditorError('画布处理超过 300 秒，请查看日志后重试',log_hint) from exc
        except OSError as exc:
            log.write_text(str(exc),encoding='utf8')
            raise EditorError('无法启动画布处理程序，请查看日志',log_hint) from exc
        log.write_bytes(outcome.stdout)
        if outcome.returncode:
            message=outcome.stdout.decode('utf8',errors='replace')[-3000:]
            try:message=json.loads(message)['error']
            except (ValueError,KeyError):pass
            message=str(message)
            for location in (run,REPO,Path.home()):
                for value in (str(location),location.as_posix()):message=message.replace(value,'[local]')
            raise EditorError(clean(message.strip())[:600] or f'画布处理程序退出，状态码 {outcome.returncode}',log_hint)
        return read(output)

    def expose(self,task,document):
        run=self.location(task);value=dict(document)
        value['objects']=[{k:v for k,v in o.items() if k not in {'file','crop_source_file'}}|
                          {'image':self.store.image(task,run,o['file']),
                           'crop_image':self.store.image(task,run,o.get('crop_source_file'))} for o in document['objects']]
        value['image']=self.store.image(task,run,document['final_file']);value.pop('final_file',None)
        return value

    def document(self,task):
        run,version,scene_hash=self.base(task)
        # Include renderer source identity so a cached editor can never outlive a
        # change in how layers are exported.
        code=hashlib.sha256(b''.join(p.read_bytes() for p in sorted((REPO/'engine/scripts').glob('*.py')))).hexdigest()[:12]
        identifier='editor-export-'+scene_hash[:24]+'-'+code
        request={'request_id':identifier,'base_render_id':version['render_id'],'base_scene_sha256':scene_hash,'changes':[]}
        output=run/'chat/requests'/identifier/'editor-output.json'
        if output.exists():data=read(output)
        else:data=self.executor.submit(self.command,run,'export',request).result()
        data=self.expose(task,data);data['base_version_id']=version['id']
        return data

    def submit(self,task,action,payload):
        if action not in ['preview','save']:raise ValueError('Unknown editor action')
        identifier=payload.get('request_id')
        if not isinstance(identifier,str) or not re.fullmatch(r'edit-[a-zA-Z0-9_-]{8,72}',identifier):raise ValueError('编辑请求编号无效')
        required={'request_id','base_version_id','changes'}
        if not required<=payload.keys() or set(payload)-required-{'layer_order'} or not isinstance(payload['changes'],list) or len(payload['changes'])>200 or (not payload['changes'] and not payload.get('layer_order')):raise ValueError('编辑请求格式无效')
        if 'layer_order' in payload and (not isinstance(payload['layer_order'],list) or not 1<=len(payload['layer_order'])<=1000 or any(not isinstance(i,str) for i in payload['layer_order'])):raise ValueError('图层顺序格式无效')
        run=self.location(task);path=run/'chat/editor-jobs'/(identifier+'.json')
        with self.guard:
            old=read(path)
            if old:
                if old['payload']!=payload or old['action']!=action:raise Conflict('请求编号已用于其他内容')
                if old['status'] in ['completed'] or (task,identifier) in self.active:return old
                job=old
            else:
                run,version,scene_hash=self.base(task)
                if payload['base_version_id']!=version['id']:raise Conflict('基础版本已变化，请重新载入最新成图')
                if (run/'.write.lock').exists():raise Conflict('任务正在处理，请稍后保存')
                job={'id':identifier,'action':action,'payload':payload,'base_render_id':version['render_id'],
                     'base_scene_sha256':scene_hash,'created_at':now(),'attempts':0}
            job.update(status='queued',error=None);save(path,job);self.active.add((task,identifier))
            self.executor.submit(self.work,task,path,job)
            return job

    def work(self,task,path,job):
        start=time.monotonic()
        try:
            job.update(status='running',started_at=now(),attempts=job['attempts']+1);save(path,job)
            payload=job['payload'];request={'request_id':job['id'],'base_render_id':job['base_render_id'],
                'base_scene_sha256':job['base_scene_sha256'],'changes':payload['changes']}
            if 'layer_order' in payload:request['layer_order']=payload['layer_order']
            result=self.command(self.location(task),job['action'],request)
            job['result']=result;job['status']='completed'
            if job['action']=='save':
                versions=self.store.document(task)['versions']
                job['version_id']=next(v['id'] for v in versions if v['render_id']==result['render_id'])
        except Exception as exc:job.update(status='failed',error=clean(str(exc))[:600])
        finally:
            job.update(finished_at=now(),elapsed_seconds=round(time.monotonic()-start,3));save(path,job)
            with self.guard:self.active.discard((task,job['id']))

    def job(self,task,identifier):
        if not re.fullmatch(r'edit-[a-zA-Z0-9_-]{8,72}',identifier):raise KeyError('Job')
        job=read(self.location(task)/'chat/editor-jobs'/(identifier+'.json'))
        if not job:raise KeyError('Job')
        value={k:v for k,v in job.items() if k not in ['payload','result','base_scene_sha256']}
        with self.guard:
            if value['status'] in ['queued','running'] and (task,identifier) not in self.active:
                value.update(status='failed',error='服务已重启，草稿已保留。重新保存可恢复本轮。')
        if job.get('result') and job['action']=='preview':value['preview']=self.expose(task,job['result'])
        return value
