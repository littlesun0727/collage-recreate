import json
import threading
import time
import urllib.request
import urllib.error
from copy import deepcopy
from pathlib import Path
import pytest
from PIL import Image,ImageChops
from test_workbench import run,build,review,document
from common import read,save,sha
from render import render
from manual_edit import execute
from editor_scene import apply_edits,capabilities,translate
from workbench.server.http import create_server


def request(run,identifier='edit-test-1234',changes=None):
    return {'request_id':identifier,'base_render_id':read(run/'result.json')['render_id'],
            'base_scene_sha256':sha(run/'scene.json'),'changes':changes or [{'id':'star','transform':{'x':-30,'y':10}}]}


def test_export_preserves_scene_and_pixels_and_rgba_reconstructs(run):
    build(run);review(run);before={n:sha(run/n) for n in ['scene.json','final.png','result.json']}
    data=execute(run,'export',request(run))
    assert before=={n:sha(run/n) for n in before}
    assert sha(data['final_file'])==before['final.png']
    canvas=Image.new('RGBA',data['canvas_size'],'white')
    for obj in data['objects']:
        with Image.open(obj['file']) as im:canvas=Image.alpha_composite(canvas,im.convert('RGBA'))
    with Image.open(run/'final.png') as im:assert ImageChops.difference(canvas.convert('RGB'),im).getbbox() is None


def test_manual_commit_isolated_idempotent_unreviewed_and_rerenderable(run):
    build(run);review(run);old=sha(run/'final.png');inp=sha(run/'analysis.json');req=request(run)
    preview=execute(run,'preview',dict(req,request_id='edit-preview-1'))
    assert sha(run/'final.png')==old and sha(preview['final_file'])!=old
    result=execute(run,'save',req)
    assert execute(run,'save',req)==result
    assert sha(run/'analysis.json')==inp and read(run/'result.json')['visual_review'] is None
    scene=read(run/'scene.json');obj=next(o for o in scene['objects'] if o['id']=='star')
    assert obj['bbox']==[150,20,190,60] and obj['editor_transform']=={'x':-30,'y':10}
    store,doc=document(run);assert len(doc['versions'])==2 and doc['versions'][-1]['review'] is None
    assert doc['versions'][-1]['label']=='手动编辑'
    first=run/'observability/versions'/doc['versions'][0]['id']/'final.png';assert sha(first)==old
    after=sha(run/'final.png');render(run,publish_result=False);assert sha(run/'final.png')==after


def test_manual_base_conflict_and_invalid_input_do_not_touch_output(run):
    build(run);req=request(run);before=sha(run/'final.png')
    for change in [{'id':'background','transform':{'x':1,'y':2}}, {'id':'star','text':'wrong'},
                   {'id':'star','transform':{'x':float('nan'),'y':0}}, {'id':'star','transform':{'x':999,'y':0}}]:
        with pytest.raises(ValueError):execute(run,'save',dict(req,changes=[change]))
        assert sha(run/'final.png')==before
    execute(run,'save',req)
    with pytest.raises(ValueError,match='基础版本'):execute(run,'save',dict(req,request_id='edit-conflict-1'))


def test_chat_revision_after_manual_edit_preserves_manual_placement(run):
    from revision import prepare_revision,commit_revision
    build(run);execute(run,'save',request(run))
    base=read(run/'result.json')['render_id'];plan={'schema_version':'collage-review-v1','render_id':base,'verdict':'needs_changes',
        'summary':'更换裁切','items':[{'id':'photo','action':'adjust','reason':'客户要求','changes':{'source_crop':[.1,.1,.9,.9]}}]}
    candidate=Path(prepare_revision(run,'chat-after-manual',plan)['candidate'])
    commit_revision(run,'chat-after-manual',{'schema_version':'collage-review-v1','render_id':read(candidate/'result.json')['render_id'],
        'verdict':'needs_changes','checked_entire_composition':True,'summary':'测试复核','items':[]})
    assert next(o for o in read(run/'scene.json')['objects'] if o['id']=='star')['editor_transform']=={'x':-30,'y':10}
    assert len(document(run)[1]['versions'])==3


def test_groups_and_embedded_text_keep_geometry_sources():
    scene={'reference_size':[300,300],'objects':[
        {'id':'frame','kind':'overlay','bbox':[10,10,200,200],'photo_id':'photo'},
        {'id':'photo','kind':'photo','bbox':[20,20,180,180],'parent_id':'frame','photo_window':{'file':'mask'}},
        {'id':'text','kind':'text','bbox':[30,30,100,60],'embedded_owner':'frame','text':'baked'}]}
    updated=apply_edits(scene,[{'id':'photo','transform':{'x':15,'y':20}}])
    assert updated['objects'][0]['editor_transform']==updated['objects'][1]['editor_transform']=={'x':15,'y':20}
    assert updated['objects'][1]['photo_window']==scene['objects'][1]['photo_window']
    assert not capabilities(scene)['text']['text']
    with pytest.raises(ValueError):apply_edits(scene,[{'id':'text','text':'replace'}])
    with pytest.raises(ValueError):apply_edits(scene,[{'id':'photo','transform':{'x':10,'y':0}},{'id':'frame','transform':{'x':20,'y':0}}])


