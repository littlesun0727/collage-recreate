import sys
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from review_previews import regions,publish,envelope
from common import read,save,sha
from test_pipeline import task
from test_layout_review import adjustment
from scene import compile_scene,apply_review
from render import render


def test_portrait_decoration_is_prioritized_over_photo_inside_paper():
    scene={'reference_size':[1000,1200],'objects':[
        {'id':'page','kind':'overlay','bbox':[0,0,450,1100]},
        {'id':'card_photo','kind':'photo','bbox':[50,50,400,500]},
        {'id':'portrait','kind':'photo','mode':'cutout','bbox':[500,0,1000,1200]},
        {'id':'decoration','kind':'overlay','bbox':[600,100,900,650]}]}
    assert regions(scene,limit=1)[0]['ids']==['portrait','decoration']


def test_matched_crops_include_rotation_and_ignore_full_page_background(tmp_path):
    scene={'reference_size':[400,500],'objects':[
        {'id':'background_photo','kind':'photo','bbox':[0,0,400,500]},
        {'id':'photo','kind':'photo','bbox':[80,100,220,350]},
        {'id':'frame','kind':'overlay','bbox':[220,160,300,320],'rotation':45}]}
    assert envelope(scene['objects'][2])[0]<220
    selected=regions(scene);assert len(selected)==1 and selected[0]['ids']==['photo','frame']
    reference=Image.new('RGB',(400,500),'red');final=Image.new('RGB',(800,1000),'blue')
    result=publish(tmp_path,scene,reference,final);im=Image.open(result[0]['file'])
    assert im.getpixel((im.width//4,im.height//2))==(255,0,0)
    assert im.getpixel((im.width*3//4,im.height//2))==(0,0,255)
    assert result[0]['bbox']==selected[0]['bbox']


def test_detail_previews_refresh_after_apply_without_changing_first(task):
    a=read(task/'analysis.json');a['objects'].append({'id':'cover','kind':'overlay',
        'bbox':[60,90,130,160],'label':'cover','description':'test','style':{'shape':'rectangle','fill':'#FFFFFF'}})
    a['layer_order'].append('cover');save(task/'analysis.json',a)
    compile_scene(task);before=render(task);detail=before['review_regions'][0]['file'];old=sha(detail)
    first=sha(task/'previews/first.png')
    apply_review(task,adjustment(task,order=['background','cover','photo']));after=render(task)
    assert after['review_regions'][0]['bbox']==before['review_regions'][0]['bbox']
    assert sha(detail)!=old and sha(task/'previews/first.png')==first
    assert after['render_id']!=before['render_id']
