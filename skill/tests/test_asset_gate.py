import numpy as np
import pytest
from PIL import Image, ImageDraw
from test_pipeline import task, review
from test_reveal import frame_task, cache_for
from common import read, save, sha
from scene import compile_scene, load_scene
from render import render, accept_review
from recovery import recover, check_plan
from asset_repair import apply_edits, text_mask, selection


def plan(task, **kw):
    return {'schema_version':'collage-recovery-v1','reference_sha256':read(task/'input.json')['reference']['sha256'],**kw}


def decision(task, oid, **kw):
    r=next(x for x in read(task/'assets/gate/report.json')['records'] if x['id']==oid)
    return {'id':oid,'input_key':r['input_key'],'verdict':'accepted','reason':'Actual pixels visually checked',**kw}


def test_quarantine_preserves_candidate_but_cannot_pass(task):
    compile_scene(task,frame_task(task));result=render(task)
    assert Image.open(task/'assets/reveal/frame.png').getpixel((100,100))==(255,0,0,255)
    assert Image.open(task/'final.png').getpixel((100,100))[2]==180
    assert result['asset_gate_summary']['pending']==1 and 'frame' in result['incomplete_objects']
    save(task/'review.json',review(task))
    with pytest.raises(ValueError):accept_review(task,task/'review.json')


def test_visual_decision_bound_to_candidate_and_analysis(task):
    config=frame_task(task);compile_scene(task,config)
    save(task/'recovery.json',plan(task,decisions=[decision(task,'frame')]))
    compile_scene(task,config);assert load_scene(task)['objects'][-1]['gate']['status']=='accepted'
    a=read(task/'analysis.json');a['objects'][-1]['description']='Changed expected content';save(task/'analysis.json',a)
    compile_scene(task,config)
    assert load_scene(task)['objects'][-1]['gate']['status']=='pending'
    assert 'stale_visual_decision' in read(task/'assets/gate/report.json')['records'][0]['issues']


def test_renderer_rejects_forged_gate_or_local_bypass(task):
    compile_scene(task,frame_task(task));s=load_scene(task);s['objects'][-1]['gate_local']=True;save(task/'scene.json',s)
    with pytest.raises(ValueError,match='Unapproved local'):render(task)


def test_erase_is_exact_offline_idempotent(task,monkeypatch):
    import reveal
    config=frame_task(task);compile_scene(task,config);render(task)
    first=sha(task/'previews/first.png');raw=sha(task.parent/'reveal-cache/batch_01/layers_aug_01.png')
    monkeypatch.setattr(reveal,'fetch_batch',lambda *a,**k:pytest.fail('Network forbidden'))
    file=task/'fix.json';save(file,plan(task,edits=[{'id':'frame','operation':'erase','bbox':[40,50,160,210],'reason':'Confirmed old photo'}]))
    r=recover(task,file);pixels=np.asarray(Image.open(task/'assets/reveal/frame.png'))
    assert pixels[100,100,3]==0 and pixels[40,30,3]==255
    assert 'frame' not in r['incomplete_objects'] and r['photo_visible_fractions']['photo']>.99
    assert recover(task,file)['final_sha256']==r['final_sha256']
    assert sha(task/'previews/first.png')==first and sha(task.parent/'reveal-cache/batch_01/layers_aug_01.png')==raw


def test_fill_preserves_alpha_and_unselected_pixels(task):
    im=Image.new('RGBA',(100,100),(12,24,36,127))
    out,records,errors=apply_edits(task,im,'board',{'edits':[{'id':'board','operation':'fill','bbox':[20,20,40,40],'fill':'#FFFF00'}]})
    assert not errors and records[0]['status']=='applied'
    assert out.getpixel((30,30))==(255,255,0,127) and out.getpixel((19,30))==(12,24,36,127)
    assert out.getchannel('A').tobytes()==im.getchannel('A').tobytes()


def test_missing_lama_quarantines_without_pixel_damage(task):
    compile_scene(task,frame_task(task));save(task/'repair-tools.json',{})
    file=task/'fix.json';save(file,plan(task,edits=[{'id':'frame','operation':'inpaint','bbox':[40,50,160,210],'reason':'Confirmed contamination'}]))
    result=recover(task,file)
    assert 'frame' in result['incomplete_objects']
    r=read(task/'assets/gate/report.json')['records'][0]
    assert 'repair_failed' in r['issues'] and Image.open(r['candidate']['file']).getpixel((100,100))==(255,0,0,255)


