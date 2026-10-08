"""Observable geometry, source coverage and review-version regression tests."""
import sys
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha
from prepare import prepare
from scene import compile_scene,apply_review
from render import render,accept_review
from effects import rounded_mask,shadow_layer
from overlays import draw_overlay
from paths import smooth_points
from validate import analysis_check


def task(tmp_path,appearance=None,style=None,rotation=0):
    sources=tmp_path/'sources';sources.mkdir()
    Image.new('RGB',(120,160),'#C82211').save(sources/'customer.png')
    Image.new('RGB',(300,300),'#112233').save(tmp_path/'reference.png')
    run=tmp_path/'run';prepare(tmp_path/'reference.png',[sources],run,width=300)
    photo={'id':'photo','kind':'photo','bbox':[75,60,225,240],'label':'photo','description':'synthetic','rotation':rotation,'style':style or {}}
    if appearance:photo['appearance']=appearance
    save(run/'analysis.json',{'schema_version':'collage-analysis-v1','reference_size':[300,300],'objects':[photo],'layer_order':['photo']})
    aid=read(run/'prepared/catalog.json')['assets'][0]['id']
    save(run/'bindings.json',{'schema_version':'collage-bindings-v1','photos':[{'slot_id':'photo','asset_id':aid,'reason':'test'}],'texts':[]})
    compile_scene(run);render(run)
    return run


def test_rounded_photo_preserves_center_rgb_and_antialiased_corner(tmp_path):
    run=task(tmp_path,'rounded_photo')
    im=Image.open(run/'assets/photos/photo.png')
    assert im.getpixel((0,0))[3]==0
    assert im.getpixel((75,90))==(200,34,17,255)
    a=np.asarray(im)[:,:,3];assert ((a>0)&(a<255)).any()


def test_cover_outline_expands_without_cropping_customer_rgb(tmp_path):
    run=task(tmp_path,style={'outline_width':6,'outline_color':'#FFFFFF'})
    im=Image.open(run/'assets/photos/photo.png');assert im.width>150 and im.height>180
    a=np.asarray(im);assert ((a[:,:,:3]==[200,34,17]).all(axis=2)&(a[:,:,3]==255)).sum()==150*180
    assert ((a[:,:,:3]==255).all(axis=2)&(a[:,:,3]>0)).any()


def test_polaroid_has_solid_wide_bottom_and_rotates_as_one(tmp_path):
    run=task(tmp_path,'polaroid',rotation=23)
    im=Image.open(run/'assets/photos/photo.png');assert im.size==(150,180)
    assert im.getpixel((75,90))==(200,34,17,255)
    assert im.getpixel((75,160))==(245,242,233,255)
    mask=Image.open(run/'assets/photos/photo-content-mask.png')
    assert mask.getpixel((75,160))==0 and mask.getpixel((75,90))==255
    # Reconstruct object-only rotation: every photo pixel stays inside its rotated paper.
    rotated=im.rotate(-23,Image.Resampling.BICUBIC,expand=True)
    content=mask.rotate(-23,Image.Resampling.BICUBIC,expand=True)
    assert np.all(np.asarray(content)<=np.asarray(rotated)[:,:,3])
    assert read(run/'result.json')['photo_visible_fractions']['photo']>.99


def test_paper_cannot_make_fully_hidden_photo_look_visible(tmp_path):
    run=task(tmp_path,'polaroid',style={'card':{'padding':[10,10,40,10]}})
    a=read(run/'analysis.json');a['objects'].append({'id':'cover','kind':'overlay','bbox':[85,70,215,200],'label':'cover','description':'covers only photo window','style':{'shape':'rectangle','fill':'#000000'}});a['layer_order'].append('cover')
    save(run/'analysis.json',a);compile_scene(run);r=render(run)
    assert r['photo_visible_fractions']['photo']==0 and 'photo' in r['incomplete_objects']


def test_shadow_extends_outside_object_without_wrapping_canvas():
    mask=Image.new('L',(100,100));mask.paste(255,(30,30,60,60))
    a=np.asarray(shadow_layer(mask,{'offset':[8,4],'blur':3,'color':'#00000080'}))[:,:,3]
    assert a[50,65]>0 and a[0].max()==0 and a[:,0].max()==0 and a.max()<=128


