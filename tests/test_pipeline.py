import copy
import json
import sys
from pathlib import Path
import pytest
import numpy as np
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha
from prepare import prepare
from validate import analysis_check,bindings_check
from scene import compile_scene,apply_review
from render import render,accept_review


@pytest.fixture
def task(tmp_path):
    sources=tmp_path/'sources';sources.mkdir()
    a=np.zeros((120,100,3),dtype='uint8');a[:,:,0]=np.arange(100)[None,:]*2;a[:,:,2]=180
    Image.fromarray(a).save(sources/'customer.png');Image.new('RGB',(200,300),'#dddddd').save(tmp_path/'reference.png')
    run=tmp_path/'run';prepare(tmp_path/'reference.png',[sources],run,width=200)
    analysis={'schema_version':'collage-analysis-v1','reference_size':[200,300],'objects':[{'id':'background','kind':'background','bbox':[0,0,200,300],'label':'base','description':'plain','style':{'fill':'#DDDDDD'}},{'id':'photo','kind':'photo','mode':'feather','bbox':[10,20,190,280],'label':'photo','description':'portrait','style':{'feather':.15}}],'layer_order':['background','photo']}
    save(run/'analysis.json',analysis);aid=read(run/'prepared/catalog.json')['assets'][0]['id']
    save(run/'bindings.json',{'schema_version':'collage-bindings-v1','photos':[{'slot_id':'photo','asset_id':aid,'reason':'test'}],'texts':[]})
    return run


def review(run,verdict='pass'):
    result=read(run/'result.json');scene=read(run/'scene.json')
    return {'schema_version':'collage-review-v1','scene_sha256':result['scene_sha256'],'final_sha256':result['final_sha256'],'verdict':verdict,'summary':'checked','items':[{'id':o['id'],'action':'keep','reason':'checked'} for o in scene['objects']]}


def test_feather_retains_customer_rgb_and_alpha(task):
    compile_scene(task);r=render(task)
    a=np.asarray(Image.open(task/'assets/photos/photo.png'))
    assert (a[:,:,2]==180).all() and a[:,:,0].max()<210
    assert a[:,:,3].min()<10 and a[:,:,3].max()==255
    assert r['customer_photos_verified'] and r['photo_visible_fractions']['photo']>.99
    assert not r['renders_verified']


@pytest.mark.parametrize('mutation',[lambda a:a['objects'][1].update(bbox=[0,0,201,100]),lambda a:a.update(layer_order=['background','background']),lambda a:a['objects'][1].update(id='background'),lambda a:a['objects'][1].update(extra='silently ignored')])
def test_invalid_analysis_rejected(task,mutation):
    a=read(task/'analysis.json');mutation(a)
    with pytest.raises(ValueError):analysis_check(a,read(task/'input.json'))


def test_missing_photo_binding_rejected(task):
    b=read(task/'bindings.json');b['photos']=[];save(task/'bindings.json',b)
    with pytest.raises(ValueError):compile_scene(task)


def test_changed_customer_source_rejected(task):
    compile_scene(task)
    source=read(task/'prepared/catalog.json')['assets'][0]['file'];Image.new('RGB',(100,120),'white').save(source)
    with pytest.raises(ValueError):render(task)


def test_placeholder_cannot_pass(task):
    a=read(task/'analysis.json');a['objects'].append({'id':'sticker','kind':'overlay','bbox':[20,20,70,80],'label':'animal','description':'rabbit','method':'placeholder'});a['layer_order'].append('sticker');save(task/'analysis.json',a)
    compile_scene(task);render(task);save(task/'review.json',review(task))
    with pytest.raises(ValueError):accept_review(task,task/'review.json')


def test_occluded_photo_cannot_pass(task):
    a=read(task/'analysis.json');a['objects'].append({'id':'cover','kind':'overlay','bbox':[0,0,200,300],'label':'cover','description':'white','style':{'shape':'rectangle','fill':'#FFFFFF'}});a['layer_order'].append('cover');save(task/'analysis.json',a)
    compile_scene(task);r=render(task)
    assert r['photo_visible_fractions']['photo']==0 and 'photo' in r['incomplete_objects']


