import pytest
from PIL import Image, ImageDraw
from test_pipeline import task
from test_reveal import cache_for, frame_task
from common import read, save
from scene import compile_scene
from render import render


@pytest.mark.parametrize('transparent',[False,True])
def test_explicit_backing_stays_below_photos_even_with_clear_opening(task,transparent):
    a=read(task/'analysis.json');p=a['objects'][1]
    p.update(mode='cover',bbox=[40,50,160,210],parent_id='board',style={})
    board={'id':'board','kind':'overlay','bbox':[0,0,200,300],
           'label':'board','description':'backing below photo','method':'extract'}
    a['objects'].append(board);a['layer_order']=['background','board','photo'];save(task/'analysis.json',a)
    layer=Image.new('RGBA',(200,300),'red')
    if transparent:ImageDraw.Draw(layer).rectangle((40,50,159,209),fill=(0,0,0,0))
    s=compile_scene(task,cache_for(task,[board],[layer]))
    assert s['layer_order']==a['layer_order']
    assert render(task)['photo_visible_fractions']['photo']>.99
    assert Image.open(task/'final.png').getpixel((100,100))[2]==180


def test_opaque_foreground_is_reported_without_silent_demotion_or_erasure(task):
    config=frame_task(task);s=compile_scene(task,config)
    assert s['layer_order']==read(task/'analysis.json')['layer_order']
    check=read(task/'assets/reveal/index.json')['records'][0]['layer_checks'][0]
    assert check['position']=='above_photo' and check['review_required']
    assert check['visible_fraction']==1
    assert Image.open(task/'assets/reveal/frame.png').getpixel((100,100))==(255,0,0,255)
    result=render(task)
    assert 'frame' in result['incomplete_objects']
    assert result['photo_visible_fractions']['photo']>.99


def test_tiny_clear_patch_does_not_authorize_derived_foreground(task):
    config=frame_task(task,True)
    path=task.parent/'reveal-cache/batch_01/layers_aug_01.png'
    with Image.open(path) as raw:layer=raw.convert('RGBA')
    ImageDraw.Draw(layer).rectangle((90,90,92,92),fill=(0,0,0,0));layer.save(path)
    s=compile_scene(task,config)
    rec=read(task/'assets/reveal/index.json')['records'][0]
    assert rec['layer_placement']=='derived_foreground_blocked'
    assert s['layer_order'].index(rec['id'])<s['layer_order'].index('photo')


def test_clear_derived_opening_allows_foreground(task):
    config=frame_task(task,True)
    path=task.parent/'reveal-cache/batch_01/layers_aug_01.png'
    with Image.open(path) as raw:layer=raw.convert('RGBA')
    ImageDraw.Draw(layer).rectangle((21,31,178,240),fill=(0,0,0,0));layer.save(path)
    s=compile_scene(task,config)
    rec=read(task/'assets/reveal/index.json')['records'][0]
    assert rec['layer_placement']=='derived_above_clear_window'
    assert s['layer_order'].index(rec['id'])>s['layer_order'].index('photo')
