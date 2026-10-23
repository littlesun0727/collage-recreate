"""Percentage reconstruction, input isolation and historical protocol compatibility."""
from copy import deepcopy
import math

import pytest
from jsonschema import Draft202012Validator, ValidationError
from PIL import Image

from collage.workflow.support import box_geometry as refine


def bundle(rotation=0):
    return {'analysis': {'reference_size': [1600, 2400], 'objects': [
        {'id': 'target', 'kind': 'overlay', 'method': 'extract',
         'bbox': [800, 1000, 1100, 1200], 'rotation': rotation,
         'description': 'Complete target', 'style': {'opacity': .9}}],
        'layer_order': ['target']}, 'text_units': []}


def manifest(size=(1200, 1500)):
    return {'version': 5, 'reference_size': [1600, 2400], 'crops': [
        {'id': 'target', 'crop_region': [200, 400, 200+size[0], 400+size[1]], 'size': list(size)}]}


def reply(**values):
    return {'movement_unit': 'original_bbox_percent', 'objects': [{
        'id': 'target', 'status': 'adjust' if any(values.values()) else 'keep',
        'edges': {e: {'action': 'expand' if values.get(e, 0) else 'keep',
                      'percent': values.get(e, 0)} for e in refine.EDGES}, 'reason': ''}]}


def test_percent_uses_original_box_dimensions_for_all_sides_not_crop():
    answer = reply(left=2, right=3, bottom=1)
    Draft202012Validator(refine.schema(['target'])).validate(answer)
    original = bundle()
    for info in (manifest(), manifest((1300, 1600))):
        result, notes, _ = refine.merge(answer, original, info)
        assert result['analysis']['objects'][0]['bbox'] == [794, 1000, 1109, 1202]
        assert not notes
        assert result['analysis']['objects'][0]['style'] == {'opacity': .9}
    assert original['analysis']['objects'][0]['bbox'] == [800, 1000, 1100, 1200]


def test_two_sides_use_same_unmodified_basis():
    result, _, _ = refine.merge(reply(left=5, right=5, top=10, bottom=10), bundle(), manifest())
    assert result['analysis']['objects'][0]['bbox'] == [785, 980, 1115, 1220]


def test_subpercent_movements_are_not_quantized_to_fixed_steps():
    result, _, _ = refine.merge(reply(left=.5, bottom=1.25), bundle(), manifest())
    assert result['analysis']['objects'][0]['bbox'] == [798, 1000, 1100, 1202]


@pytest.mark.parametrize('angle', [0, 12, -25, 90])
def test_rotated_expansion_preserves_angle_and_contains_original(angle):
    original = bundle(angle)
    result, notes, _ = refine.merge(reply(left=10, right=20, top=10, bottom=5), original, manifest())
    obj = result['analysis']['objects'][0]
    assert obj['rotation'] == angle and not notes
    l, t, r, b = obj['bbox']
    cx, cy = (l+r)/2, (t+b)/2
    a = math.radians(angle)
    for x, y in refine.rectangle_corners(original['analysis']['objects'][0]['bbox'], angle):
        u = (x-cx)*math.cos(a)+(y-cy)*math.sin(a)
        v = -(x-cx)*math.sin(a)+(y-cy)*math.cos(a)
        assert -(r-l)/2 <= u <= (r-l)/2
        assert -(b-t)/2 <= v <= (b-t)/2


def test_axis_aligned_source_edge_is_allowed_and_clamped():
    original = bundle()
    original['analysis']['objects'][0]['bbox'] = [800, 1800, 1100, 2390]
    info = manifest((1400, 2000))
    result, notes, _ = refine.merge(reply(bottom=5), original, info)
    assert result['analysis']['objects'][0]['bbox'] == [800, 1800, 1100, 2400]
    assert not notes


def test_internal_crop_boundary_is_not_treated_as_target_edge():
    result, notes, _ = refine.merge(reply(right=100), bundle(), manifest())
    assert result['decision'] == 'keep' and notes


def test_keep_preserves_exact_geometry():
    result, notes, mapping = refine.merge(reply(), bundle(12.345), manifest())
    assert result['decision'] == 'keep' and not notes
    assert mapping[0]['before'] == mapping[0]['after']


@pytest.mark.parametrize('action,percent', [('shrink', 1), ('expand', -1), ('expand', 0),
    ('expand', 101), ('expand', True), ('expand', float('nan')), ('expand', float('inf')),
    ('keep', 1), ('uncertain', 1)])
def test_invalid_movements_are_rejected(action, percent):
    answer = reply(left=1)
    answer['objects'][0]['edges']['left'] = {'action': action, 'percent': percent}
    with pytest.raises(ValueError):
        refine.validate(answer, manifest())


def test_old_amount_field_and_unit_cannot_be_silently_reinterpreted():
    answer = reply(left=1)
    answer['objects'][0]['edges']['left']['amount'] = 50
    with pytest.raises(ValidationError):
        Draft202012Validator(refine.schema(['target'])).validate(answer)
    with pytest.raises(ValueError):
        refine.validate(answer, manifest())
    answer = reply(left=1)
    answer['movement_unit'] = 'crop_normalized_1000'
    with pytest.raises(ValueError):
        refine.validate(answer, manifest())


def test_v4_saved_movements_keep_crop_scale_and_shrinking():
    answer = reply()
    answer['movement_unit'] = 'crop_normalized_1000'
    for edge in answer['objects'][0]['edges'].values():
        edge['amount'] = edge.pop('percent')
    answer['objects'][0]['status'] = 'adjust'
    answer['objects'][0]['edges']['left'] = {'action': 'shrink', 'amount': 10}
    info = manifest(); info['version'] = 4
    Draft202012Validator(refine.schema(['target'], version=4)).validate(answer)
    result, notes, _ = refine.merge(answer, bundle(), info)
    assert result['analysis']['objects'][0]['bbox'] == [812, 1000, 1100, 1200]
    assert not notes


def test_model_inputs_have_no_numeric_bbox_corners_or_normalized_grid(tmp_path):
    run = tmp_path/'run'; (run/'prepared').mkdir(parents=True)
    Image.new('RGB', (1600, 2400), 'white').save(run/'prepared/reference.png')
    context, images, info = refine.prepare(run, run/'boxes', bundle(12))
    target = context['targets'][0]
    assert context['movement_unit'] == 'original_bbox_percent'
    assert not {'original_bbox', 'original_corners', 'source_edges'} & set(target)
    assert target['original_rotation'] == 12
    assert '1000' not in images[0][0]
    assert info['version'] == 5
    assert info['crops'][0]['before_bbox'] == [800, 1000, 1100, 1200]
