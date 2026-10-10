"""Native timing, fixed geometry, immutable covers and real HTTP upload/playback."""
from copy import deepcopy
import io
import json
from pathlib import Path
import threading
import time
import urllib.request
import urllib.error
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageChops
from test_workbench import run, build, document
from common import read, save, sha
from live_media import encode_frames, inspect_video, prepare_live, timeline, frame_index
from motion_render import execute, fixed_photo_layer, make_plan
from scene import compile_scene
from render import render
from workbench.server.http import create_server


def clip(path, points=(0, 41000, 101000, 145000), duration=203000, size=(128, 160)):
    images = (Image.new('RGB', size, color) for color in ['#da1234','#22bc43','#193bee','#c1ae77'])
    return encode_frames(path, size, list(points), duration, images)


@pytest.fixture
def live_run(run):
    materials = run.parent/'live-input'; materials.mkdir()
    clip(materials/'short.mp4')
    clip(materials/'long.mp4', (0, 72000, 135000, 210000), 341000)
    target = run.parent/'live'
    prepare_live(run/'prepared/reference.png', [materials], target, width=200)
    save(target/'analysis.json', read(run/'analysis.json'))
    video = read(target/'media/manifest.json')['videos'][1]
    save(target/'bindings.json', {'schema_version':'collage-bindings-v1', 'photos':[
        {'slot_id':'photo','asset_id':video['asset_id'],'reason':'Live test'}], 'texts':[]})
    build(target)
    return target


def test_editor_crop_is_used_by_live_frames(live_run):
    from manual_edit import execute as edit
    from test_editor import request
    edit(live_run,'save',request(live_run,'edit-live-crop',changes=[{'id':'photo','source_crop':[.2,.1,.8,.9],'window_crop':[.1,.1,.9,.7]}]))
    result=execute(live_run,'motion-editor-crop')
    root=live_run/'chat/motion/versions/motion-editor-crop'
    with Image.open(root/'composed-first.png') as frame,Image.open(live_run/'final.png') as cover:
        assert ImageChops.difference(frame,cover).getbbox() is None
    assert result['frame_count']==7 and result['cover_alignment']['mean_absolute_error']==0


def test_vfr_duration_and_odd_dimensions(tmp_path):
    meta=clip(tmp_path/'clip.mp4',size=(129,161))
    assert meta['duration_us']==203000 and meta['size']==[130,162]
    assert [r['at_us'] for r in meta['frames']]==[0,41000,101000,145000]
    assert meta['frames'][-1]['duration_us']==58000 and not meta['has_audio']


def test_union_preserves_source_transition_times_and_loops():
    a={'duration_us':200000,'frames':[{'at_us':0},{'at_us':41000},{'at_us':101000}]}
    b={'duration_us':300000,'frames':[{'at_us':0},{'at_us':72000},{'at_us':210000}]}
    points=timeline([a,b],350000)
    assert points==[0,41000,72000,101000,200000,210000,241000,300000,301000]
    assert frame_index(a,200000)==0 and frame_index(a,241000)==1


def test_real_motion_preserves_cover_and_unused_longest_sets_duration(live_run):
    before={p:sha(live_run/p) for p in ['input.json','analysis.json','bindings.json','scene.json','final.png','result.json']}
    result=execute(live_run,'motion-test-12345678')
    assert result['duration_us']==341000
    assert result['frame_count']==7 and result['unique_source_frames']==4
    assert result['matte_frames']==0 and not result['visual_verified']
    assert before=={p:sha(live_run/p) for p in before}
    assert execute(live_run,'motion-test-12345678')==result
    root=live_run/'chat/motion/versions/motion-test-12345678'
    with Image.open(root/'composed-first.png') as im, Image.open(live_run/'final.png') as cover:
        assert ImageChops.difference(im,cover).getbbox() is None
    assert sha(root/'frame-0000.png')==sha(live_run/'final.png')
    with pytest.raises(ValueError,match='another cover'):
        execute(live_run,'motion-test-12345678','stale-cover')