def test_smooth_curve_has_continuous_tangent_and_dash_gaps():
    points=[(10,20),(65,55),(130,10),(185,100)];p=np.asarray(smooth_points(points))
    i=np.argmin(np.linalg.norm(p-points[1],axis=1));u=p[i]-p[i-1];v=p[i+1]-p[i]
    assert np.dot(u,v)/(np.linalg.norm(u)*np.linalg.norm(v))>.995
    im,_=draw_overlay({'kind':'overlay','style':{'shape':'curve','points':[[.05,.5],[.35,.2],[.65,.8],[.95,.5]],'stroke':'#FFFFFF','stroke_width':2,'dash':[10,10]}},(200,120))
    a=np.asarray(im)[:,:,3];assert a.max()==255 and ((a>0)&(a<255)).any()
    from cv2 import connectedComponents
    assert connectedComponents((a>127).astype('uint8'))[0]>5


@pytest.mark.parametrize('shape',['paper','tape'])
def test_paper_edges_are_straight_unless_torn_requested(shape):
    straight,_=draw_overlay({'kind':'overlay','style':{'shape':shape,'fill':'#FFFFFF'}},(120,160))
    torn,_=draw_overlay({'kind':'overlay','style':{'shape':shape,'fill':'#FFFFFF','edge':'torn'}},(120,160))
    assert np.asarray(straight)[:,:,3].min()==255
    assert (np.asarray(torn)[:,:,3]==0).any()


def test_compact_review_binds_current_version_and_rejects_stale(tmp_path):
    run=task(tmp_path,'rounded_photo');r=read(run/'result.json')
    compact={'schema_version':'collage-review-v1','render_id':r['render_id'],'checked_entire_composition':True,'verdict':'pass','summary':'synthetic check','items':[]}
    save(run/'review.json',compact);assert accept_review(run,run/'review.json')['renders_verified']
    a=read(run/'analysis.json');a['objects'][0]['bbox']=[50,60,200,240];save(run/'analysis.json',a);compile_scene(run);render(run)
    save(run/'review.json',compact)
    with pytest.raises(ValueError,match='stale'):accept_review(run,run/'review.json')


def test_compact_pass_needs_explicit_whole_image_check(tmp_path):
    run=task(tmp_path);r=read(run/'result.json')
    save(run/'review.json',{'schema_version':'collage-review-v1','render_id':r['render_id'],'verdict':'pass','summary':'no look','items':[]})
    with pytest.raises(ValueError):accept_review(run,run/'review.json')


def test_invalid_card_geometry_rejected_before_build(tmp_path):
    run=task(tmp_path);a=read(run/'analysis.json');a['objects'][0]['style']['card']={'padding':[100,100,100,100]}
    with pytest.raises(ValueError,match='window'):analysis_check(a)


def test_solid_frame_fills_corner_joins_and_keeps_window_transparent():
    im,_=draw_overlay({'kind':'overlay','style':{'shape':'frame','stroke':'#FFFFFF','stroke_width':20}},(160,200))
    a=np.asarray(im)[:,:,3]
    for y,x in [(5,5),(5,154),(194,5),(194,154)]:assert a[y,x]==255
    assert a[100,80]==0 and a[10,80]==255


def test_frame_radius_rounds_outer_edge_without_filling_window():
    style={'shape':'frame','stroke':'#FFFFFF80','stroke_width':8,'radius':.2}
    im,_=draw_overlay({'kind':'overlay','style':style},(160,200));a=np.asarray(im)[:,:,3]
    assert a[0,0]==0 and a[3,80]==128 and a[100,80]==0
    assert 0<a.max()<=140 and ((a>0)&(a<120)).any()


@pytest.mark.parametrize('radius',[0,.2])
def test_dashed_frame_preserves_gaps_and_transparent_center(radius):
    im,_=draw_overlay({'kind':'overlay','style':{'shape':'frame','stroke':'#FFFFFF',
        'stroke_width':4,'radius':radius,'dash':[10,10]}},(160,200));a=np.asarray(im)[:,:,3]
    assert a[100,80]==0
    from cv2 import connectedComponents
    assert connectedComponents((a>127).astype('uint8'))[0]>8
