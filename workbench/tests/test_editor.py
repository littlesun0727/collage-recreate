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


def test_export_preserves_scene_and_pixels_and_rgba_reconstructs(run,monkeypatch):
    build(run);review(run);before={n:sha(run/n) for n in ['scene.json','final.png','result.json']}
    import importlib
    def no_photo_processing(*args,**kwargs):raise AssertionError('Opening the canvas must reuse existing photos')
    monkeypatch.setattr(importlib.import_module('render'),'make_photo',no_photo_processing)
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


def test_linked_layers_can_transform_independently_then_move_together():
    scene={'reference_size':[300,300],'objects':[
        {'id':'frame','kind':'overlay','bbox':[10,10,200,200],'photo_id':'photo'},
        {'id':'photo','kind':'photo','bbox':[20,20,180,180],'parent_id':'frame'},
        {'id':'text','kind':'text','bbox':[30,30,100,60],'embedded_owner':'frame'}]}
    moved=apply_edits(scene,[{'id':'photo','scope':'object','transform':{'x':20,'y':5,'rotation':15,'scale':.8}},
                             {'id':'frame','scope':'object','transform':{'x':-5,'y':10}}])
    assert moved['objects'][0]['editor_transform']=={'x':-5,'y':10}
    assert moved['objects'][1]['editor_transform']=={'x':20,'y':5,'rotation':15,'scale':.8}
    assert 'editor_transform' not in moved['objects'][2]
    together=apply_edits(moved,[{'id':'frame','transform':{'x':5,'y':20}}])
    assert together['objects'][1]['editor_transform']=={'x':30,'y':15,'rotation':15,'scale':.8}
    with pytest.raises(ValueError):
        apply_edits(scene,[{'id':'text','scope':'object','transform':{'x':5,'y':0}}])
    for change in [{'id':'frame','scope':'invalid','transform':{'x':5,'y':0}}, {'id':'frame','scope':'object'}]:
        with pytest.raises(ValueError):apply_edits(scene,[change])


def test_independent_edit_survives_render_and_commit_without_moving_frame(run):
    a=read(run/'analysis.json');a['objects'][-1]['photo_id']='photo';save(run/'analysis.json',a)
    build(run)
    before=read(run/'scene.json');frame=next(o for o in before['objects'] if o['id']=='star')
    req=request(run,'edit-independent-save',changes=[{'id':'photo','scope':'object','transform':{'x':12,'y':-8}}])
    execute(run,'save',req)
    after=read(run/'scene.json')
    assert next(o for o in after['objects'] if o['id']=='star')==frame
    assert next(o for o in after['objects'] if o['id']=='photo')['editor_transform']=={'x':12,'y':-8}


def test_crop_preserves_frame_source_and_history_and_can_reset(run):
    catalog=read(run/'prepared/catalog.json');asset=catalog['assets'][0]
    source=Image.new('RGB',(200,300),'red');source.paste('blue',(100,0,200,300));source.save(asset['file'])
    asset['sha256']=sha(asset['file']);save(run/'prepared/catalog.json',catalog)
    a=read(run/'analysis.json');a['objects'][-1]['photo_id']='photo';save(run/'analysis.json',a)
    build(run);before=sha(run/'final.png');original=read(run/'scene.json')
    doc=execute(run,'export',request(run,'edit-crop-export'))
    photo=next(o for o in doc['objects'] if o['id']=='photo')
    assert photo['capabilities']['crop'] and Path(photo['crop_source_file']).is_file()
    crop=[.5,0,1,1]
    req=request(run,'edit-crop-save',changes=[{'id':'photo','source_crop':crop}])
    preview=execute(run,'preview',dict(req,request_id='edit-crop-preview'))
    assert sha(run/'final.png')==before
    assert Image.open(preview['final_file']).getpixel((110,150))==(0,0,255)
    execute(run,'save',req)
    scene=read(run/'scene.json');obj=next(o for o in scene['objects'] if o['id']=='photo')
    assert obj['binding']['source_crop']==crop and obj['bbox']==[20,40,180,260]
    assert scene['objects'][-1]==original['objects'][-1] and sha(asset['file'])==asset['sha256']
    execute(run,'save',request(run,'edit-crop-reset',changes=[{'id':'photo','source_crop':[0,0,1,1]}]))
    assert sha(run/'final.png')==before


@pytest.mark.parametrize('crop',[[0,0,0,1],[.9,0,.1,1],[-.1,0,1,1],[0,0,1,float('nan')],[False,0,1,1],[0,0,.001,1],[0,1,1]])
def test_invalid_crop_keeps_current_version(run,crop):
    build(run);before=sha(run/'scene.json')
    with pytest.raises(ValueError):execute(run,'save',request(run,changes=[{'id':'photo','source_crop':crop}]))
    assert sha(run/'scene.json')==before


def test_window_crop_trims_pixels_without_reframing_and_can_reset(run):
    build(run);before=Image.open(run/'final.png').copy();scene=read(run/'scene.json')
    req=request(run,'edit-window-crop',changes=[{'id':'photo','window_crop':[.1,.2,.9,.8]}])
    execute(run,'save',req);after=Image.open(run/'final.png')
    assert after.getpixel((30,150))==before.getpixel((0,0))
    assert after.getpixel((100,150))==before.getpixel((100,150))
    obj=next(o for o in read(run/'scene.json')['objects'] if o['id']=='photo')
    original=next(o for o in scene['objects'] if o['id']=='photo')
    assert obj['binding']==original['binding'] and obj['bbox']==original['bbox']
    exported=execute(run,'export',request(run,'edit-window-export'))
    photo=next(o for o in exported['objects'] if o['id']=='photo')
    assert Image.open(photo['file']).getpixel((30,150))[3]>0  # editable pixels retained for expansion
    execute(run,'save',request(run,'edit-window-reset',changes=[{'id':'photo','window_crop':[0,0,1,1]}]))
    assert ImageChops.difference(Image.open(run/'final.png'),before).getbbox() is None


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


