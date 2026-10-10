"""Upload-to-production wiring and recovery without spending model/API calls."""
import json
from pathlib import Path
import threading
import time
import urllib.request
import urllib.error
import pytest
from test_workbench import run, build, review, checkpoint, save, read
from workbench.server.creation import Creation
from workbench.server.motion import Motion
from workbench.server.store import Store
from workbench.server.http import create_server
from workbench.server.chat import Conflict


def test_status_reader_retries_transient_windows_file_lock(tmp_path,monkeypatch):
    from workbench.server.store import read as status_read
    path=tmp_path/'job.json';save(path,{'status':'running'})
    original=Path.read_text;calls=[]
    def locked_once(self,*args,**kwargs):
        calls.append(self)
        if len(calls)==1:raise PermissionError('transient replace lock')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',locked_once)
    assert status_read(path)=={'status':'running'} and len(calls)==2


class OfflineCreation(Creation):
    def preflight(self):pass


def wait_job(service, identifier):
    for _ in range(200):
        job=service.job(identifier)
        if job['status'] not in {'queued','running'}:return job
        time.sleep(.05)
    raise AssertionError(job)


def prepared_job(run,root,status='running'):
    store=Store([run]);task=next(iter(store.tasks()))
    job={'id':'create-1234567890abcdef','kind':'create','task':task,'task_id':task,'run':str(run),
         'created_at':'2026-10-09T01:00:00+00:00','status':status,'attempts':1}
    save(root/'jobs'/(job['id']+'.json'),job)
    return store,job


@pytest.mark.parametrize('outcome',['pass','needs_changes','stale_review','tampered_image','missing_delivery','unfinished_turn','old_attempt'])
def test_recovery_verifies_current_artifacts_without_restarting_sdk(run,tmp_path,outcome,monkeypatch):
    build(run);review(run)
    if outcome=='needs_changes':
        result=read(run/'result.json');result['visual_review']['verdict']='needs_changes';save(run/'result.json',result)
    if outcome!='missing_delivery':checkpoint(run,6,'complete','测试交付')
    if outcome=='stale_review':
        result=read(run/'result.json');result['visual_review']['render_id']='old';save(run/'result.json',result)
    if outcome=='tampered_image':(run/'final.png').write_bytes(b'changed')
    root=tmp_path/'service';store,job=prepared_job(run,root)
    save(root/'creation'/job['id']/'state.json',{'status':'failed' if outcome=='unfinished_turn' else 'finished',
                                               'attempt':0 if outcome=='old_attempt' else 1})
    def forbidden(*args,**kwargs):raise AssertionError('A recovered run must never resubmit')
    monkeypatch.setattr('workbench.server.creation.subprocess.Popen',forbidden)
    service=Motion(store,root,creator=OfflineCreation())
    try:
        result=wait_job(service,job['id'])
        assert result['status']==('completed' if outcome in {'pass','needs_changes'} else 'failed'),result
        if outcome=='needs_changes':assert result['visual_verdict']=='needs_changes'
    finally:service.close()


def test_retry_preserves_task_thread_and_rejects_changed_version(run,tmp_path,monkeypatch):
    root=tmp_path/'service';store,job=prepared_job(run,root,'failed')
    build(run);review(run)
    job['base_render_id']=read(run/'result.json')['render_id'];save(root/'jobs'/(job['id']+'.json'),job)
    control=root/'creation'/job['id'];save(control/'state.json',{'thread_id':'existing-thread','status':'failed'})
    service=Motion(store,root,creator=OfflineCreation())
    called=[]
    def record(work,path,current):called.append(current.copy())
    monkeypatch.setattr(service.executor,'submit',record)
    try:
        result=service.creator.retry(service,job['task']);assert result['id']==job['id']
        assert len(called)==1 and called[0]['run']==str(run)
        service.creator.retry(service,job['task']);assert len(called)==1
        assert read(control/'state.json')['thread_id']=='existing-thread'
        job.update(status='failed',base_render_id='old');save(root/'jobs'/(job['id']+'.json'),job)
        with pytest.raises(Conflict):service.creator.retry(service,job['task'])
    finally:service.close()


