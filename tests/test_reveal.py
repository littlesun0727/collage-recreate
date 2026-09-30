import copy
import io
import shutil
import numpy as np
import pytest
from PIL import Image, ImageDraw
from test_pipeline import task, review
from common import read, save, sha
from scene import compile_scene, load_scene
from render import render, accept_review
from reveal import load_cache, fetch_batch, acquire
from reveal_assets import targets, make_window


def cache_for(run, objects, layers):
    folder=run.parent/'reveal-cache';folder.mkdir()
    shutil.copyfile(run/'prepared/reference.png',folder/'input.png')
    batch=folder/'batch_01';batch.mkdir()
    save(batch/'request_meta.json',{'model':'ecommerce_layer','version':'v1.1','objects':objects})
    save(batch/'query_response.json',{'status':'done','generation_time':40,'output':{'boxes_mapping_index':list(range(len(objects)))}})
    for i,im in enumerate(layers,1):im.save(batch/f'layers_aug_{i:02d}.png')
    return {'enabled':True,'cache':str(folder),'remote':False,'padding':0}


def frame_task(run, integrated=False, angle=0):
    a=read(run/'analysis.json');p=a['objects'][1]
    p.update(mode='cover',bbox=[40,50,160,210],rotation=angle,style={})
    if integrated:
        p.update(bbox=[20,30,180,260],appearance='polaroid')
        save(run/'analysis.json',a);compile_scene(run)
        target=targets(read(run/'scene.json'))[0]
    else:
        target={'id':'frame','kind':'overlay','bbox':[20,30,180,260],'label':'frame','description':'paper frame','photo_id':'photo','method':'extract'}
        a['objects'].append(target);a['layer_order'].append('frame');save(run/'analysis.json',a)
    im=Image.new('RGBA',(200,300));d=ImageDraw.Draw(im)
    d.rectangle((20,30,179,259),fill=(255,255,255,255));d.rectangle((40,50,159,209),fill=(255,0,0,255))
    return cache_for(run,[target],[im])


def test_frame_clears_reference_and_renders_real_customer(task):
    config=frame_task(task)
    save(task/'recovery.json',{'schema_version':'collage-recovery-v1','reference_sha256':read(task/'input.json')['reference']['sha256'],
         'windows':[{'overlay_id':'frame','photo_id':'photo','reason':'Visible old photo in the returned window'}]})
    compile_scene(task,config);r=render(task)
    a=np.asarray(Image.open(task/'final.png'))
    assert a[100,100,2]==180 and a[40,30,:3].tolist()==[255,255,255]
    clean=np.asarray(Image.open(task/'assets/reveal/frame.png'))
    assert clean[100,100,3]==0 and clean[40,30,3]==255
    assert r['customer_photos_verified'] and r['photo_visible_fractions']['photo']>.99
    assert not r['renders_verified']


def test_full_canvas_layer_not_bbox_stretched_or_rotated_twice(task):
    a=read(task/'analysis.json');o={'id':'stamp','kind':'overlay','bbox':[140,10,190,60],'rotation':70,'label':'stamp','description':'asymmetric','method':'extract','style':{'opacity':.2,'shadow':{'blur':5,'offset':[8,8],'color':'#000000'}}}
    a['objects'].append(o);a['layer_order'].append('stamp');save(task/'analysis.json',a)
    im=Image.new('RGBA',(100,150));ImageDraw.Draw(im).rectangle((75,10,89,24),fill=(0,255,0,255))
    config=cache_for(task,[o],[im]);compile_scene(task,config);render(task)
    final=np.asarray(Image.open(task/'final.png'))
    assert final[35,165,:3].tolist()==[0,255,0]
    assert final[10,140,:3].tolist()!=[0,255,0]


def test_integrated_card_derived_without_extra_analysis_object(task):
    config=frame_task(task,True,12);compile_scene(task,config);s=load_scene(task)
    p=next(o for o in s['objects'] if o['id']=='photo')
    assert len(read(task/'analysis.json')['objects'])==2 and len(s['objects'])==3
    assert 'card' not in p['style'] and p['photo_window']
    mask=np.asarray(Image.open(p['photo_window']['file']))
    layer=np.asarray(Image.open(task/'assets/reveal'/f"{p['reveal_frame_id']}.png"))
    # Legacy card conversion still supplies placement, but no longer erases returned pixels.
    assert np.any(layer[:,:,3][mask>128]>0)
    assert s['layer_order'].index(p['reveal_frame_id'])>s['layer_order'].index('photo')
    assert render(task)['customer_photos_verified']


def test_text_suppression_requires_explicit_relation(task):
    a=read(task/'analysis.json');paper={'id':'paper','kind':'overlay','bbox':[0,0,180,30],'label':'paper','description':'caption','method':'extract'}
    text={'id':'caption','kind':'text','bbox':[10,5,100,25],'label':'caption','description':'caption','text':'hello','text_status':'known'}
    a['objects'] += [paper,text];a['layer_order'] += ['paper','caption'];save(task/'analysis.json',a)
    im=Image.new('RGBA',(200,300));d=ImageDraw.Draw(im);d.rectangle((0,0,179,29),fill='white');d.rectangle((15,7,80,18),fill='black')
    config=cache_for(task,[paper],[im]);compile_scene(task,config)
    s=load_scene(task);assert 'caption' in s['reveal_unconfirmed_text']
    assert not s['objects'][-1].get('embedded_owner')
    a['objects'][-1]['embedded_in']='paper';save(task/'analysis.json',a);compile_scene(task)
    assert load_scene(task)['objects'][-1]['embedded_owner']=='paper'
    r=render(task);assert r['coverage_complete']
    assert len(read(task/'result.json')['extraction_sheets'])==1