def test_rotate_scale_masked_photo_preview_save_and_repeat(run):
    from PIL import ImageDraw
    build(run);scene=read(run/'scene.json')
    mask=Image.new('L',(200,300));ImageDraw.Draw(mask).ellipse((30,70,160,230),fill=255)
    path=run/'window.png';mask.save(path)
    scene['objects'][1]['photo_window']={'file':str(path),'sha256':sha(path)}
    save(run/'scene.json',scene);render(run)
    layers=execute(run,'export',request(run,'editor-affine-export'))
    before=sha(run/'final.png');v={'x':-5,'y':8,'rotation':30,'scale':.75}
    req=request(run,'edit-affine-preview',changes=[{'id':'photo','transform':v}])
    data=execute(run,'preview',req)
    assert sha(run/'final.png')==before
    expected=Image.new('RGBA',(200,300),'white')
    for obj in layers['objects']:
        with Image.open(obj['file']) as raw:layer=raw.convert('RGBA')
        if obj['id']=='photo':
            # Independent PIL reference: crop around known center, scale and rotate.
            crop=layer.crop((0,0,200,300)).resize((150,225),Image.Resampling.BICUBIC)
            rotated=crop.rotate(-30,Image.Resampling.BICUBIC,expand=True)
            layer=Image.new('RGBA',(200,300));layer.alpha_composite(rotated,(round(95-rotated.width/2),round(158-rotated.height/2)))
        expected=Image.alpha_composite(expected,layer)
    with Image.open(data['final_file']) as actual:
        import numpy as np
        # Different resampling order has small edge differences, but alignment must agree.
        error=np.abs(np.asarray(actual).astype(float)-np.asarray(expected.convert('RGB')).astype(float)).mean()
        assert error<2,error
    result=execute(run,'save',dict(req,request_id='edit-affine-save'))
    assert result['render_id'] and sha(run/'final.png')==sha(data['final_file'])
    after=sha(run/'final.png');render(run,publish_result=False);assert sha(run/'final.png')==after
    assert sha(path)==scene['objects'][1]['photo_window']['sha256']


def test_rotate_group_around_selected_center_and_delete_only_frame(run):
    a=read(run/'analysis.json');a['objects'][-1]['photo_id']='photo';save(run/'analysis.json',a)
    build(run);scene=read(run/'scene.json')
    v={'x':0,'y':0,'rotation':90,'scale':.5}
    edited=apply_edits(scene,[{'id':'photo','transform':v}])
    photo,star=edited['objects'][1:]
    assert photo['editor_transform']==v
    assert star['editor_transform']=={'x':-15,'y':145,'rotation':90,'scale':.5}
    req=request(run,changes=[{'id':'star','remove':True}]);before=sha(run/'final.png')
    preview=execute(run,'preview',dict(req,request_id='edit-remove-preview'))
    assert sha(run/'final.png')==before and [o['id'] for o in preview['objects']]==['background','photo']
    execute(run,'save',req)
    assert [o['id'] for o in read(run/'scene.json')['objects']]==['background','photo']


@pytest.mark.parametrize('value',[0,-1,float('nan'),float('inf'),11])
def test_invalid_scale_is_atomic(run,value):
    build(run);before=sha(run/'final.png')
    req=request(run,changes=[{'id':'photo','transform':{'x':0,'y':0,'scale':value}}])
    with pytest.raises(ValueError):execute(run,'save',req)
    assert sha(run/'final.png')==before


def test_layer_order_only_preview_save_pixels_and_history(run):
    a=read(run/'analysis.json');a['objects'][-1]['bbox']=[60,60,140,140];save(run/'analysis.json',a)
    build(run);before=sha(run/'final.png');order=['background','star','photo']
    req=dict(request(run,'edit-order-preview'),changes=[],layer_order=order)
    preview=execute(run,'preview',req)
    assert sha(run/'final.png')==before and sha(preview['final_file'])!=before
    assert [o['id'] for o in preview['objects']]==order
    assert Image.open(preview['final_file']).getpixel((100,100))==(174,206,173)
    execute(run,'save',dict(req,request_id='edit-order-save'))
    assert read(run/'scene.json')['layer_order']==order
    assert sha(run/'final.png')==sha(preview['final_file'])
    doc=document(run)[1];assert len(doc['versions'])==2
    assert any(c['id']=='layer_order' for c in doc['versions'][-1]['changes'])
    assert sha(run/'observability/versions'/doc['versions'][0]['id']/'final.png')==before


@pytest.mark.parametrize('order', [['background','photo'],['background','photo','photo'],['background','photo','missing']])
def test_invalid_layer_order_preserves_scene(run,order):
    build(run);before=sha(run/'scene.json')
    with pytest.raises(ValueError):execute(run,'save',dict(request(run),changes=[],layer_order=order))
    assert sha(run/'scene.json')==before


def test_layer_order_after_delete_and_linked_internal_order(run):
    a=read(run/'analysis.json');a['objects'][-1]['photo_id']='photo';save(run/'analysis.json',a)
    build(run);scene=read(run/'scene.json')
    with pytest.raises(ValueError,match='内部顺序'):apply_edits(scene,[],['background','star','photo'])
    edited=apply_edits(scene,[{'id':'star','remove':True}],['photo','background'])
    assert edited['layer_order']==['photo','background']
