"""Explicit background identity from model output through customer rendering."""
from copy import deepcopy

import numpy as np
import pytest
from PIL import Image

from collage import contracts
from collage.config import Config
from collage.storage import read, save
from collage.workflow.stages.analyze import compile_inventory, repair_plan
from collage.workflow.stages import bind, photo_windows as windows
from photo_windows import defaults, check, enforce_canvas_base
from prepare import prepare
from scene import compile_scene
from render import render, material
from motion_render import fixed_photo_layer, make_plan
from reveal_assets import targets
from reveal_chain import backgrounds


def inventory(route='photo'):
    bg = {'id': 'background', 'kind': 'background', 'background_type': route,
          'bbox': [0, 378, 922, 1000], 'description': 'Person and grass are one photograph'}
    if route == 'solid_color': bg['color'] = '#00000000'
    return {'objects': [bg], 'layer_order': ['background']}


@pytest.mark.parametrize('route', ['photo', 'reference_clean', 'solid_color'])
def test_background_source_is_preserved_and_bounds_are_program_owned(route):
    source = inventory(route); original = deepcopy(source)
    result = compile_inventory(source, [960, 1280], 'normalized_1000')['analysis']
    bg = result['objects'][0]
    assert bg['kind'] == 'background' and bg['background_type'] == route
    assert bg['bbox'] == [0, 0, 960, 1280] and bg['rotation'] == 0
    assert bg['method'] == ('extract' if route == 'reference_clean' else 'local')
    contracts.validate_backgrounds(result)
    assert source == original


@pytest.mark.parametrize('fault', ['missing', 'multiple', 'no_source', 'wrong_kind', 'wrong_order'])
def test_invalid_background_requires_structural_reassessment(fault):
    source = inventory()
    photo = {'id': 'inset', 'kind': 'photo', 'bbox': [10, 10, 200, 300], 'description': 'Independent inset'}
    source['objects'].append(photo); source['layer_order'].append('inset')
    if fault == 'missing': source['objects'].pop(0); source['layer_order'].pop(0)
    if fault == 'multiple':
        source['objects'].append({**source['objects'][0], 'id': 'extra'})
        source['layer_order'].append('extra')
    if fault == 'no_source': source['objects'][0].pop('background_type')
    if fault == 'wrong_kind': source['objects'][0]['kind'] = 'photo'
    if fault == 'wrong_order': source['layer_order'].reverse()
    with pytest.raises(ValueError): compile_inventory(source, [960, 1280], 'normalized_1000')
    assert repair_plan(source, [960, 1280], 'normalized_1000') is None


def test_window_placement_cannot_shrink_background_and_photos_are_not_guessed():
    analysis = compile_inventory(inventory(), [960, 1280], 'normalized_1000')['analysis']
    plan = defaults(analysis)
    plan['photos'][0]['placement'] = {'bbox': [100, 400, 800, 1100], 'rotation': 30}
    original = deepcopy(plan)
    value, _, _ = windows.compile_response(plan, {'analysis': analysis, 'text_units': []})
    assert value['analysis']['objects'][0]['bbox'] == [0, 0, 960, 1280]
    assert value['analysis']['objects'][0]['rotation'] == 0
    assert plan == original
    analysis['objects'][0].update(kind='photo', bbox=[100, 400, 800, 1100])
    analysis['objects'][0].pop('background_type')
    original = deepcopy(analysis)
    enforce_canvas_base(analysis)
    assert analysis == original


def test_photo_background_cannot_be_cut_out():
    analysis = compile_inventory(inventory(), [960, 1280], 'normalized_1000')['analysis']
    plan = defaults(analysis)
    plan['photos'][0]['subject']['preserve_background'] = False
    with pytest.raises(ValueError, match='photo background'): check(plan, analysis)


@pytest.mark.parametrize('route', ['photo', 'reference_clean', 'solid_color'])
def test_window_and_binding_filters_include_only_customer_backgrounds(route):
    analysis = compile_inventory(inventory(route), [960, 1280], 'normalized_1000')['analysis']
    filtered = windows.input_analysis(analysis)
    expected = ['background'] if route == 'photo' else []
    assert [o['id'] for o in filtered['objects']] == expected
    context = bind.context_for({'reference_size': [960, 1280], 'catalog': []}, analysis, defaults(analysis))
    assert [p['slot_id'] for p in context['photo_slots']] == expected
    if expected:
        assert context['photo_slots'][0]['background_type'] == 'photo'
        assert context['photo_slots'][0]['kind'] == 'background'