@pytest.mark.parametrize('file',['frame.png','frame-photo-window.png','index.json'])
def test_changed_recovered_resource_is_rejected(task,file):
    compile_scene(task,frame_task(task));render(task)
    path=task/'assets/reveal'/file;path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError):render(task)


def test_reference_mismatch_rejected(task):
    config=frame_task(task);Image.new('RGB',(200,300),'red').save(task.parent/'reveal-cache/input.png')
    with pytest.raises(ValueError,match='reference'):compile_scene(task,config)


def test_filtered_targets_not_resubmitted(task,monkeypatch):
    config=frame_task(task);qp=task.parent/'reveal-cache/batch_01/query_response.json'
    q=read(qp);q['output']['boxes_mapping_index']=[];save(qp,q);config['remote']=True
    import reveal
    monkeypatch.setattr(reveal,'api_key',lambda _:pytest.fail('Filtered target must not trigger another paid request'))
    compile_scene(task,config);r=render(task)
    assert 'frame' in r['incomplete_objects']
    save(task/'review.json',review(task))
    with pytest.raises(ValueError):accept_review(task,task/'review.json')


def test_uncertain_submit_never_posts_twice(task):
    calls=[]
    def post(*args,**kwargs):calls.append(args);raise TimeoutError('unknown outcome')
    batch=task/'api-test';ref=task/'prepared/reference.png';objects=[{'id':'deco','bbox':[0,0,40,40]}]
    with pytest.raises(TimeoutError):fetch_batch(batch,ref,objects,'fake',post=post)
    with pytest.raises(ValueError,match='unknown'):fetch_batch(batch,ref,objects,'fake',post=post)
    assert len(calls)==1


def test_completed_http_task_download_resume(task):
    calls=[];buf=io.BytesIO();Image.new('RGBA',(100,150),'blue').save(buf,format='PNG')
    class Response:
        content=buf.getvalue()
        def __init__(self,data=None):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    def post(url,**kwargs):
        calls.append(url)
        if url.endswith('submit_task'):return Response({'response_status':0,'task_id':'fake'})
        return Response({'status':'done','output':{'boxes_mapping_index':[0],'layers_aug':['bg','layer']}})
    batch=task/'http';args=(batch,task/'prepared/reference.png',[{'id':'deco','bbox':[0,0,40,40]}],'fake')
    fetch_batch(*args,post=post,get=lambda *a,**k:Response());assert len(calls)==2
    fetch_batch(*args,post=post,get=lambda *a,**k:pytest.fail('Do not redownload verified cache'))
    assert len(calls)==2 and (batch/'layers_aug_01.png').is_file()


def test_sparse_line_over_multiple_photos_is_not_treated_as_photo_backing(task):
    a=read(task/'analysis.json');p=a['objects'][1];p.update(bbox=[10,30,90,260],mode='cover',style={})
    p2=copy.deepcopy(p);p2.update(id='photo_two',bbox=[110,30,190,260]);a['objects'].append(p2);a['layer_order'].append('photo_two')
    line={'id':'route','kind':'overlay','bbox':[0,0,200,300],'label':'route','description':'thin dashed route','method':'extract','style':{'shape':'polyline','points':[[0,0],[1,1]],'stroke':'#000000','stroke_width':1}}
    a['objects'].append(line);a['layer_order'].append('route');save(task/'analysis.json',a)
    b=read(task/'bindings.json');other=copy.deepcopy(b['photos'][0]);other['slot_id']='photo_two';b['photos'].append(other);save(task/'bindings.json',b)
    im=Image.new('RGBA',(200,300));ImageDraw.Draw(im).line([(0,0),(100,200),(199,299)],fill='white',width=1)
    compile_scene(task,cache_for(task,[line],[im]));index=read(task/'assets/reveal/index.json')
    assert index['records'][0]['action']=='reuse_layer'


def test_generating_embedded_owner_as_group_restores_independent_text(task):
    a=read(task/'analysis.json')
    paper={'id':'paper','kind':'overlay','bbox':[0,0,180,30],'label':'paper','description':'paper','method':'extract'}
    extra={'id':'extra','kind':'overlay','bbox':[180,0,200,30],'label':'extra','description':'extra','style':{'shape':'rectangle','fill':'#FFFFFF'}}
    caption={'id':'caption','kind':'text','bbox':[10,5,100,25],'label':'caption','description':'caption','text':'hello','text_status':'known','embedded_in':'paper'}
    a['objects'] += [paper,extra,caption];a['layer_order'] += ['paper','extra','caption'];save(task/'analysis.json',a)
    im=Image.new('RGBA',(200,300));d=ImageDraw.Draw(im);d.rectangle((0,0,179,29),fill='white');d.rectangle((15,7,80,18),fill='black')
    compile_scene(task,cache_for(task,[paper],[im]));s=load_scene(task)
    generated=task/'generated.png';Image.new('RGBA',(200,30),'yellow').save(generated)
    s['generated_groups']=[{'primary':'extra','member_ids':['paper','extra'],'bbox':[0,0,200,30],'source':{'file':str(generated),'sha256':sha(generated),'request':'test'}}]
    save(task/'scene.json',s);r=render(task)
    text_record=next(x for x in read(task/'result.json')['resources'] if x['id']=='caption')
    assert not text_record['metadata'].get('embedded_content') and text_record['quality']=='approximate'
    assert r['coverage_complete']
