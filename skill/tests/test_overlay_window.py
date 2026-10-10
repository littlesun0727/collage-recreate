"""Transparent player cards keep their opening independently of photo placement."""
import copy
import numpy as np
import pytest
from PIL import Image
from test_light_render import task
from common import read, save, sha
from scene import compile_scene, apply_review
from render import render
from overlays import draw_overlay
from validate import analysis_check


def card():
    return {'id':'player','kind':'overlay','label':'Player','description':'Open player card',
            'bbox':[50,30,250,270],'method':'local','photo_id':'photo',
            'style':{'shape':'rounded_rectangle','fill':'#FFFFFF','radius':.06,
                     'window':{'bbox':[.1,.1,.9,.75],'radius':.08}}}


def test_window_preserves_footer_and_antialiased_inner_corners():
    im,_=draw_overlay(card(),(200,240));alpha=np.asarray(im)[:,:,3]
    assert alpha[80,100]==0 and alpha[220,100]==255 and alpha[80,10]==255
    assert alpha[24,20]>200 and ((alpha[24:40,20:36]>0)&(alpha[24:40,20:36]<255)).any()


@pytest.mark.parametrize('mode',['cover','cutout'])
def test_render_editor_and_revision_preserve_explicit_opening(tmp_path,monkeypatch,mode):
    run=task(tmp_path);a=read(run/'analysis.json')
    a['objects'][0]['mode']=mode
    a['objects'][0]['bbox']=[100,80,200,200]
    a['objects'].extend([
        {'id':'bg','kind':'background','label':'Background','description':'Blue background',
         'bbox':[0,0,300,300],'style':{'shape':'rectangle','fill':'#112233'}},card()])
    a['layer_order']=['bg','player','photo'];save(run/'analysis.json',a)
    if mode=='cutout':
        def cutout_photo(obj,source,binding,size,root,model):
            im=Image.new('RGBA',size);im.paste('#C82211',(30,20,70,100))
            mask=root/'assets/photos/test-mask.png';im.getchannel('A').save(mask)
            return im,{'content_mask':str(mask),'content_mask_sha256':sha(mask)}
        monkeypatch.setattr('render.make_photo',cutout_photo)
    compile_scene(run);result=render(run)
    final=Image.open(run/'final.png')
    assert final.getpixel((80,100))==(17,34,51)
    assert final.getpixel((150,230))==(255,255,255)
    assert final.getpixel((150,150))==(200,34,17)
    before=sha(run/'final.png');render(run,capture_layers=True,editor_export=True)
    assert sha(run/'final.png')==before
    layer=Image.open(run/'editor_layers/player.png')
    assert layer.getpixel((80,100))[3]==0 and layer.getpixel((150,230))[3]==255
    p={'schema_version':'collage-review-v1','render_id':result['render_id'],
       'verdict':'needs_changes','summary':'Change window while preserving the person',
       'items':[{'id':'player','action':'adjust','reason':'Larger bottom margin',
                 'changes':{'style':{'window':{'bbox':[.1,.1,.9,.65],'radius':.08}}}}]}
    save(run/'edit.json',p);apply_review(run,run/'edit.json');render(run)
    assert Image.open(run/'final.png').getpixel((80,200))==(255,255,255)


@pytest.mark.parametrize('change',[{'bbox':[.8,.1,.2,.9]}, {'bbox':[0,0,1,1],'radius':.8}])
def test_invalid_window_rejected_before_render(change):
    obj=card();obj.pop('photo_id');obj['style']['window']=copy.deepcopy(change)
    with pytest.raises(ValueError):
        analysis_check({'schema_version':'collage-analysis-v1','reference_size':[300,300],
                        'objects':[obj],'layer_order':['player']})
