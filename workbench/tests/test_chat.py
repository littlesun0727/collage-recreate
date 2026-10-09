import json
import threading
import urllib.request
import urllib.error
from pathlib import Path
import pytest
from test_workbench import run,build,document,review
from common import read,save,sha,timed,locked
from scene import load_scene
from revision import prepare_revision,commit_revision
from workbench.server.chat import ChatQueue,Conflict
from workbench.server.http import create_server


def plan(run,changes=None):
    return {'schema_version':'collage-review-v1','render_id':read(run/'result.json')['render_id'],
       'verdict':'needs_changes','summary':'移动装饰','items':[{'id':'star','action':'adjust','reason':'客户指定','changes':changes or {'bbox':[140,20,180,60]}}]}

def checked(candidate):
    return {'schema_version':'collage-review-v1','render_id':read(candidate/'result.json')['render_id'],
            'verdict':'pass','checked_entire_composition':True,'summary':'复核完成','items':[]}

def test_staging_commit_isolated_idempotent_and_preserves_history(run):
    build(run);review(run);before=sha(run/'final.png');first=next((run/'observability/versions').glob('*/final.png'))
    meta=prepare_revision(run,'test-request',plan(run))
    assert sha(run/'final.png')==before and len(list((run/'observability/versions').glob('*/manifest.json')))==1
    candidate=Path(meta['candidate']);assert sha(candidate/'final.png')!=before
    with timed(run,'revision-commit'):out=commit_revision(run,'test-request',checked(candidate))
    assert sha(first)==before and sha(run/'final.png')!=before
    assert read(run/'result.json')['visual_review']['render_id']==out['render_id']
    assert commit_revision(run,'test-request',checked(candidate))==out
    store,doc=document(run);assert len(doc['versions'])==2 and doc['versions'][-1]['review']['verdict']=='pass'
    load_scene(run)
    for resource in read(run/'result.json')['resources']:
        assert '/candidate/' not in resource['file'].replace('\\','/')
        assert sha(resource['file'])==resource['sha256']

def test_stale_candidate_and_invalid_change_cannot_overwrite(run):
    build(run);before=sha(run/'final.png')
    with pytest.raises(ValueError):prepare_revision(run,'bad-request',plan(run,{'bbox':[190,20,220,60]}))
    assert sha(run/'final.png')==before
    one=prepare_revision(run,'first-request',plan(run));two=prepare_revision(run,'second-request',plan(run))
    commit_revision(run,'first-request',checked(Path(one['candidate'])))
    with pytest.raises(ValueError,match='Base version changed'):commit_revision(run,'second-request',checked(Path(two['candidate'])))

def test_commit_io_failure_rolls_back_current_files(run,monkeypatch):
    import revision
    build(run);review(run)
    before={n:sha(run/n) for n in ['scene.json','result.json','final.png','review.json']}
    candidate=Path(prepare_revision(run,'rollback-test',plan(run))['candidate'])
    original=revision.shutil.copy2;failed=[]
    def flaky(src,dst,*args,**kwargs):
        if Path(dst)==run/'final.png' and not failed:
            failed.append(True);raise OSError('simulated disk error')
        return original(src,dst,*args,**kwargs)
    monkeypatch.setattr(revision.shutil,'copy2',flaky)
    with pytest.raises(OSError,match='disk error'):commit_revision(run,'rollback-test',checked(candidate))
    assert {n:sha(run/n) for n in before}==before
    commit_revision(run,'rollback-test',checked(candidate))
    assert sha(run/'final.png')!=before['final.png']

def test_rejected_asset_and_embedded_text_leave_scene_unchanged(run):
    from scene import apply_review
    build(run);before=sha(run/'scene.json');payload=plan(run)
    payload['items']=[{'id':'photo','action':'adjust','reason':'换图','changes':{'asset_id':'nonexistent'}}]
    save(run/'bad.json',payload)
    with pytest.raises(ValueError,match='Unknown customer asset'):apply_review(run,run/'bad.json')
    assert sha(run/'scene.json')==before
    payload['items']=[{'id':'star','action':'adjust','reason':'改嵌入字','changes':{'text':'wrong'}}]
    save(run/'bad.json',payload)
    with pytest.raises(ValueError,match='independent local text'):apply_review(run,run/'bad.json')
    assert sha(run/'scene.json')==before

