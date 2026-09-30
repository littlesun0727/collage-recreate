import copy
import io
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numpy as np
import pytest
from PIL import Image,ImageDraw
from common import read,save
from reveal_plan import plan
from reveal_grouped import restore,acquire


def scene(n=4):
    objects=[]
    for i in range(n):
        x=50+(i%4)*900;y=50+(i//4)*900
        objects += [{'id':f'f{i}','kind':'overlay','bbox':[x,y,x+180,y+240],'photo_id':f'p{i}'},
                    {'id':f'p{i}','kind':'photo','bbox':[x+20,y+20,x+160,y+180]}]
    return {'reference_size':[4000,4000],'objects':objects}


def targets(s):return [o for o in s['objects'] if o['kind']=='overlay']


def test_spatial_groups_and_exact_photo_boxes():
    s=scene();p=plan(s,targets(s),mode='grouped');assert 1<=len(p['batches'])<=3
    for b in p['batches']:
        assert b['count']<=20 and b['estimated_sampling_gain']>2
        photo=next(o for o in b['objects'] if o['role']=='photo_context')
        assert photo['reference_bbox']==photo['original_bbox'] and photo['padding']==0
        for o in b['objects']:
            c=b['crop_box'];assert o['bbox']==[o['reference_bbox'][0]-c[0],o['reference_bbox'][1]-c[1],o['reference_bbox'][2]-c[0],o['reference_bbox'][3]-c[1]]
    assert plan(s,list(reversed(targets(s))),mode='grouped')==p


def test_nearby_merge_without_distant_chain():
    s=scene(3)
    for o in s['objects'][2:4]:o['bbox']=[v-680 if j%2==0 else v for j,v in enumerate(o['bbox'])]
    p=plan(s,targets(s),mode='grouped');assert sorted(b['count'] for b in p['batches'])==[2,4]


def test_atomic_above_ten_and_over_twenty_blocked():
    s=scene(11);carrier={'id':'carrier','kind':'overlay','bbox':[0,0,3900,3900]}
    ps=[o for o in s['objects'] if o['kind']=='photo']
    for o in ps:o['parent_id']='carrier'
    s['objects']=[carrier]+ps;p=plan(s,[carrier]);assert p['batches'][0]['count']==12
    for i in range(10):s['objects'].append({**ps[0],'id':f'extra{i}'})
    p=plan(s,[carrier]);assert not p['batches'] and p['blocked'][0]['count']==22


def test_global_overlay_does_not_swallow_small_groups():
    s=scene();big={'id':'big','kind':'overlay','bbox':[0,0,4000,4000]};s['objects'].append(big)
    p=plan(s,targets(s),mode='grouped');assert len(p['batches'])<=3
    b=next(b for b in p['batches'] if 'big' in b['primary_ids'])
    assert b['crop_box']==[0,0,4000,4000] and b['count']==5
    assert all(b['count']<=20 for b in p['batches'])


def test_full_control_has_context_and_full_canvas():
    s=scene(16);p=plan(s,targets(s),mode='full')
    assert len(p['batches'])<=3
    assert all(b['count']<=20 and b['crop_box']==[0,0,4000,4000] for b in p['batches'])
    assert sum(len(b['primary_ids']) for b in p['batches'])==16


def test_restore_preserves_offset_alpha_and_scale(tmp_path):
    raw=tmp_path/'raw.png';dest=tmp_path/'out.png';im=Image.new('RGBA',(100,50))
    ImageDraw.Draw(im).rectangle((10,10,29,29),fill='red');im.save(raw)
    assert restore(raw,[200,300,400,400],[600,700],dest)==[2,2]
    a=np.asarray(Image.open(dest));assert a.shape==(700,600,4)
    assert a[340,240].tolist()==[255,0,0,255] and a[0,0,3]==0 and a[600,240,3]==0
    with pytest.raises(ValueError,match='aspect'):restore(raw,[0,0,100,100],[600,700],dest)


def test_restore_service_dimension_rounding_requires_coordinate_evidence(tmp_path):
    raw=tmp_path/'rounded.png';dest=tmp_path/'out.png'
    im=Image.new('RGBA',(864,576));ImageDraw.Draw(im).rectangle((100,100,200,200),fill='red');im.save(raw)
    crop=[111,55,975,617];original=[[40,142,170,293],[366,430,453,485]]
    resized=[[x if i%2==0 else x*576/562 for i,x in enumerate(box)] for box in original]
    with pytest.raises(ValueError,match='aspect'):restore(raw,crop,[1080,1442],dest)
    scale=restore(raw,crop,[1080,1442],dest,(original,resized))
    assert scale==[1,562/576]
    with Image.open(dest) as out:
        assert out.getpixel((261,201))==(255,0,0,255) and out.getpixel((0,0))[3]==0
    wrong=copy.deepcopy(resized);wrong[0][1]+=10
    for invalid in [(original,wrong),(original,resized[:1]),([],[]),(original,None)]:
        with pytest.raises(ValueError,match='aspect'):restore(raw,crop,[1080,1442],dest,invalid)


@pytest.mark.parametrize('layout',['grouped','full',None])
def test_request_resume_identity_and_aux_exclusion(tmp_path,monkeypatch,layout):
    import reveal
    s=scene(1);ref=tmp_path/'reference.png';Image.new('RGB',(4000,4000),'white').save(ref);s['reference']={'file':str(ref)}
    run=tmp_path/'run';calls=[]
    def fake(task,reference,objects,key,timeout):
        calls.append(str(task));task=Path(task)
        if layout!='grouped':
            with Image.open(reference) as uploaded,Image.open(ref) as original:
                assert uploaded.size==original.size and uploaded.tobytes()==original.tobytes()
            context=next(o for o in objects if o['role']=='photo_context')
            assert context['bbox']==s['objects'][1]['bbox']
        from common import sha
        save(task/'request_meta.json',{'reference_sha256':sha(reference),'objects':objects})
        save(task/'query_response.json',{'status':'done','task_id':'fake','output':{'boxes_mapping_index':list(range(len(objects)))}})
        save(task/'submit_response.json',{'task_id':'fake'})
        with Image.open(reference) as im:size=im.size
        for i in range(len(objects)):Image.new('RGBA',size,'red').save(task/f'layers_aug_{i+1:02d}.png')
    monkeypatch.setattr(reveal,'api_key',lambda _:'fake');monkeypatch.setattr(reveal,'fetch_batch',fake)
    cfg={'remote':True}
    if layout is not None:cfg['layout']=layout
    assets,r=acquire(run,s,targets(s),cfg);assert set(assets)=={'f0'} and len(calls)==1 and not r[0].get('error')
    assets,r=acquire(run,s,targets(s),cfg);assert len(calls)==1
    # Same overlay bbox but changed photo context MUST use a different batch.
    s['objects'][1]['bbox'][0]+=3
    acquire(run,s,targets(s),cfg);assert len(calls)==2


def test_offline_no_network(tmp_path,monkeypatch):
    import reveal
    s=scene(1);ref=tmp_path/'r.png';Image.new('RGB',(4000,4000)).save(ref);s['reference']={'file':str(ref)}
    monkeypatch.setattr(reveal,'api_key',lambda _:pytest.fail('offline request'))
    assets,r=acquire(tmp_path/'r',s,targets(s),{'layout':'grouped','remote':False})
    assert not assets and r[0]['error']


def test_filtered_completed_group_not_paid_again(tmp_path,monkeypatch):
    import reveal
    from common import sha
    s=scene(1);ref=tmp_path/'r.png';Image.new('RGB',(4000,4000)).save(ref);s['reference']={'file':str(ref)}
    calls=[]
    def filtered(task,reference,objects,key,timeout):
        calls.append(1);task=Path(task)
        save(task/'request_meta.json',{'reference_sha256':sha(reference),'objects':objects})
        save(task/'query_response.json',{'status':'done','task_id':'filtered','output':{'boxes_mapping_index':[]}})
    monkeypatch.setattr(reveal,'api_key',lambda _:'fake');monkeypatch.setattr(reveal,'fetch_batch',filtered)
    for _ in range(2):
        assets,receipts=acquire(tmp_path/'run',s,targets(s),{'layout':'grouped','remote':True})
        assert not assets and receipts[0]['missing_ids']==['f0']
    assert len(calls)==1


def test_unknown_submission_not_resent_by_group_adapter(tmp_path,monkeypatch):
    import reveal
    s=scene(1);ref=tmp_path/'r.png';Image.new('RGB',(4000,4000)).save(ref);s['reference']={'file':str(ref)}
    calls=[]
    def fail(*args,**kwargs):calls.append(1);raise TimeoutError('transport interrupted')
    monkeypatch.setattr(reveal,'api_key',lambda _:'fake');monkeypatch.setattr(reveal,'http',fail)
    for _ in range(2):
        assets,receipts=acquire(tmp_path/'run',s,targets(s),{'layout':'grouped','remote':True})
        assert not assets and receipts[0]['error']
    assert len(calls)==1


def test_crop_at_canvas_edge_never_cuts_requested_bounds():
    s={'reference_size':[3000,5000],'objects':[{'id':'edge','kind':'overlay','bbox':[0,0,80,150]}]}
    b=plan(s,targets(s),mode='grouped')['batches'][0]
    assert b['crop_box'][:2]==[0,0]
    assert b['objects'][0]['bbox']==[0,0,88,165]
    assert b['crop_box'][2]>=88 and b['crop_box'][3]>=165


def test_over_sixty_unique_targets_blocks_all_requests():
    s={'reference_size':[4000,4000],'objects':[{'id':f'o{i}','kind':'overlay','bbox':[i*20,50,i*20+10,70]} for i in range(61)]}
    p=plan(s,targets(s));assert p['status']=='blocked' and p['batches']==[]


def test_four_indivisible_eleven_box_groups_cannot_fit_three():
    objects=[]
    for i in range(4):
        x=i*900;oid=f'carrier{i}'
        objects.append({'id':oid,'kind':'overlay','bbox':[x,0,x+500,500]})
        for j in range(10):objects.append({'id':f'p{i}_{j}','kind':'photo','parent_id':oid,'bbox':[x+10+j*20,20,x+25+j*20,100]})
    s={'reference_size':[4000,4000],'objects':objects};p=plan(s,targets(s))
    assert p['blocked'] and not p['batches']


def test_submission_budget_is_shared_and_persistent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from reveal import reserve_submission
    root=tmp_path/'reveal'
    def reserve(i):
        try:reserve_submission(root/f'batch_{i}',root);return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(reserve,range(5)))
    assert sum(results)==3
    existing=list(root.glob('*/budget-reserved.json'))
    for f in existing:reserve_submission(f.parent,root)
    with pytest.raises(ValueError,match='3'):reserve_submission(root/'fourth',root)


def test_small_reference_eleven_targets_stay_together():
    s={'reference_size':[900,900],'objects':[{'id':f'o{i}','kind':'overlay','bbox':[20+i*40,20,50+i*40,50]} for i in range(11)]}
    p=plan(s,targets(s));assert len(p['batches'])==1 and p['batches'][0]['count']==11
