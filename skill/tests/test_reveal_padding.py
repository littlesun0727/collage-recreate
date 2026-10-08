import copy
import pytest
from PIL import Image,ImageDraw
from test_pipeline import task
from test_reveal import cache_for,frame_task
from common import read,save,sha
from scene import compile_scene
from render import render
from reveal import request_object,fetch_batch,acquire,load_cache,matches_request


@pytest.mark.parametrize('box,size,want',[
    ([100,200,300,500],[1000,1000],[94,194,306,506]),
    ([0,0,100,100],[100,100],[0,0,100,100]),
    ([1,1,2,2],[200,300],[0,0,3,3]),
    ([3,4,16,27],[200,300],[1,2,18,29]),
    ([0,0,3,2000],[3,2000],[0,0,3,2000]),
])
def test_expansion_clamps_rounds_outwards_and_retains_original(box,size,want):
    target={'id':'x','bbox':box.copy()};obj=request_object(target,size)
    assert obj['bbox']==want and obj['original_bbox']==box and target['bbox']==box


@pytest.mark.parametrize('padding',[-.1,1.1,float('nan'),float('inf')])
def test_invalid_padding_rejected(padding):
    with pytest.raises(ValueError):request_object({'id':'x','bbox':[10,10,20,20]},[100,100],padding)


def test_capped_padding_uses_original_short_edge_full_reference_and_equal_edges():
    target={'id':'x','bbox':[100,200,900,300]}
    obj=request_object(target,[4000,2000])
    # The short-side branch binds first: .1 * 100 = 10.
    assert obj['bbox']==[90,190,910,310]
    assert obj['padding_mode']=='capped'
    # A previously expanded result is never used as the source geometry.
    assert request_object(target,[4000,2000])==obj


@pytest.mark.parametrize('size,want',[
    ([1024,768],[94,94,506,406]),
    ([2048,1024],[88,88,512,412]),
    ([4096,2048],[76,76,524,424]),
])
def test_capped_padding_scales_six_pixel_design_limit_with_reference(size,want):
    target={'id':'large','bbox':[100,100,500,400]}
    assert request_object(target,size)['bbox']==want


def test_api_minimum_can_override_policy_for_tiny_boxes_without_mutating_original():
    target={'id':'tiny','bbox':[100,100,101,101]}
    obj=request_object(target,[4000,2000])
    assert obj['bbox']==[84,84,116,116]
    assert obj['original_bbox']==[100,100,101,101]


def test_ratio_mode_preserves_historical_object_and_cache_identity():
    target={'id':'x','bbox':[100,200,300,500]}
    obj=request_object(target,[1000,1000],padding_mode='ratio')
    assert obj=={'id':'x','bbox':[80,170,320,530],
                 'original_bbox':[100,200,300,500],'padding':.1}
    assert matches_request(obj,request_object(target,[1000,1000],padding_mode='ratio'))
    assert not matches_request(obj,request_object(target,[1000,1000]))


def test_historical_saved_config_without_padding_mode_falls_back_to_ratio(task):
    a=read(task/'analysis.json')
    a['objects'].append({'id':'historical','kind':'overlay','bbox':[50,40,150,140],
                         'label':'historical','description':'historical extraction',
                         'method':'extract'})
    a['layer_order'].append('historical');save(task/'analysis.json',a)
    save(task/'reveal-config.json',{'enabled':True,'remote':False,'cache':None,
                                    'padding':.1,'layout':'legacy'})
    compile_scene(task)
    request=read(task/'assets/reveal/request-targets.json')[0]
    assert request=={'id':'historical','bbox':[40,30,160,150],
                     'original_bbox':[50,40,150,140],'padding':.1}
    assert 'padding_mode' not in read(task/'reveal-config.json')


def padded_asset(task):
    a=read(task/'analysis.json');o={'id':'sticker','kind':'overlay','bbox':[50,40,150,140],'label':'sticker','description':'irregular edge','method':'extract'}
    a['objects'].append(o);a['layer_order'].append('sticker');save(task/'analysis.json',a)
    request=request_object(o,[200,300]);im=Image.new('RGBA',(200,300));ImageDraw.Draw(im).rectangle((42,55,148,135),fill=(0,255,0,255))
    config=cache_for(task,[request],[im]);config['padding']=.1;config['padding_mode']='capped'
    return config,o


def test_expanded_edge_survives_composition_and_preview(task):
    config,obj=padded_asset(task);original=sha(task/'analysis.json');compile_scene(task,config);r=render(task)
    assert sha(task/'analysis.json')==original and not r['incomplete_objects']
    assert Image.open(task/'final.png').getpixel((44,70))[:3]==(0,255,0)
    assert Image.open(task/'assets/reveal/sticker-preview.png').size==(112,112)
    record=read(task/'assets/reveal/index.json')['records'][0]
    assert record['input_bbox']==obj['bbox'] and record['request_bbox']==[44,34,156,146]


def test_legacy_small_cache_not_used_with_default_padding(task):
    config=frame_task(task);config['padding']=.1;config['padding_mode']='capped';compile_scene(task,config)
    assert read(task/'assets/reveal/index.json')['records'][0]['action']=='local_fallback'


def test_photo_window_not_expanded_with_request(task):
    config=frame_task(task);compile_scene(task,config)
    window=read(task/'assets/reveal/index.json')['records'][0]['windows'][0]['window_bbox']
    meta=task.parent/'reveal-cache/batch_01/request_meta.json';m=read(meta)
    m['objects']=[request_object(m['objects'][0],[200,300])];save(meta,m)
    config['padding']=.1;config['padding_mode']='capped'
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
    assert calls[0]['image_boxes']==[[44,34,156,146]]
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
    for padding in [.1,.1,.2,.2]:
        acquire(task,scene,[target],{'remote':True,'padding':padding,'padding_mode':'capped'})
    assert len(calls)==2
    assert calls[0][0]['bbox']==calls[1][0]['bbox']
    assert calls[0][0]['padding']!=calls[1][0]['padding']


def test_same_id_new_geometry_selects_correct_cached_layer(task):
    config,obj=padded_asset(task);root=task.parent/'reveal-cache';old=read(root/'batch_01/request_meta.json')
    new=root/'batch_02';new.mkdir();new_obj=request_object(obj,[200,300],.2)
    save(new/'request_meta.json',{**old,'objects':[new_obj]});save(new/'query_response.json',read(root/'batch_01/query_response.json'))
    Image.new('RGBA',(200,300),'red').save(new/'layers_aug_01.png')
    found,_=load_cache(root,task/'prepared/reference.png',{'sticker':new_obj})
    assert found['sticker']['padding']==.2 and 'batch_02' in found['sticker']['file']