def test_cap_three_seconds_even_if_unselected_source_longer(live_run):
    manifest=read(live_run/'media/manifest.json')
    manifest['videos'][0]['duration_us']=3_100_000
    save(live_run/'media/manifest.json',manifest)
    plan=make_plan(live_run,read(live_run/'scene.json'),read(live_run/'result.json'))
    assert plan['duration_us']==3_000_000


def test_source_change_and_stale_cover_are_rejected(live_run):
    with pytest.raises(ValueError,match='Cover changed'):
        execute(live_run,'motion-stale-12345678','wrong')
    video=read(live_run/'media/manifest.json')['videos'][0]
    Path(video['file']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='Source changed'):
        execute(live_run,'motion-changed-12345678')


def test_cover_edit_during_motion_keeps_both_versions(live_run,monkeypatch):
    import motion_render
    original=motion_render.encode_frames
    before=read(live_run/'result.json')['render_id']
    changed={}
    def encode_with_edit(*args,**kwargs):
        scene=read(live_run/'scene.json')
        scene['objects'][-1]['editor_transform']={'x':-30,'y':10}
        save(live_run/'scene.json',scene);render(live_run)
        changed['hash']=sha(live_run/'final.png')
        return original(*args,**kwargs)
    monkeypatch.setattr(motion_render,'encode_frames',encode_with_edit)
    result=execute(live_run,'motion-concurrent-12345678')
    assert result['base_render_id']==before and result['superseded_at_completion']
    assert read(live_run/'result.json')['render_id']!=before
    assert sha(live_run/'final.png')==changed['hash']


def test_pure_photos_keep_original_prepare(run):
    from prepare import prepare
    target=run.parent/'plain-live'; original=run.parent/'plain-static'
    material=run.parent.parent/'inputs'; reference=run/'prepared/reference.png'
    prepare_live(reference,[material],target,width=200)
    prepare(reference,[material],original,width=200)
    assert not (target/'media/manifest.json').exists()
    assert read(target/'prepared/catalog.json')['assets'][0]['sha256']==read(original/'prepared/catalog.json')['assets'][0]['sha256']


@pytest.mark.parametrize('mirror',[False,True])
def test_cutout_keeps_native_motion_without_recenter_and_no_cover_bbox_clipping(mirror):
    scene={'reference_size':[200,300],'canvas_size':[200,300]}
    obj={'id':'person','bbox':[60,100,100,180],'mode':'cutout','style':{'outline_width':0},'binding':{'mirror_x':mirror}}
    frames=[]
    for x in (20,40):
        im=Image.new('RGBA',(100,120));ImageDraw.Draw(im).rectangle((x,20,x+19,59),fill='red');frames.append(im)
    anchor=(20,20,40,60)
    a=fixed_photo_layer(obj,frames[0],anchor,scene).getchannel('A').getbbox()
    b=fixed_photo_layer(obj,frames[1],anchor,scene).getchannel('A').getbbox()
    assert b[0]-a[0]==(-40 if mirror else 40)
    assert a[2]-a[0]==b[2]-b[0] and a[3]-a[1]==b[3]-b[1]
    assert b[0]<obj['bbox'][0] if mirror else b[2]>obj['bbox'][2]


def test_matte_once_per_unique_frame_and_cache_reused(live_run,monkeypatch):
    import photos
    import motion_render
    model=live_run/'fake-model.onnx';model.write_bytes(b'model')
    inp=read(live_run/'input.json');inp['cutout_model']=str(model);save(live_run/'input.json',inp)
    analysis=read(live_run/'analysis.json');analysis['objects'][1]['mode']='cutout';save(live_run/'analysis.json',analysis)
    calls=[]
    def fake_cutout(image,source,model,cache):
        calls.append(source['sha256'])
        alpha=Image.new('L',image.size);ImageDraw.Draw(alpha).rectangle((20,20,100,140),fill=255)
        result=image.copy();result.putalpha(alpha);return result
    monkeypatch.setattr(photos,'cutout',fake_cutout);monkeypatch.setattr(motion_render,'cutout',fake_cutout)
    compile_scene(live_run);render(live_run);calls.clear()
    result=execute(live_run,'motion-matte-12345678')
    assert result['matte_frames']==4 and result['matte_cache_hits']==0
    assert len(calls)==4 # one matte per source frame; static cover tile is cached
    calls.clear()
    again=execute(live_run,'motion-matte-repeat-12345678')
    assert again['matte_cache_hits']==4 and len(calls)==0


