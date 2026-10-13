"""Regression: the top cookie group used to upload a 399 x 112 crop."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from reveal_plan import crop_geometry,plan


def test_cookie_strip_expands_context_and_preserves_reference_coordinates():
    objects=[{'id':'left','kind':'overlay','bbox':[256,25,352,92]},
             {'id':'right','kind':'overlay','bbox':[524,35,621,103]}]
    scene={'reference_size':[900,1212],'objects':objects}
    batch=plan(scene,objects,mode='grouped')['batches'][0]
    assert batch['crop_box']==[239,0,638,129]
    for obj in batch['objects']:
        assert obj['original_bbox']==next(o['bbox'] for o in objects if o['id']==obj['id'])
        assert [value+batch['crop_box'][i%2] for i,value in enumerate(obj['bbox'])]==obj['reference_bbox']


@pytest.mark.parametrize('box',[
    [0,0,20,20],[880,1192,900,1212],[0,500,20,520],[430,1192,450,1212],
    [400,500,420,520],[400,500,513,613],
])
def test_small_crop_stays_inside_reference_and_contains_target(box):
    crop,*_=crop_geometry(box,[900,1212],'grouped')
    assert min(crop[2]-crop[0],crop[3]-crop[1])>128
    assert 0<=crop[0]<=box[0]<box[2]<=crop[2]<=900
    assert 0<=crop[1]<=box[1]<box[3]<=crop[3]<=1212


def test_valid_crop_and_full_mode_are_unchanged():
    assert crop_geometry([228,171,656,1137],[900,1212],'grouped')[0]==[203,146,681,1162]
    assert crop_geometry([249,18,628,110],[900,1212],'full')[0]==[0,0,900,1212]


def test_reference_too_small_cannot_supply_extra_context():
    with pytest.raises(ValueError,match='too small'):
        crop_geometry([0,0,80,100],[80,1200],'grouped')
