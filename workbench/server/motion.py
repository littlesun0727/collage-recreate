"""Persisted jobs with independent serial import, creation and render workers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import unquote
from .store import clean
from .chat import now, Conflict, process_alive

REPO = Path(__file__).resolve().parents[2]
IDENTIFIER = re.compile(r'(?:motion|import|batch|create)-[a-zA-Z0-9_-]{8,72}')


def read(path,default=None):
    for attempt in range(9):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8-sig'))
        except PermissionError:
            if attempt==8:raise
            time.sleep(min(.005*2**attempt,.15))
        except FileNotFoundError:
            if attempt==0:
                time.sleep(.005);continue
            return {} if default is None else default
        except (OSError,ValueError):
            return {} if default is None else default


def save(path,value):
    # Keep the server independent of the skill. Windows readers can briefly deny rename.
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex[:12]+'.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
        for attempt in range(9):
            try:
                os.replace(temporary,path);return
            except PermissionError:
                if attempt==8:raise
                time.sleep(min(.005*2**attempt,.15))
    finally:
        temporary.unlink(missing_ok=True)


class Motion:
    def __init__(self, store, media_root, cutout_model=None, creator=None):
        self.store = store
        self.creator = creator
        self.root = Path(media_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.queue_lock = (self.root/'queue.lock').open('a+b')
        self.queue_lock.seek(0, 2)
        if not self.queue_lock.tell():
            self.queue_lock.write(b'0'); self.queue_lock.flush()
        self.queue_lock.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.queue_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.queue_lock.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
        except Exception:
            self.queue_lock.close(); raise
        self.cutout_model = cutout_model
        self.guard = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='live-create')
        self.render_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='live-motion')
        self.import_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='live-import')
        self.active = set()
        self.verified_content = {}
        # Persist queued jobs; recover an orphaned child before starting another sample.
        pending=[(path,read(path)) for path in (self.root/'jobs').glob('*.json')]
        for path,job in sorted(pending,key=lambda item:(item[1].get('status')!='running',self.queue_key(item[1]))):
            if job.get('status') in {'queued', 'running'}:
                if job.get('kind')=='create' and not self.creator:
                    continue
                if job.get('kind')=='import' and job.get('auto_create') and not self.creator:
                    continue
                if job.get('kind')=='render' and job['status']=='queued':
                    blocker=self.render_blocker(Path(job['run']),base_render_id=job.get('base_render_id'))
                    if blocker:
                        job.update(status='failed',error=blocker,finished_at=now(),pid=None)
                        save(path,job)
                        continue
                self.active.add(job['id'])
                self.worker(job).submit(self.work, path, job)
        # Recover an import saved just before its automatic creation was enqueued.
        if self.creator:
            for path in sorted((self.root/'jobs').glob('import-*.json')):
                job=read(path)
                if job.get('status')=='completed' and job.get('auto_create'):
                    self.creator.submit(self,job)

    def close(self):
        self.import_executor.shutdown(wait=True)
        self.executor.shutdown(wait=True)
        self.render_executor.shutdown(wait=True)
        self.queue_lock.close()

    def worker(self, job):
        if job['kind']=='import':
            return self.import_executor
        if job['kind']=='render':
            return self.render_executor
        return self.executor

    @staticmethod
    def queue_key(job):
        return (job.get('queued_at') or job.get('created_at',''),job.get('id',''))

    def render_blocker(self, run, result=None, base_render_id=None):
        result=read(run/'result.json') if result is None else result
        if not result.get('exported'):
            return '请先完成静态封面，再制作动态成片。'
        if base_render_id and result.get('render_id')!=base_render_id:
            return '封面已更新，请基于最新封面重新制作动态成片。'
        missing=result.get('incomplete_objects',[])
        if missing:
            labels={o['id']:o.get('label') or o['id'] for o in read(run/'scene.json').get('objects',[])}
            names='、'.join(labels.get(oid,oid) for oid in missing)
            return f'封面对象尚未完成：{names}。请先修复，或在编辑画布中删除这些对象并保存，再制作动态成片；直接重试不能解决此问题。'
        return None

    def location(self, task):
        run = self.store.tasks().get(task)
        if run is None:
            raise KeyError('Task')
        return run

    def batch(self, identifier):
        if not re.fullmatch(r'batch-[a-f0-9]{32}', identifier):
            raise KeyError('Upload batch')
        path = self.root/'uploads'/identifier/'batch.json'
        value = read(path)
        if not value:
            raise KeyError('Upload batch')
        return path, value

    def create_batch(self):
        identifier = 'batch-'+uuid.uuid4().hex
        save(self.root/'uploads'/identifier/'batch.json', {'id': identifier, 'files': [], 'sealed': False})
        return {'id': identifier, 'status': 'ready'}

    def upload(self, identifier, headers, stream):
        length = int(headers.get('Content-Length', '0'))
        if not 0 < length <= 128*1024*1024:
            raise ValueError('每个素材需要在 1 字节至 128 MB 之间')
        name = unquote(headers.get('X-File-Name', ''))
        if not name or len(name)>180 or any(c in name for c in '/\\:\x00') or any(ord(c)<32 for c in name):
            raise ValueError('素材文件名无效')
        suffix = Path(name).suffix.lower()
        role = headers.get('X-Media-Role', 'material')
        if role not in {'reference', 'material'} or suffix not in {'.png','.jpg','.jpeg','.webp','.bmp','.mp4','.mov'}:
            raise ValueError('仅支持图片与 MP4/MOV 视频')
        if role == 'reference' and suffix in {'.mp4', '.mov'}:
            raise ValueError('参考图需要是静态图片')
        with self.guard:
            path, batch = self.batch(identifier)
            if batch['sealed']:
                raise Conflict('素材批次已提交，请新建一批素材')
            if len(batch['files']) >= 100 or sum(f['bytes'] for f in batch['files'])+length > 1024**3:
                raise ValueError('一批最多 100 个素材、总计 1 GB')
            if role == 'reference' and any(f['role']=='reference' for f in batch['files']):
                raise ValueError('一批只接受一张参考图')
            folder = path.parent/('reference' if role=='reference' else 'materials')
            folder.mkdir(exist_ok=True)
            destination = folder/(uuid.uuid4().hex+suffix)
            temporary = destination.with_suffix('.upload')
            digest = hashlib.sha256()
            try:
                with temporary.open('xb') as output:
                    remaining = length
                    while remaining:
                        block = stream.read(min(1024*1024, remaining))
                        if not block:
                            raise ValueError('素材上传中断，请重新上传')
                        output.write(block); digest.update(block); remaining -= len(block)
                temporary.replace(destination)
                item = {'id': destination.stem, 'name': name, 'role': role, 'bytes': length,
                        'file': str(destination), 'sha256': digest.hexdigest()}
                batch['files'].append(item); save(path, batch)
                return clean(item)|{'status': 'uploaded'}
            finally:
                temporary.unlink(missing_ok=True)

    def submit_import(self, identifier, payload):
        with self.guard:
            path, batch = self.batch(identifier)
            if batch.get('job_id'):
                return self.job(batch['job_id'])
            if set(payload)-{'width','instructions'}:
                raise ValueError('未知导入参数')
            width = payload.get('width', 1200)
            instructions = payload.get('instructions', '')
            if type(width) is not int or not 128<=width<=4096 or not isinstance(instructions,str) or len(instructions)>4000:
                raise ValueError('输出宽度或说明无效')
            if not any(f['role']=='reference' for f in batch['files']) or not any(f['role']=='material' for f in batch['files']):
                raise ValueError('请上传一张参考图和至少一个客户素材')
            if self.creator:
                self.creator.preflight()
            roots = [r for r in self.store.roots if not (r/'input.json').exists()]
            if not roots:
                raise ValueError('工作台需要一个任务父目录才能创建任务')
            job = {'id': 'import-'+uuid.uuid4().hex, 'kind': 'import', 'batch_id': identifier,
                   'run': str(roots[0]/('Live-'+uuid.uuid4().hex[:12])), 'payload': payload,
                   'width': width, 'instructions': instructions, 'status': 'queued', 'created_at': now(),
                   'auto_create': bool(self.creator)}
            batch.update(sealed=True, job_id=job['id']); save(path, batch)
            return self.enqueue(job)

    def submit(self, task, payload):
        if set(payload) != {'request_id', 'base_version_id'} or not re.fullmatch(r'motion-[a-zA-Z0-9_-]{8,72}', str(payload.get('request_id', ''))):
            raise ValueError('动态渲染请求格式无效')
        run = self.location(task)
        with self.guard:
            path = self.root/'jobs'/(payload['request_id']+'.json')
            old = read(path)
            if old:
                if old['payload'] != payload or old['task'] != task:
                    raise Conflict('请求编号已用于其他内容')
                if old['status']=='completed' or old['id'] in self.active:
                    return self.job(old['id'])
                blocker=self.render_blocker(run,base_render_id=old.get('base_render_id'))
                if blocker:raise Conflict(blocker)
                return self.enqueue(old)
            versions = self.store.document(task)['versions']
            result = read(run/'result.json')
            if not versions or versions[-1]['id'] != payload['base_version_id'] or versions[-1]['render_id']!=result.get('render_id'):
                raise Conflict('封面已更新，请返回最新版本后再制作动态成片')
            if not (run/'media/manifest.json').exists():
                raise ValueError('此任务没有导入 Live 视频')
            blocker=self.render_blocker(run,result)
            if blocker:raise Conflict(blocker)
            job = {'id': payload['request_id'], 'kind': 'render', 'task': task, 'run': str(run),
                   'payload': payload, 'base_render_id': result['render_id'], 'created_at': now()}
            return self.enqueue(job)

    def enqueue(self, job):
        path = self.root/'jobs'/(job['id']+'.json')
        job.update(status='queued', error=None,queued_at=now())
        for key in ('started_at','finished_at','elapsed_seconds','pid'):
            job.pop(key,None)
        save(path, job)
        if job['id'] not in self.active:
            self.active.add(job['id']); self.worker(job).submit(self.work, path, job)
        return {'id': job['id'], 'status': 'queued'}

    def work(self, path, job):
        if job['kind']=='create':
            return self.creator.work(self,path,job)
        start = time.monotonic()
        try:
            # If the server restarted while its child survived, wait for that child.
            pid = job.get('pid')
            while pid and process_alive(pid):
                time.sleep(.5)
            run = Path(job['run'])
            finished = run/'chat/motion/versions'/job['id']/'result.json'
            if job['kind']=='render' and finished.exists():
                job.update(status='completed'); return
            job.update(status='running', started_at=now(), pid=None); save(path, job)
            if job['kind']=='render':
                for lock in [run/'.write.lock',run/'chat/motion/.write.lock']:
                    owner = read(lock)
                    if owner.get('request_id')==job['id'] and not process_alive(owner.get('pid')):
                        lock.unlink(missing_ok=True)
                command = [sys.executable, '-B', str(REPO/'skill/scripts/motion_render.py'), '--run', str(run),
                           '--request-id', job['id'], '--base-render-id', job['base_render_id']]
            else:
                _, batch = self.batch(job['batch_id'])
                if run.exists():
                    has_video=any(Path(f['file']).suffix.lower() in {'.mp4','.mov'} for f in batch['files'])
                    if job.get('auto_create') and (run/'input.json').is_file() and (run/'prepared/catalog.json').is_file() and (not has_video or (run/'media/manifest.json').is_file()):
                        job['task_id']=next(key for key,value in self.store.tasks().items() if value==run)
                        job.update(status='completed')
                        self.creator.submit(self,job)
                        return
                    raise ValueError('上次素材准备已中断；请新建素材批次，旧记录保留供检查')
                reference = next(f['file'] for f in batch['files'] if f['role']=='reference')
                materials = self.root/'uploads'/job['batch_id']/'materials'
                command = [sys.executable, '-B', str(REPO/'skill/scripts/workflow.py'), 'prepare-live',
                           '--run', str(run), '--reference', reference, '--materials', str(materials),
                           '--width', str(job['width']), '--instructions', job['instructions']]
                command += ['--progress-file',str(path.with_suffix('.progress.json'))]
                if self.cutout_model:
                    command += ['--cutout-model', str(self.cutout_model)]
            env = dict(os.environ); env['COLLAGE_REQUEST_ID'] = job['id']
            with path.with_suffix('.log').open('wb') as output:
                child = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, env=env,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                job['pid'] = child.pid; save(path, job)
                try:
                    code = child.wait(timeout=7200)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(); raise ValueError('动态任务超过两小时，已停止；已完成的帧缓存保留')
            if code:
                raise ValueError(path.with_suffix('.log').read_text(encoding='utf8', errors='replace')[-1500:])
            job.update(status='completed')
            if job['kind']=='import':
                manifest_path=run/'media/manifest.json'
                manifest=read(manifest_path)
                if manifest:
                    names={f['file']:f['name'] for f in batch['files']}
                    for video in manifest['videos']:
                        video['name']=names.get(video['file'],video['name'])
                    save(manifest_path,manifest)
                job['task_id'] = next(key for key, value in self.store.tasks().items() if value==run)
                job['handoff'] = '素材准备完成。请在 Codex 中继续该任务的布局分析、静态封面制作与复核；封面完成后可制作动态成片。'
                if job.get('auto_create') and self.creator:
                    self.creator.submit(self,job)
        except Exception as exc:
            job.update(status='failed', error=clean(str(exc))[:700])
        finally:
            job.update(finished_at=now(), elapsed_seconds=round(time.monotonic()-start, 3), pid=None)
            save(path, job)
            with self.guard:
                self.active.discard(job['id'])

    def job(self, identifier, task=None):
        if not IDENTIFIER.fullmatch(identifier):
            raise KeyError('Job')
        row = read(self.root/'jobs'/(identifier+'.json'))
        if not row:
            raise KeyError('Job')
        if task is not None and row.get('task')!=task:
            raise KeyError('Job')
        value = {key: row[key] for key in ('id','kind','status','created_at','queued_at','started_at','finished_at','elapsed_seconds','error','task_id','handoff','auto_create','visual_verdict','attempts') if key in row}
        if row['status']=='queued':
            ahead=[j for p in (self.root/'jobs').glob('*.json') if (j:=read(p)).get('kind') in {'import','create','render'} and
                j.get('status') in {'queued','running'} and j.get('id')!=row['id'] and
                j['kind']==row['kind'] and
                (j.get('status')=='running' or self.queue_key(j)<self.queue_key(row))]
            value['queue_position']=len(ahead)+1
            value['waiting_seconds']=round(max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(row.get('queued_at') or row['created_at'])).total_seconds()),1)
            blocker=next((j for j in ahead if j['status']=='running'),None)
            if blocker:value['blocked_by']={'id':blocker['id'],'kind':blocker['kind'],'task_name':Path(blocker['run']).name}
        if row['kind']=='create':
            value.update(model='gpt-5.6-sol',effort='medium')
            value.setdefault('queue_position',0)
        if row['kind']=='render':
            progress=read(Path(row['run'])/'chat/motion/versions'/identifier/'progress.json')
            if row['status']=='queued' or (row['status']=='running' and progress.get('started_at','')<row.get('started_at','')):
                progress={}
            value['progress'] = clean(progress)
        elif row['kind']=='import':
            value['progress']=read((self.root/'jobs'/(identifier+'.json')).with_suffix('.progress.json'))
            current=value['progress'].get('current')
            if current:
                _,batch=self.batch(row['batch_id'])
                value['progress']['current']=next((f['name'] for f in batch['files'] if Path(f['file']).name==current),current)
        if row['kind']=='import' and row.get('status')=='completed' and not row.get('auto_create'):
            # Intentional operator handoff, not an arbitrary filesystem argument accepted by an API.
            value['codex_handoff'] = f"继续任务 {row['run']}：已 prepare-live，按 collage-recreate-v5 完成原六步封面制作，再 motion-render。"
        if value.get('error'):value['error']=clean(value['error'])
        return value

    def document(self, task):
        run = self.location(task)
        current = read(run/'result.json')
        manifest = read(run/'media/manifest.json')
        asset_ids={video['asset_id'] for video in manifest.get('videos',[])}
        eligible=sum(obj.get('kind')=='photo' and obj.get('binding',{}).get('asset_id') in asset_ids
                     for obj in read(run/'scene.json').get('objects',[]))
        videos = []
        for video in manifest.get('videos', []):
            videos.append({k: video[k] for k in ('id','name','asset_id','duration_us','frame_count','average_rate','size')})
            videos[-1]['url'] = f'/api/tasks/{task}/media/{video["id"]}/content'
            # The catalog thumbnail is already inside the run and registered by Store.
            videos[-1]['poster'] = self.store.image(task, run, run/'prepared'/(video['asset_id']+'.jpg'))
        versions = []
        for path in sorted((run/'chat/motion/versions').glob('*/result.json')):
            result = read(path)
            value = {k: result[k] for k in ('id','base_render_id','status','duration_us','frame_count','matte_frames','matte_cache_hits','timing','at') if k in result}
            value.update(stale=result.get('base_render_id')!=current.get('render_id'),
                         url=f'/api/tasks/{task}/motion/versions/{result["id"]}/content')
            versions.append(value)
        jobs = [self.job(p.stem) for p in sorted((self.root/'jobs').glob('motion-*.json')) if read(p).get('task')==task]
        for path in (run/'chat/motion/versions').glob('*/progress.json'):
            if any(job['id']==path.parent.name for job in jobs):
                continue
            progress=read(path)
            owner=read(run/'chat/motion/.write.lock')
            if progress.get('status')=='running' and (not owner or not process_alive(owner.get('pid'))):
                progress.update(status='failed',error='制作进程已中断，可使用帧缓存重试')
            jobs.append({'id':path.parent.name,'kind':'render','status':progress.get('status','failed'),
                         'progress':clean(progress),'error':clean(progress.get('error')),'started_at':progress.get('started_at')})
        jobs.sort(key=lambda job:job.get('created_at') or job.get('started_at') or '')
        return {'enabled': True, 'videos': videos, 'versions': sorted(versions,key=lambda v:v['at']), 'jobs': jobs,
                'base_render_id': current.get('render_id'),'eligible_slots':eligible,
                'render_blocker': self.render_blocker(run,current),
                'target_duration_us': min(3_000_000,max((v['duration_us'] for v in videos),default=0))}

    def content(self, task, kind, identifier):
        run = self.location(task)
        if kind=='media':
            record = next((v for v in read(run/'media/manifest.json').get('videos',[]) if v['id']==identifier), None)
        else:
            if not re.fullmatch(r'motion-[a-zA-Z0-9_-]{8,72}', identifier):
                raise KeyError('Video')
            record = read(run/'chat/motion/versions'/identifier/'result.json').get('video')
        if not record:
            raise KeyError('Video')
        path = Path(record['file'])
        if not path.is_file():
            raise FileNotFoundError('Video')
        stat=path.stat();signature=(str(path),stat.st_size,stat.st_mtime_ns,record['sha256'])
        with self.guard:
            if signature not in self.verified_content:
                digest=hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
                if digest.hexdigest()!=record['sha256']:
                    raise ValueError('Video changed since registration')
                self.verified_content[signature]=True
        return path