def test_review_stale_after_changes_and_first_preserved(task):
    compile_scene(task);render(task);first=sha(task/'previews/first.png');r=review(task,'needs_changes');r['items'][1].update(action='adjust',changes={'bbox':[20,30,180,270]});save(task/'review.json',r)
    apply_review(task,task/'review.json');render(task)
    assert sha(task/'previews/first.png')==first
    with pytest.raises(ValueError):accept_review(task,task/'review.json')


def test_repeat_render_deterministic_and_acceptance_not_implicit(task):
    compile_scene(task);r=render(task);digest=r['final_sha256'];save(task/'review.json',review(task));assert accept_review(task,task/'review.json')['renders_verified']
    again=render(task);assert again['final_sha256']==digest and not again['renders_verified']


def test_raw_crop_over_photo_becomes_explicit_placeholder(task):
    a=read(task/'analysis.json');a['objects'].append({'id':'extract','kind':'overlay','bbox':[20,30,100,140],'label':'extract','description':'over portrait','method':'extract','style':{'extract_mode':'crop'}});a['layer_order'].append('extract');save(task/'analysis.json',a)
    compile_scene(task);r=render(task);assert 'extract' in r['incomplete_objects']


def test_yibu_dry_run_never_reads_credentials(task):
    from argparse import Namespace
    from generation.generate import generate
    compile_scene(task);render(task)
    result=generate(task,Namespace(ids=['background'],workers=1,allow_remote=False,dry_run=True,credentials=str(task/'does-not-exist.json'),timeout=30))
    assert result['status']=='dry_run_verified'
    call=read(next((task/'generation').glob('*/request/call.json')))
    assert call['http_dispatches']==0


def test_fused_generation_preserves_member_coverage_and_customer_pixels(task,monkeypatch):
    from argparse import Namespace
    import generation.generate as generator
    a=read(task/'analysis.json')
    for oid,box in [('deco1',[10,10,60,60]),('deco2',[50,20,90,60])]:
        a['objects'].append({'id':oid,'kind':'overlay','bbox':box,'label':oid,'description':'synthetic decoration','method':'placeholder'})
        a['layer_order'].append(oid)
    save(task/'analysis.json',a);compile_scene(task);render(task)
    photo_sha=sha(task/'assets/photos/photo.png');requests=[]
    def fake_request(run,scene,obj,args):
        requests.append(obj);p=run/'synthetic-generated.png';Image.new('RGBA',(80,50),(255,0,0,200)).save(p)
        return obj['id'],{'file':str(p),'sha256':sha(p),'request':'test-stub'}
    monkeypatch.setattr(generator,'request_one',fake_request)
    result=generator.generate(task,Namespace(ids=['deco1','deco2'],workers=1,group=True,allow_remote=True,dry_run=False))
    assert result['status']=='generated' and len(requests)==1
    s=read(task/'scene.json');assert s['generated_groups'][0]['bbox']==[10,10,90,60]
    r=read(task/'result.json');assert r['coverage_complete'] and r['incomplete_objects']==[]
    assert sha(task/'assets/photos/photo.png')==photo_sha
    assert len([x for x in r['resources'] if x['group']=='deco2'])==2
    with pytest.raises(ValueError,match='all members'):
        generator.generate(task,Namespace(ids=['deco1'],workers=1,group=False,allow_remote=True,dry_run=False))


def test_fused_generation_rejects_interleaved_photo_before_network(task):
    from argparse import Namespace
    from generation.generate import generate
    a=read(task/'analysis.json')
    for oid in ['deco1','deco2']:
        a['objects'].append({'id':oid,'kind':'overlay','bbox':[10,10,80,80],'label':oid,'description':'test','method':'placeholder'})
    a['layer_order']=['background','deco1','photo','deco2'];save(task/'analysis.json',a)
    compile_scene(task);render(task)
    with pytest.raises(ValueError,match='interleaved'):
        generate(task,Namespace(ids=['deco1','deco2'],workers=1,group=True,allow_remote=True,dry_run=False))


