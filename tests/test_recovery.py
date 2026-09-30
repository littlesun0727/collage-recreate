import copy
import numpy as np
import pytest
from PIL import Image, ImageDraw
from test_pipeline import task, review
from test_reveal import cache_for, frame_task
from common import read, save, sha
from scene import compile_scene, load_scene
from render import render, accept_review
from recovery import recover, check_plan


def plan_for(task, **kwargs):
    return {'schema_version':'collage-recovery-v1','reference_sha256':read(task/'input.json')['reference']['sha256'],**kwargs}


def test_default_preserves_raw_pixels_and_photo_geometry(task):
    config=frame_task(task);before=read(task/'analysis.json')['objects'][1]
    compile_scene(task,config);scene=load_scene(task);p=scene['objects'][1]
    raw=Image.open(task.parent/'reveal-cache/batch_01/layers_aug_01.png')
    kept=Image.open(task/'assets/reveal/frame.png')
    assert raw.tobytes()==kept.tobytes() and p['bbox']==before['bbox']
    assert p['rotation']==before['rotation']
    result=render(task)
    assert 'photo' in result['incomplete_objects']  # Opaque old photo is NOT claimed as usable.
    save(task/'review.json',review(task))
    with pytest.raises(ValueError):accept_review(task,task/'review.json')


def test_opt_in_clear_is_offline_idempotent_and_does_not_expand_customer(task,monkeypatch):
    import reveal
    config=frame_task(task);compile_scene(task,config);render(task)
    original_photo=copy.deepcopy(load_scene(task)['objects'][1]);first=sha(task/'previews/first.png')
    cache=task.parent/'reveal-cache/batch_01/layers_aug_01.png';raw_hash=sha(cache)
    monkeypatch.setattr(reveal,'fetch_batch',lambda *a,**k:pytest.fail('Recovery must be offline'))
    path=task/'repair-input.json'
    save(path,plan_for(task,windows=[{'overlay_id':'frame','photo_id':'photo','padding':3,'reason':'Observed old reference photo'}]))
    result=recover(task,path);new_photo=load_scene(task)['objects'][1]
    assert new_photo['bbox']==original_photo['bbox'] and new_photo['binding']==original_photo['binding']
    assert sha(new_photo['photo_window']['file'])==original_photo['photo_window']['sha256']
    alpha=np.array(Image.open(task/'assets/reveal/frame.png'))[:,:,3]
    assert alpha[100,100]==0 and alpha[40,30]==255
    assert not result['incomplete_objects'] and sha(cache)==raw_hash and sha(task/'previews/first.png')==first
    assert recover(task,path)['final_sha256']==result['final_sha256']


def fragmented_task(task):
    a=read(task/'analysis.json')
    paper={'id':'paper','kind':'overlay','bbox':[0,0,200,300],'label':'paper','description':'requested backing','method':'extract'}
    outline={'id':'cat','kind':'overlay','bbox':[30,40,130,140],'label':'cat','description':'requested cat','method':'extract'}
    a['objects'] += [paper,outline];a['layer_order']=['background','paper','photo','cat'];save(task/'analysis.json',a)
    donor=Image.new('RGBA',(200,300));d=ImageDraw.Draw(donor)
    d.rectangle((40,50,119,129),fill=(0,255,0,255));d.rectangle((160,230,190,260),fill='red')
    shell=Image.new('RGBA',(200,300));ImageDraw.Draw(shell).rectangle((30,40,129,139),fill='orange')
    config=cache_for(task,[paper,outline],[donor,shell]);compile_scene(task,config);render(task)
    group={'primary':'cat','member_ids':['paper','cat'],'reason':'Cat pixels were returned in the backing layer',
           'parts':[{'source_id':'cat'},{'source_id':'paper','bbox':[40,50,120,130]}]}
    return group