def test_upload_automatically_enqueues_once_and_blocks_edits(run,tmp_path):
    class PausedCreation(OfflineCreation):
        def work(self,service,path,job):
            # Leave queued to test the full HTTP contract without starting AI.
            with service.guard:service.active.discard(job['id'])
    root=tmp_path/'service'
    server=create_server([run.parent],port=0,enable_create=True,enable_editor=True,
                         media_root=root,creator=PausedCreation())
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def get(route):
        with urllib.request.urlopen(base+route) as response:return json.load(response)
    config=get('/api/chat-config');assert config['create_enabled'] and config['motion_enabled']
    def post(route,payload=None,body=None,headers=None):
        h={'Origin':base,'X-Chat-Token':config['token'],'Content-Type':'application/json'};h.update(headers or {})
        with urllib.request.urlopen(urllib.request.Request(base+route,headers=h,
             data=body if body is not None else json.dumps(payload or {}).encode())) as response:return json.load(response)
    try:
        batch=post('/api/uploads')['id']
        for role,file in [('reference',run/'prepared/reference.png'),('material',Path(read(run/'prepared/catalog.json')['assets'][0]['file']))]:
            post(f'/api/uploads/{batch}/files',body=file.read_bytes(),headers={'X-File-Name':role+'.png','X-Media-Role':role})
        request={'width':200,'instructions':'保留参考中的文案'}
        imported=post(f'/api/uploads/{batch}/prepare',request)
        result=wait_job(server.motion,imported['id']);assert result['status']=='completed',result
        assert result['auto_create'] and 'codex_handoff' not in result
        task=result['task_id'];creation=get(f'/api/tasks/{task}/creation')['job']
        assert creation['status']=='queued' and creation['model']=='gpt-5.6-sol'
        assert post(f'/api/uploads/{batch}/prepare',request)['id']==imported['id']
        assert len(list((root/'jobs').glob('create-*.json')))==1
        assert post(f'/api/tasks/{task}/creation/retry')['id']==creation['id']
        for suffix in ['chat','editor/save','motion/render']:
            with pytest.raises(urllib.error.HTTPError) as error:post(f'/api/tasks/{task}/{suffix}')
            assert error.value.code==409
        uploaded_run=server.motion.store.tasks()[task]
        assert read(uploaded_run/'input.json')['instructions']==request['instructions']
        assert not (uploaded_run/'analysis.json').exists()
    finally:server.shutdown();server.server_close();thread.join()


def test_restart_between_import_completion_and_enqueue_recovers_once(run,tmp_path):
    class PausedCreation(OfflineCreation):
        def work(self,service,path,job):
            with service.guard:service.active.discard(job['id'])
    root=tmp_path/'service';store=Store([run]);task=next(iter(store.tasks()))
    imported={'id':'import-abcdef1234567890','kind':'import','run':str(run),'task_id':task,
              'auto_create':True,'status':'completed'}
    save(root/'jobs'/(imported['id']+'.json'),imported)
    for _ in range(2):
        service=Motion(store,root,creator=PausedCreation())
        try:
            assert len(list((root/'jobs').glob('create-*.json')))==1
            assert service.creator.public(service,task)['job']['status']=='queued'
        finally:service.close()


def test_concurrent_import_callbacks_start_only_one_agent(run,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    class CountingCreation(OfflineCreation):
        calls=0
        def work(self,service,path,job):
            with service.guard:
                self.calls+=1;service.active.discard(job['id'])
    root=tmp_path/'service';store=Store([run]);task=next(iter(store.tasks()))
    creator=CountingCreation();service=Motion(store,root,creator=creator)
    imported={'id':'import-abcdef1234567890','run':str(run),'task_id':task}
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:creator.submit(service,imported),range(16)))
        assert len({r['id'] for r in results})==1
    finally:service.close()
    assert creator.calls==1
