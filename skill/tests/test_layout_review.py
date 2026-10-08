import copy
import pytest
from PIL import Image
from test_pipeline import task, review
from common import read, save, sha
from scene import compile_scene, apply_review
from render import render, accept_review


def adjustment(task, items=None, order=None):
    r=review(task,'needs_changes');r['items']=items or []
    if order is not None:r['layer_order']=order
    p=task/'adjustment.json';save(p,r);return p


def carrier_scene(task):
    a=read(task/'analysis.json');p=a['objects'][1]
    p.update(mode='cover',bbox=[40,70,100,150],style={})
    a['objects'].append({'id':'paper','kind':'overlay','bbox':[30,50,110,170],
                         'label':'paper','description':'local carrier','method':'local',
                         'photo_id':'photo','style':{'shape':'rectangle','fill':'#FFFFFF'}})
    a['layer_order'].append('paper');save(task/'analysis.json',a)
    compile_scene(task);render(task)


def test_reorder_changes_occlusion_without_rebuilding_or_remote_calls(task,monkeypatch):
    a=read(task/'analysis.json');a['objects'].append({'id':'cover','kind':'overlay',
        'bbox':[0,0,200,300],'label':'cover','description':'paper','method':'local',
        'style':{'shape':'rectangle','fill':'#FFFFFF'}})
    a['layer_order'].append('cover');save(task/'analysis.json',a)
    compile_scene(task);before=render(task);first=sha(task/'previews/first.png')
    import urllib.request, scene
    def forbidden(*args,**kwargs):raise AssertionError('Layout must not submit or recompile')
    monkeypatch.setattr(urllib.request,'urlopen',forbidden)
    monkeypatch.setattr(scene,'compile_scene',forbidden)
    p=adjustment(task,order=['background','cover','photo'])
    apply_review(task,p);after=render(task)
    assert before['photo_visible_fractions']['photo']==0
    assert after['photo_visible_fractions']['photo']>.99
    assert before['render_id']!=after['render_id']
    assert sha(task/'previews/first.png')==first
    with pytest.raises(ValueError,match='stale'):apply_review(task,p)


@pytest.mark.parametrize('order',[['photo'],['photo','photo'],['background','missing']])
def test_invalid_order_does_not_write_scene(task,order):
    compile_scene(task);render(task);before=sha(task/'scene.json')
    with pytest.raises(ValueError):apply_review(task,adjustment(task,order=order))
    assert sha(task/'scene.json')==before
    assert not (task/'reviews').exists()


def test_pass_cannot_include_pending_order(task):
    compile_scene(task);render(task);r=review(task)
    r['layer_order']=['background','photo'];save(task/'review.json',r)
    with pytest.raises(ValueError,match='Apply layer_order'):accept_review(task,task/'review.json')


def test_local_carrier_moves_photo_and_window_and_preserves_binding(task):
    carrier_scene(task);s=read(task/'scene.json');old_binding=copy.deepcopy(s['objects'][1]['binding'])
    p=adjustment(task,[{'id':'paper','action':'adjust','reason':'move whole card',
                       'changes':{'bbox':[80,80,160,200],'rotation':90}}])
    apply_review(task,p);render(task);s=read(task/'scene.json');photo=s['objects'][1]
    assert photo['bbox']==[90,100,150,180] and photo['rotation']==90
    assert photo['binding']==old_binding
    # The hole follows the moved photo, with a solid paper rim outside it.
    with Image.open(task/'final.png') as im:
        assert im.getpixel((120,140))[2]==180
        assert im.getpixel((65,140))==(255,255,255)
        assert im.getpixel((70,90))==(221,221,221)


def test_local_carrier_uniform_scale_moves_and_resizes_photo(task):
    carrier_scene(task)
    apply_review(task,adjustment(task,[{'id':'paper','action':'adjust','reason':'scale card',
        'changes':{'bbox':[30,50,150,230]}}]))
    photo=read(task/'scene.json')['objects'][1]
    assert photo['bbox']==[45,80,135,200]
    assert render(task)['customer_photos_verified']


def test_default_local_carrier_rotates_offset_parent_linked_photo(task):
    carrier_scene(task)
    a=read(task/'analysis.json');paper=a['objects'][-1];paper.pop('method');paper.pop('photo_id')
    a['objects'][1].update(parent_id='paper',bbox=[40,70,80,130])
    save(task/'analysis.json',a);compile_scene(task);render(task)
    apply_review(task,adjustment(task,[{'id':'paper','action':'adjust','reason':'rotate',
                                      'changes':{'rotation':90}}]))
    photo=read(task/'scene.json')['objects'][1]
    assert photo['bbox']==[60,70,100,130] and photo['rotation']==90
    assert render(task)['photo_visible_fractions']['photo']>.99