def test_http_mixed_upload_prepare_ranges_and_stale_version(live_run,tmp_path):
    server=create_server([live_run.parent],port=0,enable_motion=True,media_root=tmp_path/'service')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def get(path,headers=None):
        return urllib.request.urlopen(urllib.request.Request(base+path,headers=headers or {}))
    token=json.load(get('/api/chat-config'))['token']
    def post(path,payload=None,body=None,headers=None):
        h={'Origin':base,'X-Chat-Token':token,'Content-Type':'application/json'};h.update(headers or {})
        return urllib.request.urlopen(urllib.request.Request(base+path,data=body if body is not None else json.dumps(payload or {}).encode(),headers=h))
    try:
        task=next(k for k,v in server.motion.store.tasks().items() if v==live_run)
        doc=json.load(get(f'/api/tasks/{task}/motion')); source=doc['videos'][0]['url']
        response=get(source,{'Range':'bytes=0-23'})
        assert response.status==206 and len(response.read())==24
        response=get(source,{'Range':'bytes=-17'});assert len(response.read())==17
        with pytest.raises(urllib.error.HTTPError) as error:
            get(source,{'Range':'bytes=999999999-'})
        assert error.value.code==416
        with pytest.raises(urllib.error.HTTPError) as error:
            post(f'/api/tasks/{task}/motion/render',{'request_id':'motion-http-stale1234','base_version_id':'stale'})
        assert error.value.code==409
        batch=json.load(post('/api/uploads'))['id']
        with pytest.raises(urllib.error.HTTPError) as error:
            post(f'/api/uploads/{batch}/files',body=b'bad',headers={'X-File-Name':'..%2Fevil.mp4'})
        assert error.value.code==400
        post(f'/api/uploads/{batch}/files',body=(live_run/'prepared/reference.png').read_bytes(),headers={'X-File-Name':'reference.png','X-Media-Role':'reference'})
        video=read(live_run/'media/manifest.json')['videos'][1]
        post(f'/api/uploads/{batch}/files',body=Path(video['file']).read_bytes(),headers={'X-File-Name':'customer.mp4'})
        job=json.load(post(f'/api/uploads/{batch}/prepare',{'width':200}))
        for _ in range(100):
            imported=json.load(get('/api/uploads/jobs/'+job['id']))
            if imported['status'] in {'completed','failed'}:break
            time.sleep(.1)
        assert imported['status']=='completed',imported
        assert 'codex_handoff' in imported
        assert len(json.load(get('/api/tasks/'+imported['task_id']+'/motion'))['videos'])==1
        version=server.motion.store.document(task)['versions'][-1]
        request={'request_id':'motion-http-good1234','base_version_id':version['id']}
        post(f'/api/tasks/{task}/motion/render',request)
        for _ in range(100):
            outcome=json.load(get(f'/api/tasks/{task}/motion/jobs/'+request['request_id']))
            if outcome['status'] in {'completed','failed'}:break
            time.sleep(.1)
        assert outcome['status']=='completed',outcome
        assert json.load(post(f'/api/tasks/{task}/motion/render',request))['id']==request['request_id']
        doc=json.load(get(f'/api/tasks/{task}/motion'))
        assert doc['versions'][-1]['base_render_id']==version['render_id']
        response=get(doc['versions'][-1]['url'],{'Range':'bytes=0-63'});assert len(response.read())==64
        # A second process cannot run a duplicate worker against the same persisted queue.
        from workbench.server.motion import Motion
        with pytest.raises(OSError):
            Motion(server.motion.store,tmp_path/'service')
    finally:
        server.shutdown();server.server_close();thread.join()


