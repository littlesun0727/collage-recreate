import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save
from reveal_plan import plan
from test_reveal_grouped import scene,targets
from test_pipeline import task


@pytest.mark.parametrize('count',[1,6,10])
def test_default_full_keeps_up_to_twenty_actual_boxes_together(count):
    s=scene(count);p=plan(s,targets(s))
    assert p['status']=='ready' and p['mode']=='full'
    assert len(p['batches'])==1
    b=p['batches'][0]
    assert b['count']==count*2 and b['crop_box']==[0,0,4000,4000]
    assert b['estimated_sampling_gain']==1
    assert all(o['bbox']==o['reference_bbox'] for o in b['objects'])


@pytest.mark.parametrize('count,requests',[(11,2),(20,2),(21,3)])
def test_capacity_batches_count_context_and_keep_each_frame_with_its_photo(count,requests):
    s=scene(count);s['reference_size']=[6000,6000]
    p=plan(s,targets(s));assert p['status']=='ready'
    assert len(p['batches'])==requests
    seen=[]
    for b in p['batches']:
        assert b['count']<=20 and b['crop_box']==[0,0,6000,6000]
        seen+=b['primary_ids']
        photos={o['source_id']:o for o in b['objects'] if o['role']=='photo_context'}
        for oid in b['primary_ids']:
            frame=next(o for o in s['objects'] if o['id']==oid)
            photo=next(o for o in s['objects'] if o['id']==frame['photo_id'])
            assert photos[photo['id']]['bbox']==photo['bbox']
            assert photos[photo['id']]['padding']==0
    assert sorted(seen)==sorted(o['id'] for o in targets(s))


def test_shared_photo_context_deduplicates_before_twenty_box_threshold():
    s=scene(19)
    s['objects']=[o for o in s['objects'] if o['kind']=='overlay' or o['id']=='p0']
    for o in targets(s):o['photo_id']='p0'
    s['reference_size']=[6000,6000]
    p=plan(s,targets(s));assert len(p['batches'])==1
    assert p['batches'][0]['count']==20
    assert sum(o['role']=='photo_context' for o in p['batches'][0]['objects'])==1


def test_full_preserves_parent_relations_for_multiwindow_frames_and_embedded_assets():
    s=scene(11);s['reference_size']=[6000,6000]
    # A second window is linked through parent_id, and a fixed ornament belongs
    # to the same frame. This entire unit must stay together when splitting.
    s['objects'] += [
        {'id':'extra_photo','kind':'photo','parent_id':'f0','bbox':[70,230,210,280]},
        {'id':'ornament','kind':'overlay','embedded_in':'f0','bbox':[30,30,90,90]}]
    p=plan(s,targets(s));assert p['status']=='ready' and len(p['batches'])==2
    b=next(b for b in p['batches'] if 'f0' in b['primary_ids'])
    assert 'ornament' in b['primary_ids']
    assert {'p0','extra_photo'} <= {o['source_id'] for o in b['objects'] if o['role']=='photo_context'}


@pytest.mark.parametrize('layout',[None,'full','grouped','legacy'])
def test_build_cli_default_and_explicit_layout_are_saved(task,monkeypatch,layout):
    import reveal
    import workflow
    # No complex targets: exercise the real CLI/configuration/compile path offline.
    monkeypatch.setattr(reveal,'api_key',lambda *_:pytest.fail('Unexpected API request'))
    args=['workflow.py','build','--run',str(task),'--reveal']
    if layout:args+=['--reveal-layout',layout]
    monkeypatch.setattr(sys,'argv',args)
    assert workflow.main()==0
    expected=layout or 'grouped'
    assert read(task/'reveal-config.json')['layout']==expected
    assert read(task/'reveal-config.json')['padding_mode']=='capped'
    # An existing run with no new extraction flags keeps its saved mode.
    monkeypatch.setattr(sys,'argv',['workflow.py','build','--run',str(task)])
    assert workflow.main()==0
    assert read(task/'reveal-config.json')['layout']==expected
    assert read(task/'reveal-config.json')['padding_mode']=='capped'


def test_build_cli_explicit_ratio_padding_mode_is_saved(task,monkeypatch):
    import reveal
    import workflow
    monkeypatch.setattr(reveal,'api_key',lambda *_:pytest.fail('Unexpected API request'))
    monkeypatch.setattr(sys,'argv',['workflow.py','build','--run',str(task),
                                   '--reveal','--reveal-padding-mode','ratio'])
    assert workflow.main()==0
    config=read(task/'reveal-config.json')
    assert config['layout']=='grouped'
    assert config['padding']==.1
    assert config['padding_mode']=='ratio'


def test_reveal_plan_cli_defaults_to_grouped_and_keeps_linked_photo(task,monkeypatch):
    import workflow
    a=read(task/'analysis.json')
    a['objects'].append({'id':'frame','kind':'overlay','bbox':[0,0,200,300],
                         'label':'frame','description':'frame','method':'placeholder','photo_id':'photo'})
    a['layer_order'].append('frame');save(task/'analysis.json',a)
    monkeypatch.setattr(sys,'argv',['workflow.py','reveal-plan','--run',str(task)])
    assert workflow.main()==0
    p=read(task/'previews/reveal-plan/request-plan.json')
    assert p['mode']=='grouped' and p['padding_mode']=='capped' and len(p['batches'])==1
    b=p['batches'][0]
    assert b['count']==2 and b['crop_box']==[0,0,200,300]
    assert next(o for o in b['objects'] if o['role']=='photo_context')['bbox']==[10,20,190,280]


def test_historical_plan_without_padding_mode_remains_ratio_compatible():
    s=scene(1)
    old=plan(s,targets(s),padding_mode='ratio')
    asset=next(o for o in old['batches'][0]['objects'] if o['role']=='asset')
    photo=next(o for o in old['batches'][0]['objects'] if o['role']=='photo_context')
    assert 'padding_mode' not in old and 'padding_mode' not in asset
    assert asset['reference_bbox']==[32,26,248,314]
    assert photo['reference_bbox']==photo['original_bbox']==[70,70,210,230]