def test_customer_background_binds_renders_and_enters_live_plan(tmp_path):
    reference = tmp_path/'reference.png'; Image.new('RGB', (96, 128), '#0000FF').save(reference)
    customers = tmp_path/'customers'; customers.mkdir()
    Image.new('RGB', (96, 128), '#EE2211').save(customers/'customer.png')
    run = tmp_path/'run'; prepare(reference, [customers], run, width=192)
    analysis = compile_inventory(inventory(), [96, 128], 'normalized_1000', run)['analysis']
    save(run/'analysis.json', analysis)
    save(run/'structure.json', {**inventory(), 'coordinate_mode': 'normalized_1000'})
    plan = defaults(analysis); save(run/'photo-windows.json', plan)
    catalog = read(run/'prepared/catalog.json')['assets']; asset = catalog[0]
    structure = bind.read_structure(run, Config())
    context = bind.context_for({'reference_size': [96, 128], 'catalog': catalog}, structure, plan)
    assert context['photo_slots'][0]['bbox'] == [0, 0, 96, 128]
    answer = {'photos': [{'slot_id': 'background', 'asset_id': asset['id'],
                          'source_crop': [0, 0, 1, 1], 'crop_center': [.5, .5],
                          'mirror_x': False, 'reason': 'Full customer scene'}]}
    save(run/'bindings.json', bind.validate(answer, context, structure, run, plan))
    scene = compile_scene(run)
    assert scene['objects'][0]['kind'] == 'background'
    assert not targets(scene) and not backgrounds(scene)
    result = render(run)
    assert result['customer_photos_verified']
    with Image.open(run/'final.png') as image:
        assert image.getextrema() == ((238, 238), (34, 34), (17, 17))
    with Image.open(asset['file']) as image:
        live = fixed_photo_layer(scene['objects'][0], image.convert('RGBA'), None, scene)
    assert np.all(np.asarray(live) == [238, 34, 17, 255])
    save(run/'media/manifest.json', {'videos': [{**asset, 'id': 'video_1', 'asset_id': asset['id'],
                                               'poster': asset, 'duration_us': 1000000}]})
    assert make_plan(run, scene, result)['slots'] == [
        {'object_id': 'background', 'video_id': 'video_1', 'mode': 'cover'}]


def test_popout_can_share_customer_background():
    source = inventory()
    source['objects'].append({'id': 'front', 'kind': 'photo', 'linked_to': 'background',
                              'bbox': [100, 100, 300, 500], 'description': 'Continuous foreground across decoration'})
    source['layer_order'].append('front')
    analysis = compile_inventory(source, [960, 1280], 'normalized_1000')['analysis']
    check(defaults(analysis), analysis)


@pytest.mark.parametrize('color,rgba', [('#1A2B3C', (26, 43, 60, 255)),
                                      ('#00000000', (0, 0, 0, 0))])
def test_solid_color_renders_uniform_pixels_without_photo_or_extraction(color, rgba, tmp_path):
    source = inventory('solid_color')
    source['objects'][0]['color'] = color
    analysis = compile_inventory(source, [96, 128], 'normalized_1000')['analysis']
    obj = analysis['objects'][0]
    assert obj['style'] == {'fill': color}
    assert not windows.input_analysis(analysis)['objects']
    assert not targets(analysis) and not backgrounds(analysis)
    tile, _ = material(obj, (96, 128), None, [], tmp_path, analysis)
    assert np.all(np.asarray(tile) == rgba)


def test_new_inventory_rejects_old_color_name_but_saved_analysis_remains_readable(tmp_path):
    from jsonschema import Draft202012Validator
    source = inventory('solid_color')
    analysis = compile_inventory(source, [96, 128], 'normalized_1000')['analysis']
    source['objects'][0]['background_type'] = 'simple_design'
    with pytest.raises(ValueError, match='background_type'):
        compile_inventory(source, [96, 128], 'normalized_1000')
    analysis['objects'][0]['background_type'] = 'simple_design'
    original = deepcopy(analysis)
    Draft202012Validator(contracts.ANALYSIS).validate(analysis)
    contracts.validate_backgrounds(analysis)
    tile, _ = material(analysis['objects'][0], (96, 128), None, [], tmp_path, analysis)
    assert np.all(np.asarray(tile) == [0, 0, 0, 0])
    assert analysis == original
