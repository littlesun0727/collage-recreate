"""First-image workflow on the existing durable serial media queue; no agent runtime."""
import os
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import time
from .chat import Conflict, now, process_alive
from .store import events

REPO = Path(__file__).resolve().parents[2]
ACTIVE = {'queued', 'running'}


class Creation:
    def configuration(self, policy=None):
        from .models import ModelSettings
        from collage_v6.paths import RUNTIME_ROOT
        return (getattr(self, 'settings', None) or ModelSettings(RUNTIME_ROOT/'service')).config(policy)

    def preflight(self, config=None):
        from collage_v6.config import Config
        if config is not None:config.check_keys()
        if time.monotonic()-getattr(self,'checked_at',-1000)<60:return
        config=config or Config.load()
        sys.path.insert(0, str(REPO/'engine/scripts'))
        try:
            from reveal import api_key
            api_key(config.reveal_key_file)
            import PIL, numpy, onnxruntime, cv2, jsonschema  # noqa: F401
        except (ImportError, ValueError) as exc:
            raise ValueError('制作环境或 360_API_KEY 配置缺失，请使用完整工作流 Python 环境') from exc
        finally:
            sys.path.pop(0)
        self.checked_at=time.monotonic()

    def submit(self, service, imported):
        from .motion import read, save
        identifier = 'create-'+imported['id'].removeprefix('import-')
        with service.guard:
            old = read(service.root/'jobs'/(identifier+'.json'))
            if old:
                return old
            run = Path(imported['run'])
            policy = imported.get('model_policy') or read(run/'workflow-models.json') or self.configuration().model_policy()
            save(run/'workflow-models.json', policy)
            job = {'id':identifier, 'kind':'create', 'task':imported['task_id'],
                   'task_id':imported['task_id'], 'run':imported['run'], 'created_at':now(), 'attempts':0}
            job['model_policy'] = policy
            return service.enqueue(job)

    def retry(self, service, task):
        from .motion import read
        with service.guard:
            job = self.find(service, task)
            if not job:
                raise KeyError('Creation')
            if job['status'] in ACTIVE:
                return job
            if job['status']=='completed':
                raise Conflict('首版已交付，请使用继续修改或编辑画布')
            control = service.root/'creation'/job['id']
            for pid in [job.get('pid'), read(control/'cli-process.json').get('pid')]:
                if pid and process_alive(pid):
                    raise Conflict('上次制作进程仍在退出，请稍后继续')
            run = Path(job['run'])
            if read(run/'result.json').get('render_id') != job.get('base_render_id'):
                raise Conflict('成图已修改，请保留当前版本并使用继续修改')
            lock = read(run/'.write.lock')
            if lock:
                if lock.get('request_id') != job['id'] or process_alive(lock.get('pid')):
                    raise Conflict('任务仍有其他写入操作，请稍后继续')
                (run/'.write.lock').unlink()
            policy = (job.get('model_policy') or read(run/'workflow-models.json')
                      or read(control/'state.json').get('model_policy')
                      or read(run/'workflow-timing.json').get('model_policy'))
            self.preflight(self.configuration(policy))
            job.pop('finished_at',None)
            return service.enqueue(job)

    def find(self, service, task):
        from .motion import read
        return next((j for p in (service.root/'jobs').glob('create-*.json')
                     if (j:=read(p)).get('task')==task), None)

    def public(self, service, task):
        job = self.find(service,task)
        return {'enabled':True,'job':service.job(job['id'],task=task) if job else None}

    def work(self, service, path, job):
        from .motion import read, save
        control = service.root/'creation'/job['id']
        control.mkdir(parents=True,exist_ok=True)
        run = Path(job['run'])
        start = time.monotonic()
        try:
            recovering = job['status']=='running'
            if recovering:
                # Never start a second worker while the prior workflow process is alive.
                for pid in [job.get('pid'),read(control/'cli-process.json').get('pid')]:
                    while pid and process_alive(pid):
                        time.sleep(.5)
            else:
                policy = (job.get('model_policy') or read(run/'workflow-models.json')
                          or read(control/'state.json').get('model_policy') or self.configuration().model_policy())
                config = self.configuration(policy)
                self.preflight(config)
                job['model_policy'] = config.model_policy()
                save(run/'workflow-models.json', job['model_policy'])
                job.update(status='running',started_at=now(),attempts=job.get('attempts',0)+1,error=None)
                save(path,job)
                request = {'run':str(run),'control':str(control),'engine':str(REPO/'engine'),
                           'python':sys.executable,'attempt':job['attempts']}
                save(control/'input.json',request)
                env=dict(os.environ);env['COLLAGE_REQUEST_ID']=job['id'];env['PYTHONDONTWRITEBYTECODE']='1'
                with (control/'agent.log').open('ab') as output:
                    child=subprocess.Popen([sys.executable,'-B','-m','collage_v6','run','--run',str(run),
                                             '--control',str(control),'--attempt',str(job['attempts'])],
                        cwd=REPO,stdout=output,stderr=subprocess.STDOUT,env=env,
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    job['pid']=child.pid;save(path,job)
                    try:
                        child.wait(timeout=46*60)
                    except subprocess.TimeoutExpired:
                        if os.name=='nt':
                            subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,
                                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        else:child.kill()
                        child.wait(timeout=30)
                        raise ValueError('制作超时，已保留产物；可继续未完成的步骤')
            state=read(control/'state.json');result=read(run/'result.json')
            review=result.get('visual_review') or {}
            delivery=result.get('workflow_delivery') or {}
            unchecked_delivery=(delivery.get('mode')=='best_effort'
                and delivery.get('render_id')==result.get('render_id')
                and ((delivery.get('review_status')=='incomplete' and delivery.get('category') in {'with_issues','simple_draft'})
                     or (delivery.get('review_status')=='skipped' and delivery.get('category') in {'unreviewed','with_issues','simple_draft'}))
                and review.get('verdict')=='needs_changes'
                and review.get('checked_entire_composition') is False)
            delivered=any(e.get('event') in {'agent_stage','workflow_stage'} and e.get('stage')==6 and e.get('status')=='complete'
                          and e.get('render_id')==result.get('render_id')
                          for e in events(run)[0])
            valid=(state.get('status')=='finished' and state.get('attempt')==job.get('attempts')
                   and result.get('exported') and (run/'final.png').is_file()
                   and (run/'previews/comparison.png').is_file() and result.get('customer_photos_verified')
                   and review.get('render_id')==result.get('render_id')
                   and (review.get('checked_entire_composition') or unchecked_delivery)
                   and review.get('verdict') in {'pass','needs_changes'} and delivered)
            if valid:
                valid=(hashlib.sha256((run/'final.png').read_bytes()).hexdigest()==result.get('final_sha256')
                       and hashlib.sha256((run/'scene.json').read_bytes()).hexdigest()==result.get('scene_sha256'))
            job.update(status='completed' if valid else 'failed',visual_verdict=review.get('verdict'),
                       delivery_category=delivery.get('category'), review_status=delivery.get('review_status'),
                       error=None if valid else state.get('error') or '首版尚未完成交付或文件校验，可继续未完成步骤。')
        except Exception as exc:
            job.update(status='failed',error=str(exc)[:700])
        finally:
            job.update(finished_at=now(),elapsed_seconds=round(time.monotonic()-start,3),pid=None,
                       base_render_id=read(run/'result.json').get('render_id'))
            save(path,job)
            timing=read(run/'workflow-timing.json')
            timing.update(status=job['status'],finished_at=job['finished_at'])
            save(run/'workflow-timing.json',timing)
            with service.guard:service.active.discard(job['id'])