def test_swap_customer_photo_and_independent_text(run):
    from PIL import Image
    from scene import compile_scene
    a=read(run/'analysis.json');a['objects'].append({'id':'title','kind':'text','label':'标题','description':'标题','bbox':[10,2,150,35],'text':'OLD','text_status':'known','style':{'font_size':14}})
    a['layer_order'].append('title');save(run/'analysis.json',a)
    image=run/'prepared/new-customer.png';Image.new('RGB',(200,300),'#ffbb33').save(image)
    catalog=read(run/'prepared/catalog.json');new=dict(catalog['assets'][0]);new.update(id='new_photo',file=str(image),sha256=sha(image));catalog['assets'].append(new);save(run/'prepared/catalog.json',catalog)
    build(run);payload=plan(run);payload['items']=[{'id':'photo','action':'adjust','reason':'换图','changes':{'asset_id':'new_photo','source_crop':[.1,.1,.9,.9]}},{'id':'title','action':'adjust','reason':'改字','changes':{'text':'NEW'}}]
    meta=prepare_revision(run,'replace-request',payload);candidate=Path(meta['candidate'])
    commit_revision(run,'replace-request',checked(candidate));objects={o['id']:o for o in load_scene(run)['objects']}
    assert objects['photo']['source']['sha256']==sha(image) and objects['title']['text']=='NEW'
    assert read(run/'result.json')['customer_photos_verified']

def payload(doc,identifier='queue-test-1'):
    return {'request_id':identifier,'message':'装饰向左移动10像素','base_version_id':doc['versions'][-1]['id'],'selected_ids':['star']}

def agent(mode,root,context,images):
    if mode=='plan':return {'decision':'edit','summary':'左移装饰','question':'','changes':[{'id':'star','reason':'客户要求','changes_json':'{"bbox":[140,20,180,60]}'}],'layer_order':[]}
    return {'verdict':'pass','summary':'已左移装饰，无新遮挡。','issues':[]}

def test_queue_deduplicates_and_detects_stale_queued_base(run,tmp_path):
    build(run);review(run);store,doc=document(run);q=ChatQueue(store,tmp_path/'queue.sqlite',agent=agent,start=False)
    try:
        one=q.submit(doc['id'],payload(doc));assert q.submit(doc['id'],payload(doc))['id']==one['id']
        two=q.submit(doc['id'],payload(doc,'queue-test-2'))
        assert len(q.rows())==2
        q.process(one);assert q.rows()[0]['status']=='completed',q.rows()[0]
        q.process(two);assert q.rows()[1]['status']=='conflict'
        assert len(document(run)[1]['versions'])==2
    finally:q.close()

def test_review_failure_keeps_image_and_retry_reuses_candidate(run,tmp_path):
    build(run);review(run);store,doc=document(run);calls=[]
    def flaky(mode,*args):
        calls.append(mode)
        if mode=='review' and calls.count('review')==1:raise RuntimeError('Temporary agent failure')
        return agent(mode,*args)
    q=ChatQueue(store,tmp_path/'queue.sqlite',agent=flaky,start=False)
    try:
        before=sha(run/'final.png');job=q.submit(doc['id'],payload(doc));q.process(job)
        assert q.rows()[0]['status']=='failed' and sha(run/'final.png')==before
        q.process(q.retry(doc['id'],job['id']))
        assert q.rows()[0]['status']=='completed' and calls==['plan','review','review']
    finally:q.close()

def test_clarification_does_not_mutate_and_reply_keeps_context(run,tmp_path):
    build(run);review(run);store,doc=document(run);contexts=[]
    def clarify(mode,root,context,images):
        contexts.append(context)
        if len(contexts)==1:return {'decision':'clarify','summary':'','question':'想替换哪一张照片？','changes':[],'layer_order':[]}
        return agent(mode,root,context,images)
    q=ChatQueue(store,tmp_path/'queue.sqlite',agent=clarify,start=False)
    try:
        before=sha(run/'final.png');job=q.submit(doc['id'],payload(doc));q.process(job)
        assert q.rows()[0]['status']=='clarify' and sha(run/'final.png')==before
        reply=payload(doc,'reply-request');reply['reply_to']=job['id'];reply['message']='先不换照片，只移动装饰'
        q.process(q.submit(doc['id'],reply))
        assert contexts[1]['reply_to']==job['id'] and contexts[1]['conversation'][0]['assistant']=='想替换哪一张照片？'
        assert q.rows()[-1]['status']=='completed'
    finally:q.close()

def test_queue_restart_and_post_origin_validation(run,tmp_path):
    build(run);store,doc=document(run);path=tmp_path/'queue.sqlite'
    q=ChatQueue(store,path,start=False);job=q.submit(doc['id'],payload(doc));job['status']='running';q.put(job);q.close()
    q=ChatQueue(store,path,start=False);assert q.rows()[0]['status']=='failed';q.close()
    server=create_server([run],0,chat_path=path,agent=agent);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        config=json.load(urllib.request.urlopen(base+'/api/chat-config'))
        req=urllib.request.Request(base+'/api/tasks/'+doc['id']+'/chat',data=json.dumps(payload(doc,'new-request')).encode(),headers={'Content-Type':'application/json','Origin':'https://evil.example','X-Chat-Token':config['token']})
        with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(req)
        assert error.value.code==403
    finally:server.shutdown();server.server_close();thread.join()
