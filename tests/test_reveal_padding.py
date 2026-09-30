import copy
import pytest
from PIL import Image,ImageDraw
from test_pipeline import task
from test_reveal import cache_for,frame_task
from common import read,save,sha
from scene import compile_scene
from render import render
from reveal import request_object,fetch_batch,acquire,load_cache


@pytest.mark.parametrize('box,size,want',[
    ([100,200,300,500],[1000,1000],[80,170,320,530]),
    ([0,0,100,100],[100,100],[0,0,100,100]),
    ([1,1,2,2],[200,300],[0,0,3,3]),
    ([3,4,16,27],[200,300],[1,1,18,30]),
    ([0,0,3,2000],[3,2000],[0,0,3,2000]),
])
def test_expansion_clamps_rounds_outwards_and_retains_original(box,size,want):
    target={'id':'x','bbox':box.copy()};obj=request_object(target,size)
    assert obj['bbox']==want and obj['original_bbox']==box and target['bbox']==box


@pytest.mark.parametrize('padding',[-.1,1.1,float('nan'),float('inf')])
def test_invalid_padding_rejected(padding):
    with pytest.raises(ValueError):request_object({'id':'x','bbox':[10,10,20,20]},[100,100],padding)


def padded_asset(task):
    a=read(task/'analysis.json');o={'id':'sticker','kind':'overlay','bbox':[50,40,150,140],'label':'sticker','description':'irregular edge','method':'extract'}
    a['objects'].append(o);a['layer_order'].append('sticker');save(task/'analysis.json',a)
    request=request_object(o,[200,300]);im=Image.new('RGBA',(200,300));ImageDraw.Draw(im).rectangle((42,55,148,135),fill=(0,255,0,255))
    config=cache_for(task,[request],[im]);config['padding']=.1
    return config,o


def test_expanded_edge_survives_composition_and_preview(task):
    config,obj=padded_asset(task);original=sha(task/'analysis.json');compile_scene(task,config);r=render(task)
    assert sha(task/'analysis.json')==original and not r['incomplete_objects']
    assert Image.open(task/'final.png').getpixel((44,70))[:3]==(0,255,0)
    assert Image.open(task/'assets/reveal/sticker-preview.png').size==(120,120)
    record=read(task/'assets/reveal/index.json')['records'][0]
    assert record['input_bbox']==obj['bbox'] and record['request_bbox']==[40,30,160,150]


def test_legacy_small_cache_not_used_with_default_padding(task):
    config=frame_task(task);config['padding']=.1;compile_scene(task,config)
    assert read(task/'assets/reveal/index.json')['records'][0]['action']=='local_fallback'


def test_photo_window_not_expanded_with_request(task):
    config=frame_task(task);compile_scene(task,config)
    window=read(task/'assets/reveal/index.json')['records'][0]['windows'][0]['window_bbox']
    meta=task.parent/'reveal-cache/batch_01/request_meta.json';m=read(meta)
    m['objects']=[request_object(m['objects'][0],[200,300])];save(meta,m);config['padding']=.1
    compile_scene(task,config)
    assert read(task/'assets/reveal/index.json')['records'][0]['windows'][0]['window_bbox']==window==[40,50,160,210]


def test_request_body_uses_expanded_box_and_preserves_metadata(task):
    calls=[]
    class Response:
        def __init__(self,data):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    def post(url,**kwargs):
        calls.append(kwargs['json'])
        return Response({'response_status':0,'task_id':'fake'} if url.endswith('submit_task') else {'status':'done','output':{'boxes_mapping_index':[]}})
    obj=request_object({'id':'x','bbox':[50,40,150,140]},[200,300])
    fetch_batch(task/'api',task/'prepared/reference.png',[obj],'fake',post=post)
    assert calls[0]['image_boxes']==[[40,30,160,150]]
    assert read(task/'api/request_meta.json')['objects'][0]['original_bbox']==[50,40,150,140]


def test_paid_cache_is_geometry_specific_and_filtered_targets_not_retried(task,monkeypatch):
    import reveal
    compile_scene(task);scene=read(task/'scene.json');calls=[]
    target={'id':'deco','bbox':[50,40,150,140],'request':True}
    monkeypatch.setattr(reveal,'api_key',lambda _: 'fake')
    def fake_fetch(batch,reference,objects,*args):
        batch.mkdir(parents=True,exist_ok=True);calls.append(copy.deepcopy(objects))
        save(batch/'request_meta.json',{'objects':objects,'model':'ecommerce_layer','version':'v1.1'})
        save(batch/'query_response.json',{'status':'done','output':{'boxes_mapping_index':[]}})
    monkeypatch.setattr(reveal,'fetch_batch',fake_fetch)
    for padding in [.1,.1,.2,.2]:acquire(task,scene,[target],{'remote':True,'padding':padding})
    assert len(calls)==2 and calls[0][0]['bbox']!=calls[1][0]['bbox']


def test_same_id_new_geometry_selects_correct_cached_layer(task):
    config,obj=padded_asset(task);root=task.parent/'reveal-cache';old=read(root/'batch_01/request_meta.json')
    new=root/'batch_02';new.mkdir();new_obj=request_object(obj,[200,300],.2)
    save(new/'request_meta.json',{**old,'objects':[new_obj]});save(new/'query_response.json',read(root/'batch_01/query_response.json'))
    Image.new('RGBA',(200,300),'red').save(new/'layers_aug_01.png')
    found,_=load_cache(root,task/'prepared/reference.png',{'sticker':new_obj})
    assert found['sticker']['padding']==.2 and 'batch_02' in found['sticker']['file']
