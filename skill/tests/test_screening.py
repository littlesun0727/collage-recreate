import pytest
from PIL import Image, ImageDraw
from test_pipeline import task
from test_reveal import frame_task, cache_for
from common import read, save, sha
from scene import compile_scene, load_scene
from render import render
from screening import screen


def submit(task, decisions, render_id=None):
    file=task/'screen-plan.json'
    save(file,{'render_id':render_id or read(task/'result.json')['render_id'],'decisions':decisions})
    return screen(task,file)


def test_draw_carrier_uses_photo_hole_without_repair_or_acquisition(task,monkeypatch):
    import reveal,asset_repair
    compile_scene(task,frame_task(task));render(task)
    source=sha(task/'assets/reveal/frame.png');first=sha(task/'previews/first.png')
    photo=sha(task/'assets/photos/photo.png');analysis=sha(task/'analysis.json')
    monkeypatch.setattr(reveal,'acquire',lambda *a,**k:pytest.fail('No extraction in screen'))
    monkeypatch.setattr(asset_repair,'worker',lambda *a,**k:pytest.fail('No repair model in screen'))
    r=submit(task,[{'id':'frame','action':'draw','style':{'shape':'paper','fill':'#FFFFFF','texture':'none'},'reason':'Simple carrier with old photo'}])
    assert r['photo_visible_fractions']['photo']>.99 and 'frame' not in r['incomplete_objects']
    im=Image.open(task/'final.png');assert im.getpixel((100,100))[2]==180 and im.getpixel((30,40))==(255,255,255)
    assert source==sha(task/'assets/reveal/frame.png') and first==sha(task/'previews/first.png')
    assert photo==sha(task/'assets/photos/photo.png') and analysis==sha(task/'analysis.json')
    s=load_scene(task);assert s['objects'][-1]['gate_local']
    s['objects'][-1]['style']['fill']='#FF0000';save(task/'scene.json',s)
    with pytest.raises(ValueError,match='style changed'):render(task)


def test_drop_keeps_missing_status_and_rejects_stale_or_second_pass(task):
    compile_scene(task,frame_task(task));render(task)
    d=[{'id':'frame','action':'drop','reason':'Unusable content'}]
    with pytest.raises(ValueError,match='render_id'):submit(task,d,'stale')
    r=submit(task,d);assert 'frame' in r['incomplete_objects']
    assert not load_scene(task)['objects'][-1].get('recovered')
    with pytest.raises(ValueError,match='already applied'):submit(task,d)


@pytest.mark.parametrize('unknown',[False,True])
def test_confirmed_embedded_text_avoids_duplicate_or_unknown_placeholder(task,unknown):
    a=read(task/'analysis.json')
    board={'id':'board','kind':'overlay','bbox':[0,0,180,30],'label':'board','description':'caption carrier','method':'extract'}
    caption={'id':'caption','kind':'text','bbox':[10,5,100,25],'label':'caption','description':'caption',
             'text':None if unknown else 'hello','text_status':'unreadable' if unknown else 'known','embedded_in':'board'}
    a['objects'] += [board,caption];a['layer_order'] += ['board','caption'];save(task/'analysis.json',a)
    image=Image.new('RGBA',(200,300));d=ImageDraw.Draw(image);d.rectangle((0,0,179,29),fill='white');d.rectangle((15,7,80,18),fill='black')
    compile_scene(task,cache_for(task,[board],[image]));render(task)
    r=submit(task,[{'id':'board','action':'keep','embedded_ids':['caption'],'reason':'Complete printed caption visually confirmed'}])
    assert load_scene(task)['objects'][-1]['embedded_owner']=='board'
    assert 'caption' not in r['incomplete_objects']


def test_empty_candidate_cannot_be_kept(task):
    config=frame_task(task);Image.new('RGBA',(200,300)).save(task.parent/'reveal-cache/batch_01/layers_aug_01.png')
    compile_scene(task,config);render(task)
    with pytest.raises(ValueError,match='Cannot keep'):submit(task,[{'id':'frame','action':'keep','reason':'Invalid acceptance'}])


def test_explicit_local_carrier_does_not_request_extraction(task):
    from reveal_assets import targets
    config=frame_task(task)
    a=read(task/'analysis.json');a['objects'][-1].update(method='local',style={'shape':'rectangle','fill':'#FFFFFF'})
    save(task/'analysis.json',a);s=compile_scene(task,{'enabled':False});r=render(task)
    assert not targets(s)[0]['request'] and r['photo_visible_fractions']['photo']>.99


def test_screen_can_adjust_existing_local_primitive_without_fake_rejection(task):
    a=read(task/'analysis.json')
    a['objects'].append({'id':'badge','kind':'overlay','bbox':[10,5,110,25],'label':'badge','description':'plain capsule',
                         'method':'local','style':{'shape':'rectangle','fill':'#FFFFFF'}})
    a['layer_order'].append('badge');save(task/'analysis.json',a)
    compile_scene(task,{'enabled':True,'remote':False,'layout':'grouped'});render(task)
    first=sha(task/'previews/first.png')
    r=submit(task,[{'id':'badge','action':'draw','style':{'shape':'rounded_rectangle','radius':.5,'fill':'#FFFFFF'},'reason':'Match capsule corners'}])
    assert r['asset_gate_summary']['rejected']==0 and not r['incomplete_objects']
    assert sha(task/'previews/first.png')==first and r['final_sha256']!=first
    assert load_scene(task)['objects'][-1]['style']['radius']==.5