def test_dashed_frame_leaves_gaps_and_transparent_center():
    from overlays import draw_overlay
    im,_=draw_overlay({'kind':'overlay','style':{'shape':'frame','stroke':'#FFFFFF','stroke_width':4,'dash':[12,10],'line_cap':'round'}},(200,160))
    alpha=np.asarray(im)[:,:,3]
    assert alpha[80,100]==0
    assert (alpha[2,10:190]==0).sum()>25 and (alpha[2,10:190]>0).sum()>50
    assert np.all(alpha[10:150,10:190]==0)


def test_review_cannot_certify_a_final_changed_outside_renderer(task):
    compile_scene(task);render(task);r=review(task)
    Image.new('RGB',(200,300),'white').save(task/'final.png');r['final_sha256']=sha(task/'final.png');save(task/'review.json',r)
    with pytest.raises(ValueError,match='result is stale'):accept_review(task,task/'review.json')


def test_review_invalid_crop_rejected_without_scene_mutation(task):
    compile_scene(task);render(task);before=sha(task/'scene.json');r=review(task,'needs_changes')
    r['items'][1].update(action='adjust',changes={'source_crop':[.8,0,.2,1]});save(task/'review.json',r)
    with pytest.raises(ValueError,match='positive area'):apply_review(task,task/'review.json')
    assert sha(task/'scene.json')==before


def test_reference_separation_can_succeed_despite_full_photo_bbox_overlap(tmp_path):
    from PIL import ImageDraw
    from extraction import extract,sheets
    ref=Image.new('RGB',(120,120),'#00FF00');ImageDraw.Draw(ref).ellipse((20,15,100,105),fill='#CC2211')
    obj={'id':'sticker','bbox':[0,0,120,120],'style':{'extract_mode':'color','extract_background':'#00FF00'}}
    tile,meta=extract(obj,ref,[{'bbox':[0,0,120,120]}],(120,120),tmp_path)
    assert meta['quality']=='needs_review' and meta['photo_bbox_overlap']==1
    assert tile.getpixel((0,0))[3]==0 and tile.getpixel((60,60))==(204,34,17,255)
    (tmp_path/'previews').mkdir();paths=sheets(tmp_path,[{'id':'sticker','metadata':meta}])
    assert len(paths)==1 and Path(paths[0]).exists()


def test_reference_auto_uses_local_foreground_instead_of_geometry_veto(tmp_path,monkeypatch):
    from PIL import ImageDraw
    import extraction
    calls=[]
    def segment(im,source,model,cache):
        calls.append(model);im=im.copy();alpha=Image.new('L',im.size);ImageDraw.Draw(alpha).ellipse((10,10,70,70),fill=255);im.putalpha(alpha);return im
    monkeypatch.setattr(extraction,'cutout',segment)
    ref=Image.new('RGB',(80,80),'#CA4567')
    tile,meta=extraction.extract({'id':'sticker','bbox':[0,0,80,80],'style':{}},ref,[{'bbox':[0,0,80,80]}],(80,80),tmp_path,'local-weights')
    assert calls==['local-weights'] and meta['extraction_mode']=='birefnet'
    assert tile.getpixel((40,40))==(202,69,103,255) and tile.getpixel((0,0))[3]==0


def test_reference_grabcut_fallback_preserves_foreground_pixels(tmp_path):
    from PIL import ImageDraw
    from extraction import extract
    ref=Image.new('RGB',(100,100),'#CCDDEE');ImageDraw.Draw(ref).ellipse((22,15,78,85),fill='#CA1234')
    tile,meta=extract({'id':'sticker','bbox':[0,0,100,100],'style':{}},ref,[{'bbox':[0,0,100,100]}],(100,100),tmp_path)
    assert meta['extraction_mode']=='grabcut'
    assert tile.getpixel((50,50))==(202,18,52,255) and tile.getpixel((0,0))[3]==0
