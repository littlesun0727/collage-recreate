"""Customer edits must reach rendering and preserve the previous version."""
from pathlib import Path
import pytest
from PIL import Image
from test_workbench import run, build, review, document
from test_chat import plan, checked, payload
from common import read, save, sha
from scene import apply_review, load_scene
from revision import prepare_revision, commit_revision
from workbench.server.chat import ChatQueue


def removal(run, *ids):
    p=plan(run)
    p['items']=[{'id':oid,'action':'remove','reason':'Customer requested deletion'} for oid in ids]
    return p


def test_delete_photo_and_linked_frame_preserves_history(run):
    a=read(run/'analysis.json')
    a['objects'][-1]['photo_id']='photo'
    save(run/'analysis.json',a)
    build(run);review(run)
    before=sha(run/'final.png');snapshot=next((run/'observability/versions').glob('*/final.png'))
    p=removal(run,'photo','star');p['layer_order']=['background']
    candidate=Path(prepare_revision(run,'remove-photo-frame',p)['candidate'])
    assert sha(run/'final.png')==before
    assert [o['id'] for o in load_scene(candidate)['objects']]==['background']
    commit_revision(run,'remove-photo-frame',checked(candidate))
    assert sha(snapshot)==before and sha(run/'final.png')!=before
    changes=document(run)[1]['versions'][-1]['changes']
    assert {c['id'] for c in changes if 'removed' in c['fields']}=={'photo','star'}


def test_background_swap_and_remove_overlay_through_chat_queue(run,tmp_path):
    build(run);review(run);store,doc=document(run)
    asset=read(run/'prepared/catalog.json')['assets'][0]
    before=sha(run/'final.png')
    def agent(mode,root,context,images):
        if mode=='plan':
            return {'decision':'edit','summary':'Replace background and remove overlay','question':'',
                'changes':[{'id':'background','reason':'Selected customer background',
                    'changes_json':'{"crop_center":[0.5,0.5],"asset_id":"'+asset['id']+'"}'}],
                'remove_ids':['star'],'layer_order':[]}
        assert not any(o['id']=='star' for o in context['actual_scene']['objects'])
        return {'verdict':'pass','summary':'Verified','issues':[]}
    q=ChatQueue(store,tmp_path/'queue.sqlite',agent=agent,start=False)
    try:
        job=q.submit(doc['id'],payload(doc));q.process(job)
        assert q.rows()[0]['status']=='completed',q.rows()[0]
    finally:q.close()
    s=load_scene(run);bg=next(o for o in s['objects'] if o['id']=='background')
    assert bg['kind']=='photo' and bg['customer_background']
    assert bg['binding']['crop_center']==[.5,.5] and bg['source']['sha256']==asset['sha256']
    assert s['layer_order']==['background','photo']
    assert read(run/'result.json')['customer_photos_verified']
    assert sha(run/'final.png')!=before
    assert Image.open(run/'final.png').convert('RGB').getpixel((0,0))==(174,206,173)


@pytest.mark.parametrize('ids,order',[
    (['missing'],None),(['star','star'],None),(['background','photo','star'],None),
    (['star'],['background','photo','star']),
])
def test_invalid_removal_is_atomic(run,ids,order):
    build(run);before=sha(run/'scene.json');p=removal(run,*ids)
    if order is not None:p['layer_order']=order
    save(run/'bad.json',p)
    with pytest.raises(ValueError):apply_review(run,run/'bad.json')
    assert sha(run/'scene.json')==before


@pytest.mark.parametrize('relationship',['embedded_owner','recovery_owner','generated_groups'])
def test_cannot_delete_part_of_merged_pixels(run,relationship):
    build(run);s=read(run/'scene.json')
    if relationship=='generated_groups':s[relationship]=[{'member_ids':['star','background']}]
    else:s['objects'][-1][relationship]='background'
    save(run/'scene.json',s);before=sha(run/'scene.json')
    p=removal(run,'star');p['scene_sha256']=before;save(run/'bad.json',p)
    with pytest.raises(ValueError,match='complete'):apply_review(run,run/'bad.json')
    assert sha(run/'scene.json')==before


def test_remove_photo_alone_clears_frame_link(run):
    a=read(run/'analysis.json');a['objects'][-1]['photo_id']='photo';save(run/'analysis.json',a)
    build(run)
    candidate=Path(prepare_revision(run,'photo-only',removal(run,'photo'))['candidate'])
    s=load_scene(candidate)
    assert [o['id'] for o in s['objects']]==['background','star']
    assert 'photo_id' not in s['objects'][-1]


def test_invalid_background_source_does_not_delete_overlay(run):
    build(run);before=sha(run/'scene.json');p=removal(run,'star')
    p['items'].append({'id':'background','action':'adjust','reason':'Replace',
                       'changes':{'asset_id':'missing'}})
    save(run/'bad.json',p)
    with pytest.raises(ValueError,match='Unknown customer asset'):apply_review(run,run/'bad.json')
    assert sha(run/'scene.json')==before


def test_revision_promotes_new_json_asset_with_matching_hash(run):
    build(run);review(run)
    candidate=Path(prepare_revision(run,'new-asset-gate',removal(run,'star'))['candidate'])
    from asset_gate import publish
    from render import render
    scene=load_scene(candidate)
    # A newly extracted layer produces a new gate report with candidate-local paths.
    image=candidate/'assets/proof.png';Image.new('RGBA',(8,8),'red').save(image)
    record={'id':'inspection','status':'accepted','basis':'visual','input_key':'a'*64,
            'issues':[],'warnings':[],
            'candidate':{'file':str(image),'sha256':sha(image)}}
    publish(candidate,scene,[record]);save(candidate/'scene.json',scene)
    render(candidate,publish_result=False)
    metadata=read(candidate.parent/'candidate.json')
    metadata.update(scene_sha256=sha(candidate/'scene.json'),final_sha256=sha(candidate/'final.png'))
    save(candidate.parent/'candidate.json',metadata)
    original_hash=sha(candidate/'assets/gate/report.json')
    commit_revision(run,'new-asset-gate',checked(candidate))
    live=load_scene(run)
    assert live['asset_gate']['sha256']==sha(run/'assets/gate/report.json')
    assert live['asset_gate']['sha256']!=original_hash
    assert sha(candidate/'assets/gate/report.json')==original_hash
    assert read(run/'assets/gate/report.json')['records'][0]['candidate']['file']==str(run/'assets/proof.png')


def test_promotion_does_not_repair_a_tampered_json_dependency(tmp_path):
    from revision import stage_promotion
    candidate=tmp_path/'candidate';live=tmp_path/'live'
    report=candidate/'assets/report.json';save(report,{'file':str(candidate/'image.png')})
    scene=candidate/'scene.json';save(scene,{'asset_gate':{'file':str(report),'sha256':sha(report)}})
    save(report,{'tampered':True})
    with pytest.raises(ValueError,match='changed before promotion'):
        stage_promotion([report,scene],candidate,live,tmp_path/'staging')
    assert not live.exists()


def test_promotion_keeps_frozen_historical_hashes(tmp_path):
    from revision import stage_promotion
    candidate=tmp_path/'candidate';live=tmp_path/'live'
    report=candidate/'assets/report.json';save(report,{'status':'current'})
    first=candidate/'previews/first-scene.json'
    historical={'file':str(live/'assets/report.json'),'sha256':'b'*64}
    save(first,{'asset_gate':historical})
    staged=stage_promotion([report,first],candidate,live,tmp_path/'staging')
    assert read(staged[first])['asset_gate']==historical