def test_ocr_matches_only_authorized_text_and_clips_dilation():
    im=Image.new('RGBA',(100,100),'white');allowed=selection(im.size,{'bbox':[10,10,70,40]})
    records=[{'text':'DELETE','confidence':.99,'polygon':[[11,11],[40,11],[40,25],[11,25]]},
             {'text':'KEEP','confidence':.99,'polygon':[[50,11],[90,11],[90,25],[50,25]]}]
    mask,selected=text_mask(im,allowed,records,{'texts':['DELETE'],'padding':8})
    assert len(selected)==1 and mask.getpixel((10,10))==255 and mask.getpixel((9,10))==0 and mask.getpixel((60,20))==0
    with pytest.raises(RuntimeError,match='not confidently'):text_mask(im,allowed,records,{'texts':['MISSING']})


def test_inpainting_worker_cannot_change_unselected_pixels_or_alpha(task,monkeypatch):
    import asset_repair
    monkeypatch.setattr(asset_repair,'worker',lambda *args:Image.new('RGB',(50,50),'green'))
    im=Image.new('RGBA',(50,50),(255,0,0,80))
    out,_,errors=apply_edits(task,im,'x',{'edits':[{'id':'x','operation':'inpaint','bbox':[5,5,15,15]}]})
    assert not errors and out.getpixel((0,0))==im.getpixel((0,0)) and out.getpixel((10,10))==(0,128,0,80)


def test_legal_separate_shapes_and_shadow_slack_are_not_removed(task):
    a=read(task/'analysis.json');obj={'id':'deco','kind':'overlay','bbox':[40,40,140,90],'method':'extract','label':'two ornaments','description':'two separate shapes'}
    a['objects'].append(obj);a['layer_order'].append('deco');save(task/'analysis.json',a)
    im=Image.new('RGBA',(200,300));d=ImageDraw.Draw(im);d.ellipse((38,38,68,68),fill='red');d.ellipse((110,60,139,89),fill='green')
    compile_scene(task,cache_for(task,[obj],[im]));s=load_scene(task)
    assert s['objects'][-1]['gate']['status']=='accepted'
    assert Image.open(s['objects'][-1]['recovered']['file']).tobytes()==im.tobytes()


def test_large_outlier_is_quarantined_not_automatically_deleted(task):
    a=read(task/'analysis.json');obj={'id':'deco','kind':'overlay','bbox':[40,40,80,80],'method':'extract','label':'deco','description':'ornament'}
    a['objects'].append(obj);a['layer_order'].append('deco');save(task/'analysis.json',a)
    im=Image.new('RGBA',(200,300));d=ImageDraw.Draw(im);d.rectangle((40,40,79,79),fill='red');d.rectangle((140,200,180,250),fill='green')
    compile_scene(task,cache_for(task,[obj],[im]));r=read(task/'assets/gate/report.json')['records'][0]
    assert r['status']=='pending' and 'pixels_outside_expected_extent' in r['issues']
    assert Image.open(r['candidate']['file']).tobytes()==im.tobytes()


def test_minor_window_edge_is_warning_not_blanket_rejection(task):
    config=frame_task(task)
    file=task.parent/'reveal-cache/batch_01/layers_aug_01.png'
    im=Image.open(file).convert('RGBA');ImageDraw.Draw(im).rectangle((44,54,155,205),fill=(0,0,0,0));im.save(file)
    s=compile_scene(task,config);r=read(task/'assets/gate/report.json')['records'][0]
    assert r['status']=='accepted' and 'foreground_window_edge_content' in r['warnings']
    assert s['objects'][-1].get('recovered') and render(task)['photo_visible_fractions']['photo']>.85


def test_local_fallback_only_uses_existing_supported_primitive(task):
    a=read(task/'analysis.json');obj={'id':'dot','kind':'overlay','bbox':[0,0,30,30],
        'method':'extract','label':'dot','description':'solid circle','style':{'shape':'ellipse','fill':'#FF0000'}}
    a['objects'].append(obj);a['layer_order'].append('dot');save(task/'analysis.json',a)
    config=cache_for(task,[obj],[Image.new('RGBA',(200,300))])
    s=compile_scene(task,config);r=render(task)
    assert s['objects'][-1]['gate_local'] and 'dot' not in r['incomplete_objects']
    assert Image.open(task/'final.png').getpixel((15,15))==(255,0,0)


@pytest.mark.parametrize('edit',[
    {'operation':'erase','bbox':[-1,0,40,40]},
    {'operation':'remove_text','bbox':[0,0,40,40]},
    {'operation':'fill','bbox':[0,0,40,40],'fill':'nonsense'},
    {'operation':'erase','polygon':[[1,1],[2,2],[3,3]]},
])
def test_invalid_edit_rejected(task,edit):
    s=compile_scene(task,frame_task(task))
    with pytest.raises(ValueError):check_plan(plan(task,edits=[{'id':'frame','reason':'test',**edit}]),s)