def test_group_restores_misassigned_pixels_at_correct_layer_without_double_draw(task):
    group=fragmented_task(task);first=sha(task/'previews/first.png');path=task/'repair-input.json'
    save(path,plan_for(task,groups=[group]));result=recover(task,path)
    final=Image.open(task/'final.png')
    assert final.getpixel((80,90))==(0,255,0) and final.getpixel((32,42))==(255,165,0)
    assert final.getpixel((175,245))!=(255,0,0)  # Unselected donor contamination removed.
    assert result['coverage_complete'] and result['customer_photos_verified']
    resources=read(task/'result.json')['resources']
    assert next(r for r in resources if r['id']=='paper')['group']=='cat'
    assert len(result['extraction_sheets'])==1 and sha(task/'previews/first.png')==first
    assert recover(task,path)['final_sha256']==result['final_sha256']
    # Removing the optional recovery plan restores the original composition.
    save(path,plan_for(task));assert recover(task,path)['final_sha256']==first


def test_group_donor_tampering_is_rejected(task):
    group=fragmented_task(task);path=task/'repair-input.json';save(path,plan_for(task,groups=[group]));recover(task,path)
    donor=task.parent/'reveal-cache/batch_01/layers_aug_01.png';donor.write_bytes(donor.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='Source changed'):render(task)


def test_omitted_contamination_is_transparent_and_cannot_pass(task):
    config=frame_task(task);compile_scene(task,config);render(task)
    path=task/'repair-input.json';save(path,plan_for(task,omit=[{'id':'frame','reason':'Observed contamination cannot be separated reliably'}]))
    result=recover(task,path)
    assert Image.open(task/'assets/reveal/frame.png').getchannel('A').getextrema()==(0,0)
    assert 'frame' in result['incomplete_objects'] and result['photo_visible_fractions']['photo']>.99
    save(task/'review.json',review(task))
    with pytest.raises(ValueError):accept_review(task,task/'review.json')


def test_large_multiwindow_backing_retained_and_repairs_use_independent_masks(task):
    a=read(task/'analysis.json');p=a['objects'][1];p.update(mode='cover',bbox=[10,30,80,140],parent_id='board',style={})
    p2=copy.deepcopy(p);p2.update(id='photo_two',bbox=[110,160,180,270])
    board={'id':'board','kind':'overlay','bbox':[0,0,200,300],'label':'board','description':'two windows','method':'extract'}
    a['objects'] += [p2,board];a['layer_order'] += ['photo_two','board'];save(task/'analysis.json',a)
    b=read(task/'bindings.json');b['photos'].append({**b['photos'][0],'slot_id':'photo_two'});save(task/'bindings.json',b)
    config=cache_for(task,[board],[Image.new('RGBA',(200,300),'red')]);compile_scene(task,config)
    assert read(task/'assets/reveal/index.json')['records'][0]['action']=='reuse_layer'
    path=task/'repair-input.json';save(path,plan_for(task,windows=[{'overlay_id':'board','photo_id':pid,'reason':'Old photo visible'} for pid in ['photo','photo_two']]))
    recover(task,path);alpha=np.array(Image.open(task/'assets/reveal/board.png'))[:,:,3]
    assert alpha[50,40]==0 and alpha[200,140]==0 and alpha[150,100]==255
    for obj in load_scene(task)['objects']:
        if obj['kind']=='photo':assert obj['bbox']==next(x['bbox'] for x in a['objects'] if x['id']==obj['id'])


@pytest.mark.parametrize('change',[
    lambda p:p.update(reference_sha256='stale'),
    lambda p:p['groups'][0]['parts'][0].update(source_id='photo'),
    lambda p:p['groups'][0]['member_ids'].append('photo'),
    lambda p:p['groups'][0]['parts'][0].update(bbox=[0,0,999,999]),
    lambda p:p['groups'].append(copy.deepcopy(p['groups'][0])),
    lambda p:p['groups'][0]['parts'][0].update(source_id='layers_aug_00'),
])
def test_invalid_recovery_rejected_before_writing(task,change):
    group=fragmented_task(task);plan=plan_for(task,groups=[group]);change(plan)
    path=task/'repair-input.json';save(path,plan)
    with pytest.raises(ValueError):recover(task,path)
    assert not (task/'recovery.json').exists()