def test_queued_job_resumes_after_restart(live_run,tmp_path):
    from workbench.server.store import Store
    from workbench.server.motion import Motion
    store=Store([live_run]);task=next(iter(store.tasks()))
    root=tmp_path/'queue';identifier='motion-resume-12345678'
    result=read(live_run/'result.json')
    save(root/'jobs'/(identifier+'.json'),{'id':identifier,'kind':'render','task':task,'run':str(live_run),
         'payload':{},'base_render_id':result['render_id'],'created_at':'2026-10-09T00:00:00+00:00','status':'queued'})
    service=Motion(store,root)
    try:
        for _ in range(100):
            job=service.job(identifier)
            if job['status'] in {'completed','failed'}:break
            time.sleep(.1)
        assert job['status']=='completed',job
        assert service.document(task)['versions'][0]['base_render_id']==result['render_id']
    finally:service.close()


def test_import_has_own_serial_worker_and_truthful_queue(tmp_path,monkeypatch):
    from workbench.server.motion import Motion,save as save_job
    from workbench.server.store import Store
    heavy=threading.Event();importing=threading.Event();second=threading.Event();release=threading.Event()
    def work(self,path,job):
        job['status']='running';save_job(path,job)
        if job['kind']=='render':heavy.set();release.wait(5)
        elif job['id']=='import-first-1234':importing.set();release.wait(5)
        else:second.set()
        job['status']='completed';save_job(path,job)
    monkeypatch.setattr(Motion,'work',work)
    service=Motion(Store([tmp_path/'tasks']),tmp_path/'service')
    def job(identifier,kind,at):return {'id':identifier,'kind':kind,'run':str(tmp_path/identifier),'created_at':at}
    try:
        service.enqueue(job('motion-busy-1234','render','2026-10-09T01:00:00+00:00'))
        assert heavy.wait(2)
        service.enqueue(job('import-first-1234','import','2026-10-09T01:00:01+00:00'))
        assert importing.wait(2),'Import must start while rendering is busy'
        service.enqueue(job('import-next-1234','import','2026-10-09T01:00:02+00:00'))
        row=service.job('import-next-1234')
        assert row['status']=='queued' and row['queue_position']==2
        assert row['blocked_by']['id']=='import-first-1234'
        assert not second.is_set()
    finally:release.set();service.close()
    assert second.is_set()


def test_import_progress_reports_contact_sheets_and_completion(run,tmp_path):
    target=tmp_path/'progress-task';progress=tmp_path/'progress.json'
    result=prepare_live(run/'prepared/reference.png',[run.parent.parent/'inputs'],target,progress_file=progress)
    value=read(progress)
    assert value['stage']=='complete' and value['assets']==result['asset_count']==1
    assert value['elapsed_seconds']>=0


@pytest.mark.parametrize('recover',[False,True])
def test_creation_and_render_have_independent_serial_queues(tmp_path,monkeypatch,recover):
    from workbench.server.motion import Motion,save as save_job
    from workbench.server.store import Store
    release=threading.Event()
    started={kind:threading.Event() for kind in ['create','render','import']}
    finished=[]
    def work(self,path,job):
        job['status']='running';save_job(path,job)
        if job['id'].endswith('first-1234'):
            started[job['kind']].set()
            assert release.wait(10)
        finished.append(job['id'])
        job['status']='completed';save_job(path,job)
        with self.guard:self.active.discard(job['id'])
    monkeypatch.setattr(Motion,'work',work)
    root=tmp_path/'service'
    jobs=[]
    for index,(kind,prefix) in enumerate([('create','create'),('render','motion'),('import','import')]):
        for offset,label in enumerate(['first','next']):
            jobs.append({'id':f'{prefix}-{label}-1234','kind':kind,'status':'queued',
                         'run':str(tmp_path/f'{prefix}-{label}'),
                         'created_at':f'2026-10-09T01:00:0{index*2+offset}+00:00'})
    if recover:
        for job in jobs:
            if job['kind']=='render':save_job(Path(job['run'])/'result.json',{'exported':True})
            save_job(root/'jobs'/(job['id']+'.json'),job)
    service=Motion(Store([tmp_path/'tasks']),root,creator=object())
    try:
        if not recover:
            for job in jobs:service.enqueue(job)
        for kind,event in started.items():
            assert event.wait(2),f'{kind} must start while other queues are busy'
        assert not finished,'Each lane must keep its second job queued'
        for kind,prefix in [('create','create'),('render','motion'),('import','import')]:
            row=service.job(f'{prefix}-next-1234')
            assert row['status']=='queued' and row['queue_position']==2
            assert row['blocked_by']['id']==f'{prefix}-first-1234'
            assert row['blocked_by']['kind']==kind
    finally:
        release.set();service.close()
    assert len(finished)==len(set(finished))==6
    for prefix in ['create','motion','import']:
        assert finished.index(f'{prefix}-first-1234')<finished.index(f'{prefix}-next-1234')