@pytest.mark.parametrize('changes',[{'bbox':[30,50,150,170]}, {'bbox':[150,200,230,320]}])
def test_invalid_carrier_transform_is_atomic(task,changes):
    carrier_scene(task);before=sha(task/'scene.json')
    with pytest.raises(ValueError):apply_review(task,adjustment(task,[
        {'id':'paper','action':'adjust','reason':'bad transform','changes':changes}]))
    assert sha(task/'scene.json')==before
    assert not (task/'reviews').exists()


def test_reject_double_geometry_but_allow_crop_with_carrier_move(task):
    carrier_scene(task)
    items=[{'id':'paper','action':'adjust','reason':'move','changes':{'bbox':[40,50,120,170]}},
           {'id':'photo','action':'adjust','reason':'move','changes':{'bbox':[50,70,110,150]}}]
    with pytest.raises(ValueError,match='linked photos follow'):apply_review(task,adjustment(task,items))
    items[1]['changes']={'crop_center':[.2,.7]}
    apply_review(task,adjustment(task,items));photo=read(task/'scene.json')['objects'][1]
    assert photo['bbox']==[50,70,110,150]
    assert photo['binding']['crop_center']==[.2,.7]


def test_extracted_layer_reorder_keeps_pixels_and_geometry_protection(task,monkeypatch):
    from test_reveal import frame_task
    from scene import load_scene
    config=frame_task(task)
    save(task/'recovery.json',{'schema_version':'collage-recovery-v1',
        'reference_sha256':read(task/'input.json')['reference']['sha256'],
        'windows':[{'overlay_id':'frame','photo_id':'photo','reason':'fixture clear window'}]})
    compile_scene(task,config);render(task)
    scene=load_scene(task);frame=next(o for o in scene['objects'] if o['id']=='frame')
    before=sha(frame['recovered']['file'])
    import urllib.request
    def forbidden(*args,**kwargs):raise AssertionError('No remote extraction during apply')
    monkeypatch.setattr(urllib.request,'urlopen',forbidden)
    order=list(scene['layer_order']);order.remove('frame');order.append('frame')
    apply_review(task,adjustment(task,order=order));render(task)
    assert sha(frame['recovered']['file'])==before
    with pytest.raises(ValueError,match='Recovered geometry'):
        apply_review(task,adjustment(task,[{'id':'frame','action':'adjust','reason':'move',
                                          'changes':{'rotation':10}}]))


def extracted_decoration(task):
    from PIL import ImageDraw
    from test_reveal import cache_for
    a=read(task/'analysis.json')
    o={'id':'sticker','kind':'overlay','bbox':[20,10,60,40],'label':'sticker',
       'description':'two separated marks, one complete unit','method':'extract'}
    a['objects'].append(o);a['layer_order'].append('sticker');save(task/'analysis.json',a)
    im=Image.new('RGBA',(200,300));d=ImageDraw.Draw(im)
    d.rectangle((20,10,28,18),fill='#00FF00');d.rectangle((48,30,58,38),fill='#00FF00')
    compile_scene(task,cache_for(task,[o],[im]));render(task)
    return read(task/'scene.json')['objects'][-1]


def test_translate_extraction_preserves_all_pixels_and_round_trips_offline(task,monkeypatch):
    import urllib.request
    import numpy as np
    o=extracted_decoration(task);source=o['recovered']['file'];source_hash=sha(source)
    first=sha(task/'previews/first.png');before=sha(task/'final.png')
    def forbidden(*args,**kwargs):raise AssertionError('Translation must stay offline')
    monkeypatch.setattr(urllib.request,'urlopen',forbidden)
    apply_review(task,adjustment(task,[{'id':'sticker','action':'adjust','reason':'avoid face',
        'changes':{'bbox':[50,60,90,90]}}]));render(task)
    with Image.open(task/'final.png') as im:
        a=np.asarray(im);green=(a==[0,255,0]).all(axis=2)
        assert green[60:69,50:59].all() and green[80:89,78:89].all()
        assert not green[10:19,20:29].any() and green.sum()==9*9+11*9
    assert read(task/'scene.json')['objects'][-1]['extracted_offset']==[30,50]
    assert sha(source)==source_hash and sha(task/'previews/first.png')==first
    apply_review(task,adjustment(task,[{'id':'sticker','action':'adjust','reason':'restore',
        'changes':{'bbox':[20,10,60,40]}}]));render(task)
    assert sha(task/'final.png')==before and sha(source)==source_hash


@pytest.mark.parametrize('changes',[{'bbox':[50,60,100,90]},{'rotation':10},
    {'style':{'shape':'star'}},{'bbox':[50,60,90,90],'style':{'opacity':.5}}])
def test_extracted_decoration_rejects_redrawing_or_deformation_atomically(task,changes):
    extracted_decoration(task);before=sha(task/'scene.json')
    with pytest.raises(ValueError,match='Recovered geometry'):
        apply_review(task,adjustment(task,[{'id':'sticker','action':'adjust','reason':'bad transform','changes':changes}]))
    assert sha(task/'scene.json')==before