def test_photo_window_and_pixels_move_together(run):
    from PIL import ImageDraw
    build(run);scene=read(run/'scene.json')
    mask=Image.new('L',(200,300));ImageDraw.Draw(mask).ellipse((30,70,160,230),fill=255)
    path=run/'window.png';mask.save(path)
    next(o for o in scene['objects'] if o['id']=='photo')['photo_window']={'file':str(path),'sha256':sha(path)}
    save(run/'scene.json',scene);render(run)
    layers=execute(run,'export',request(run,'editor-mask-export'))
    req=request(run,'edit-mask-save',changes=[{'id':'photo','transform':{'x':12,'y':-8}}])
    execute(run,'save',req)
    expected=Image.new('RGBA',(200,300),'white')
    for obj in layers['objects']:
        with Image.open(obj['file']) as raw:layer=raw.convert('RGBA')
        if obj['id']=='photo':layer=translate(layer,{'editor_transform':{'x':12,'y':-8}},1)
        expected=Image.alpha_composite(expected,layer)
    with Image.open(run/'final.png') as actual:assert ImageChops.difference(actual,expected.convert('RGB')).getbbox() is None
    assert sha(path)==scene['objects'][1]['photo_window']['sha256']


def test_independent_text_preview_and_save(run):
    a=read(run/'analysis.json');a['objects'].append({'id':'title','kind':'text','label':'标题','description':'标题','bbox':[10,2,150,35],'text':'OLD','text_status':'known','style':{'font_size':14}})
    a['layer_order'].append('title');save(run/'analysis.json',a);build(run)
    req=request(run,changes=[{'id':'title','text':'NEW TEXT'}]);before=sha(run/'final.png')
    data=execute(run,'preview',req);assert sha(data['final_file'])!=before and sha(run/'final.png')==before
    execute(run,'save',dict(req,request_id='edit-text-save'))
    assert next(o for o in read(run/'scene.json')['objects'] if o['id']=='title')['text']=='NEW TEXT'
    assert read(run/'result.json')['visual_review'] is None


def test_manual_commit_failure_rolls_back_and_retry_succeeds(run,monkeypatch):
    import revision
    build(run);review(run);before={n:sha(run/n) for n in ['scene.json','result.json','final.png','review.json']};req=request(run)
    original=revision.shutil.copy2;failed=[]
    def flaky(src,dst,*args,**kwargs):
        if Path(dst)==run/'final.png' and not failed:failed.append(True);raise OSError('disk failure')
        return original(src,dst,*args,**kwargs)
    monkeypatch.setattr(revision.shutil,'copy2',flaky)
    with pytest.raises(OSError):execute(run,'save',req)
    assert before=={n:sha(run/n) for n in before}
    execute(run,'save',req);assert sha(run/'final.png')!=before['final.png']


def test_editor_http_without_chat_origin_idempotency_and_versions(run):
    build(run);_,doc=document(run);task=doc['id'];server=create_server([run],port=0,enable_editor=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}'
    def get(path):
        with urllib.request.urlopen(url+path) as r:return json.load(r)
    def post(path,body,origin=url):
        req=urllib.request.Request(url+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Origin':origin,'X-Chat-Token':config['token']})
        with urllib.request.urlopen(req) as r:return json.load(r)
    try:
        config=get('/api/chat-config');assert config['editor_enabled'] and not config['enabled']
        data=get(f'/api/tasks/{task}/editor');assert data['base_version_id']==doc['versions'][-1]['id']
        assert all('file' not in o and o['image'] for o in data['objects'])
        payload={'request_id':'edit-http-save1','base_version_id':data['base_version_id'],'changes':[{'id':'star','transform':{'x':-20,'y':0}}]}
        path=f'/api/tasks/{task}/editor/save'
        with pytest.raises(urllib.error.HTTPError) as exc:post(path,payload,'https://evil.example')
        assert exc.value.code==403
        post(path,payload);post(path,payload)
        for _ in range(150):
            job=get(f'/api/tasks/{task}/editor/jobs/'+payload['request_id'])
            if job['status'] not in ['running','queued']:break
            time.sleep(.1)
        assert job['status']=='completed',job
        assert len(get(f'/api/tasks/{task}')['versions'])==2
        with pytest.raises(urllib.error.HTTPError) as exc:post(path,dict(payload,request_id='edit-http-stale1'))
        assert exc.value.code==409
    finally:server.shutdown();server.server_close();thread.join()