def test_incomplete_cover_rejected_before_enqueue_and_after_restart(live_run,tmp_path):
    from workbench.server.motion import Motion,save as save_job
    from workbench.server.store import Store
    from workbench.server.chat import Conflict
    store=Store([live_run]);task=next(iter(store.tasks()))
    version=store.document(task)['versions'][-1]['id']
    scene=read(live_run/'scene.json');missing=scene['objects'][0]
    result=read(live_run/'result.json');result['incomplete_objects']=[missing['id']]
    save(live_run/'result.json',result)
    root=tmp_path/'service';identifier='motion-incomplete-1234'
    job={'id':identifier,'kind':'render','task':task,'run':str(live_run),'status':'queued',
         'created_at':'2026-10-09T01:00:00+00:00','base_render_id':result['render_id'],
         'payload':{'request_id':identifier,'base_version_id':version}}
    save_job(root/'jobs'/(identifier+'.json'),job)
    service=Motion(store,root)
    try:
        recovered=service.job(identifier)
        assert recovered['status']=='failed' and identifier not in service.active
        blocker=service.document(task)['render_blocker']
        assert (missing.get('label') or missing['id']) in blocker
        assert recovered['error']==blocker
        for request_id in [identifier,'motion-incomplete-new1234']:
            with pytest.raises(Conflict,match='封面对象尚未完成'):
                service.submit(task,{'request_id':request_id,'base_version_id':version})
        assert len(list((root/'jobs').glob('*.json')))==1
        assert not (live_run/'chat/motion/versions'/identifier/'progress.json').exists()
    finally:service.close()


def test_retry_goes_to_back_of_queue_without_previous_attempt_progress(tmp_path,monkeypatch):
    from workbench.server.motion import Motion,save as save_job
    from workbench.server.store import Store
    started=threading.Event();release=threading.Event();order=[]
    def work(self,path,job):
        job.update(status='running',started_at='2026-10-09T01:01:00+00:00');save_job(path,job)
        if job['id']=='motion-first-1234':started.set();release.wait(10)
        order.append(job['id']);job['status']='completed';save_job(path,job)
        with self.guard:self.active.discard(job['id'])
    monkeypatch.setattr(Motion,'work',work)
    service=Motion(Store([tmp_path/'tasks']),tmp_path/'service')
    def job(name,created):return {'id':f'motion-{name}-1234','kind':'render','run':str(tmp_path/name),'created_at':created}
    try:
        service.enqueue(job('first','2026-10-09T01:00:01+00:00'));assert started.wait(2)
        service.enqueue(job('second','2026-10-09T01:00:02+00:00'))
        retried=job('retry','2026-10-09T01:00:00+00:00')
        retried.update(status='failed',started_at='2026-10-09T01:00:00+00:00',finished_at='2026-10-09T01:00:01+00:00',elapsed_seconds=1)
        save_job(Path(retried['run'])/'chat/motion/versions'/retried['id']/'progress.json',
                 {'status':'failed','started_at':retried['started_at'],'stages':{'prepare':{'status':'failed'}}})
        service.enqueue(retried)
        row=service.job(retried['id'])
        assert row['queue_position']==3 and row['progress']=={}
        assert row['waiting_seconds']<2 and 'started_at' not in row and 'elapsed_seconds' not in row
        # The worker has begun a new attempt, but has not written its progress yet.
        path=service.root/'jobs'/(retried['id']+'.json')
        save_job(path,retried|{'status':'running','started_at':'2026-10-09T01:01:00+00:00'})
        assert service.job(retried['id'])['progress']=={}
    finally:release.set();service.close()
    assert order==['motion-first-1234','motion-second-1234','motion-retry-1234']
