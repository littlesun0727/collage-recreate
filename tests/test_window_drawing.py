"""Observable drawing behavior, filtered requests and the saved-plan boundary."""
from copy import deepcopy
import json

import numpy as np
from PIL import Image, ImageDraw
import pytest
from jsonschema import Draft202012Validator

from collage import contracts, prompts
from collage.workflow.stages.analyze import compile_inventory, schema as inventory_schema
from collage.workflow.stages.photo_windows import input_analysis, compile_response, schema
from collage.vlm.coordinates import ReferenceCoordinates
from collage.vlm.gpt_schema import project, restore
from photo_windows import defaults, check, resolve_seams, configure, apply_window, needs_subject_mask
from photos import fit_image
from window_contract import execution_spec, SCHEMA, empty_stroke
from window_drawing import sampling_margin


def bundle():
    return compile_inventory({'objects': [
        {'id': 'bg', 'kind': 'background', 'bbox': [0, 0, 1000, 1000], 'description': 'white', 'color': '#FFFFFF'},
        {'id': 'carrier', 'kind': 'overlay', 'bbox': [50, 50, 950, 950], 'description': 'fixed printed label'},
        {'id': 'photo', 'kind': 'photo', 'bbox': [200, 200, 800, 800], 'description': 'portrait with visible environment', 'parent_id': 'carrier'},
        {'id': 'frame', 'kind': 'frame', 'bbox': [180, 180, 820, 820], 'description': 'paper frame', 'photo_id': 'photo'},
        {'id': 'title', 'kind': 'text', 'bbox': [100, 20, 500, 80], 'text': 'private label'},
    ]}, [1000, 1000], 'normalized_1000')


def plan():
    value = defaults(bundle()['analysis'])
    value['photos'][0]['mask'] = {'enabled': True, 'shape': {'type': 'rectangle'}, 'stroke': empty_stroke()}
    return value


def stroke(spec, target, pattern='solid', color='#0000FF'):
    line = spec[target]['stroke']
    line.update(enabled=True, width=.04, color=color, pattern=pattern, line_cap='round')
    if pattern != 'solid':
        line['dash'] = [.06, .06]


def aligned(tile, size=(100, 100)):
    x, y = (tile.width-size[0])//2, (tile.height-size[1])//2
    return np.asarray(tile.crop((x, y, x+size[0], y+size[1])))


def test_filter_keeps_only_photo_frame_without_stale_labels_or_dangling_carrier_ids():
    current = bundle(); original = deepcopy(current)
    current['analysis']['objects'][2]['mode'] = 'stroke'
    filtered = input_analysis(current['analysis'])
    assert [o['id'] for o in filtered['objects']] == filtered['layer_order'] == ['photo', 'frame']
    assert set(filtered['objects'][0]) == {'id', 'kind', 'bbox', 'description'}
    assert filtered['objects'][1]['photo_id'] == 'photo'
    assert all(word not in json.dumps(filtered) for word in ('private label', 'carrier', 'mode'))
    assert current['analysis']['objects'][0] == original['analysis']['objects'][0]


@pytest.mark.parametrize('label', ['cutout', 'cover', 'feather', 'stroke'])
def test_first_analysis_rejects_effect_classification(label):
    value = {'objects': [{'id': 'p', 'kind': 'photo', 'bbox': [0, 0, 100, 100], 'description': 'photo', 'mode': label}],
             'layer_order': ['p']}
    assert not Draft202012Validator(inventory_schema([100, 100], 'pixel')).is_valid(value)


@pytest.mark.parametrize('window_enabled', [False, True])
def test_public_example_and_strict_provider_coordinates_roundtrip(window_enabled):
    example = json.loads(prompts.render('photo_windows').split('```json\n')[1].split('```')[0])
    if window_enabled:
        example['photos'][0]['mask'] = plan()['photos'][0]['mask']
    adapter = ReferenceCoordinates({'reference_size': [1200, 1600]}, 'normalized_1000')
    contract = adapter.schema(schema())
    Draft202012Validator(contract).validate(example)
    def nullable(value, node):
        if isinstance(value, dict):
            return {k: nullable(value[k], child) if k in value else None for k, child in node['properties'].items()}
        if isinstance(value, list) and node.get('type') == 'array':
            return [nullable(v, node['items']) for v in value]
        return value
    strict = nullable(example, contract)
    Draft202012Validator(project(contract)).validate(strict)
    decoded = adapter.decode(restore(strict, contract))
    assert decoded['photos'][0]['placement']['bbox'] == [120, 320, 840, 1280]
    assert decoded['photos'][0]['mask'] == example['photos'][0]['mask']
    assert decoded['photos'][0]['edge_gradient'] == example['photos'][0]['edge_gradient']


@pytest.mark.parametrize('preserve', [True, False])
def test_mask_and_subject_strokes_render_together_with_independent_background(preserve):
    spec = plan()['photos'][0]
    stroke(spec, 'mask', color='#FF0000'); stroke(spec, 'subject')
    spec['subject']['preserve_background'] = preserve
    mask = Image.new('L', (100, 100)); ImageDraw.Draw(mask).rectangle((30, 20, 69, 79), fill=255)
    im = Image.new('RGBA', mask.size, '#00FF00')
    if not preserve:
        im.putalpha(mask)
    photo = {'style': {}}; configure(photo, spec)
    assert needs_subject_mask(photo)
    tile, content = apply_window(im, photo['photo_window_spec'], mask if preserve else None)
    pixels = aligned(tile)
    assert np.any(np.all(pixels[:, :, :3] == [255, 0, 0], axis=2))
    assert np.any(np.all(pixels[:, :, :3] == [0, 0, 255], axis=2))
    assert pixels[50, 50].tolist() == [0, 255, 0, 255]
    assert pixels[50, 10, 3] == (255 if preserve else 0)
    assert content.size == tile.size


@pytest.mark.parametrize('target', ['mask', 'subject'])
def test_solid_dash_and_dot_have_different_visible_coverage(target):
    counts = []
    mask = Image.new('L', (160, 160)); ImageDraw.Draw(mask).ellipse((35, 25, 125, 135), fill=255)
    for pattern in ('solid', 'dash', 'dot'):
        spec = plan()['photos'][0]; stroke(spec, target, pattern)
        tile, _ = apply_window(Image.new('RGBA', mask.size, '#00FF00'), spec, mask)
        counts.append(np.count_nonzero(np.all(np.asarray(tile)[:, :, :3] == [0, 0, 255], axis=2)))
    assert 0 < counts[1] < counts[0]*.85
    assert 0 < counts[2] < counts[0]*.85


@pytest.mark.parametrize('edge', ['top', 'bottom', 'left', 'right'])
def test_inward_gradient_only_fades_requested_edge(edge):
    spec = plan()['photos'][0]
    spec['edge_gradient'].update(enabled=True, **{edge: .2})
    tile, _ = apply_window(Image.new('RGBA', (100, 100), '#00FF00'), spec)
    alpha = aligned(tile)[:, :, 3]
    positions = {'top': (0, 50), 'bottom': (99, 50), 'left': (50, 0), 'right': (50, 99)}
    for name, pos in positions.items():
        assert alpha[pos] < 5 if name == edge else alpha[pos] == 255
    assert alpha[50, 50] == 255
    # The entire strip must fade uniformly, including corners nearest another
    # hard side; selecting only the closest boundary produces opaque wedges.
    if edge in {'top', 'bottom'}:
        assert np.all(alpha == alpha[:, 50:51])
    else:
        assert np.all(alpha == alpha[50:51, :])


def test_outward_gradient_uses_original_source_transform_and_independent_widths():
    spec = plan()['photos'][0]
    spec['edge_gradient'].update(enabled=True, direction='outside', left=.1, right=.2)
    spec = execution_spec(spec)
    source = np.zeros((100, 160, 4), dtype='uint8')
    source[:, :, 0] = np.arange(160); source[:, :, 3] = 255
    margin = sampling_margin((100, 100), spec)
    sampled = fit_image(Image.fromarray(source), (100, 100), margin=margin)
    tile, _ = apply_window(sampled, spec, source_margin=margin)
    x0, y0 = (tile.width-100)//2, (tile.height-100)//2
    assert tile.getpixel((x0+50, y0+50)) == (80, 0, 0, 255)
    assert tile.getpixel((x0-5, y0+50))[0] == 25
    assert 0 < tile.getpixel((x0-5, y0+50))[3] < 255
    assert tile.getpixel((x0-15, y0+50))[3] == 0
    assert 0 < tile.getpixel((x0+115, y0+50))[3] < 255
    assert tile.getpixel((x0+50, y0-5))[3] == 0
    assert aligned(tile)[50, 0, 3] == aligned(tile)[50, 99, 3] == 255


def test_outward_gradient_cannot_extend_a_disabled_side_even_with_source_pixels():
    spec = plan()['photos'][0]
    spec['edge_gradient'].update(enabled=True, direction='outside', top=.2)
    tile, _ = apply_window(Image.new('RGBA', (140, 140), '#00FF00'), spec, source_margin=20)
    x0, y0 = (tile.width-100)//2, (tile.height-100)//2
    assert tile.getpixel((x0+50, y0-10))[3] > 0
    assert tile.getpixel((x0-5, y0-10))[3] == 0
    assert tile.getpixel((x0+105, y0-10))[3] == 0


def test_ellipse_gradient_tracks_curved_mask_boundary():
    spec = plan()['photos'][0]; spec['mask']['shape'] = {'type': 'ellipse'}
    spec['edge_gradient'].update(enabled=True, top=.2, bottom=.2, left=.2, right=.2)
    tile, _ = apply_window(Image.new('RGBA', (100, 100), '#00FF00'), spec)
    alpha = aligned(tile)[:, :, 3]
    assert alpha[10, 10] == 0 and alpha[50, 50] == 255
    assert 0 < alpha[18, 18] < 150  # Near the curve, far from rectangular edges.


def test_single_top_gradient_also_reaches_sloping_upper_ellipse():
    spec = plan()['photos'][0]; spec['mask']['shape'] = {'type': 'ellipse'}
    spec['edge_gradient'].update(enabled=True, top=.2)
    tile, _ = apply_window(Image.new('RGBA', (100, 100), '#00FF00'), spec)
    alpha = aligned(tile)[:, :, 3]
    assert 0 < alpha[12, 25] < 100
    assert alpha[50, 1] > 200 and alpha[98, 50] > 200


@pytest.mark.parametrize('fault', ['enabled', 'width', 'dash', 'dot_cap', 'center_offset'])
def test_contradictory_drawing_parameters_are_rejected(fault):
    value = plan(); spec = value['photos'][0]
    if fault == 'enabled': spec['edge_gradient']['enabled'] = True
    if fault == 'width': spec['subject']['stroke']['enabled'] = True
    if fault == 'dash': spec['mask']['stroke']['pattern'] = 'dash'
    if fault == 'dot_cap': spec['mask']['stroke'].update(pattern='dot', dash=[.01, .02])
    if fault == 'center_offset': spec['mask']['stroke'].update(placement='center', offset=.02)
    with pytest.raises(ValueError): resolve_seams(value, bundle()['analysis'])


def test_geometry_repair_preserves_both_strokes_and_public_contract():
    current = bundle(); value = plan(); spec = value['photos'][0]
    stroke(spec, 'mask', color='#FF0000'); stroke(spec, 'subject', 'dash')
    spec['edge_gradient'].update(enabled=True, direction='outside', left=.3)
    # Only the original photo footprint has backing; no transparent extension is supported.
    current['analysis']['objects'][0]['bbox'] = [200, 200, 800, 800]
    original = deepcopy(value)
    resolved, notes = resolve_seams(value, current['analysis'])
    assert notes and not resolved['photos'][0]['edge_gradient']['enabled']
    assert resolved['photos'][0]['mask'] == original['photos'][0]['mask']
    assert resolved['photos'][0]['subject'] == original['photos'][0]['subject']
    Draft202012Validator(SCHEMA).validate(resolved)
    Draft202012Validator(contracts.PHOTO_WINDOWS).validate(resolved)
    assert check(resolved, current['analysis']) == resolved
    assert value == original


def test_preserved_subject_stroke_needs_a_mask_and_does_not_close_across_crop():
    spec = plan()['photos'][0]; stroke(spec, 'subject', 'dash')
    im = Image.new('RGBA', (100, 100), '#00FF00')
    with pytest.raises(ValueError, match='aligned L subject mask'):
        apply_window(im, spec)
    mask = Image.new('L', im.size); ImageDraw.Draw(mask).rectangle((30, 20, 70, 99), fill=255)
    tile, _ = apply_window(im, spec, mask)
    pixels = aligned(tile)
    assert np.all(pixels[97:, 40:60, :3] == [0, 255, 0])


@pytest.mark.parametrize('fixed_window', [False, True])
def test_cutout_outline_can_extend_past_placement_only_without_fixed_window(fixed_window):
    spec = plan()['photos'][0]
    if not fixed_window:
        spec['mask'] = {'enabled': False}
    spec['subject']['preserve_background'] = False
    stroke(spec, 'subject', color='#0000FF')
    spec['subject']['stroke'].update(width=.06, placement='outside')
    # Standalone cutouts are trimmed to their alpha bounds before placement;
    # head, feet or hands can therefore touch any edge of the placement box.
    mask = Image.new('L', (100, 100))
    ImageDraw.Draw(mask).ellipse((0, 0, 99, 99), fill=255)
    source = Image.new('RGBA', mask.size, '#00FF00'); source.putalpha(mask)
    tile, content = apply_window(source, spec)
    pixels = np.asarray(tile); pad = (tile.width-100)//2
    for x, y in [(pad+50, pad-3), (pad+50, pad+102), (pad-3, pad+50), (pad+102, pad+50)]:
        if fixed_window:
            assert pixels[y, x, 3] == 0
        else:
            assert pixels[y, x].tolist() == [0, 0, 255, 255]
    # Added outline is decoration, never customer-photo content.
    assert np.array_equal(np.asarray(content.crop((pad, pad, pad+100, pad+100))), np.asarray(mask))
    assert content.getbbox() == (pad, pad, pad+100, pad+100)
    assert np.array_equal(aligned(tile)[np.asarray(mask) == 255], np.asarray(source)[np.asarray(mask) == 255])


@pytest.mark.parametrize('rotation,mirror', [(0, False), (18, True)])
@pytest.mark.parametrize('window_enabled', [True, False])
@pytest.mark.parametrize('preserve', [True, False])
def test_static_and_live_use_identical_dual_strokes_and_outward_sampling(tmp_path, monkeypatch, rotation, mirror, window_enabled, preserve):
    from common import sha
    from photos import make_photo
    from motion_render import fixed_photo_layer
    spec = plan()['photos'][0]
    stroke(spec, 'subject', 'dot')
    spec['subject']['preserve_background'] = preserve
    if window_enabled:
        stroke(spec, 'mask', 'dash', '#FF0000')
        spec['edge_gradient'].update(enabled=True, direction='outside', left=.1, right=.2)
    else:
        spec['mask'] = {'enabled': False}
    obj = {'id': 'photo', 'bbox': [50, 50, 150, 150], 'rotation': rotation, 'style': {},
           'binding': {'source_crop': [0, 0, 1, 1], 'crop_center': [.5, .5], 'mirror_x': mirror}}
    configure(obj, spec)
    source = Image.new('RGBA', (160, 100), '#00FF00')
    ImageDraw.Draw(source).rectangle((75, 0, 85, 99), fill='#FFFF00')
    path = tmp_path/'customer.png'; source.save(path)
    asset = {'file': str(path), 'sha256': sha(path)}
    mask = Image.new('L', source.size); ImageDraw.Draw(mask).ellipse((45, 10, 115, 99), fill=255)
    cut = source.copy(); cut.putalpha(mask)
    monkeypatch.setattr('photos.cutout', lambda *args: cut.copy())
    tile, _ = make_photo(obj, asset, obj['binding'], (100, 100), tmp_path, None)
    if rotation:
        tile = tile.rotate(-rotation, Image.Resampling.BICUBIC, expand=True)
    still = Image.new('RGBA', (200, 200))
    still.alpha_composite(tile, (round((200-tile.width)/2), round((200-tile.height)/2)))
    live = fixed_photo_layer(obj, source if preserve else cut, None if preserve else mask.getbbox(),
                            {'canvas_size': [200, 200], 'reference_size': [200, 200]}, mask)
    assert np.array_equal(np.asarray(still), np.asarray(live))


def test_new_popout_fallback_retains_link_without_an_initial_effect_label():
    current = compile_inventory({'objects': [
        {'id': 'base', 'kind': 'photo', 'bbox': [100, 200, 900, 900], 'description': 'base with environment'},
        {'id': 'front', 'kind': 'photo', 'bbox': [100, 100, 900, 900], 'description': 'same person above window', 'linked_to': 'base'},
    ]}, [1000, 1000], 'normalized_1000')
    value = defaults(current['analysis'])
    assert value['photos'][0]['subject']['preserve_background']
    assert not value['photos'][1]['subject']['preserve_background']
    assert value['photos'][1]['linked_to'] == 'base'
    assert all('mode' not in o for o in current['analysis']['objects'])
    assert check(value, current['analysis']) == value


@pytest.mark.parametrize('extra', ['shape', 'stroke'])
def test_disabled_window_rejects_geometry_instead_of_silently_dropping_it(extra):
    value = plan(); spec = value['photos'][0]
    original = deepcopy(spec['mask'][extra])
    spec['mask'] = {'enabled': False, extra: original}
    assert not Draft202012Validator(SCHEMA).is_valid(value)
    with pytest.raises(ValueError, match='disabled window must omit'):
        execution_spec(spec)


def test_disabled_window_rejects_gradient_before_seam_repair():
    value = plan(); spec = value['photos'][0]
    spec['mask'] = {'enabled': False}
    spec['edge_gradient'].update(enabled=True, top=.1)
    original = deepcopy(value)
    with pytest.raises(ValueError, match='requires an enabled fixed window'):
        resolve_seams(value, bundle()['analysis'])
    assert value == original


def test_subject_stroke_does_not_accept_a_geometric_contour():
    value = plan(); spec = value['photos'][0]
    spec['mask'] = {'enabled': False}
    stroke(spec, 'subject')
    spec['subject']['stroke']['points'] = [[0, 0], [1, 0], [.5, 1]]
    assert not Draft202012Validator(SCHEMA).is_valid(value)


def test_disabled_window_compilation_keeps_segmentation_requirement():
    value = plan(); spec = value['photos'][0]
    spec['mask'] = {'enabled': False}
    stroke(spec, 'subject')
    original = deepcopy(value)
    resolved, notes = resolve_seams(value, bundle()['analysis'])
    assert resolved == original and notes == []
    photo = {'style': {}}
    configure(photo, spec)
    assert not photo['photo_window_spec']['window_enabled']
    assert needs_subject_mask(photo)
    with pytest.raises(ValueError, match='aligned L subject mask'):
        apply_window(Image.new('RGBA', (100, 100), '#00FF00'), spec)
    assert value == original


@pytest.mark.parametrize('preserve', [True, False])
def test_same_plan_tracks_two_customer_silhouettes_without_window_geometry(monkeypatch, preserve):
    spec = plan()['photos'][0]
    spec['mask'] = {'enabled': False}
    spec['subject']['preserve_background'] = preserve
    stroke(spec, 'subject')
    spec['subject']['stroke']['placement'] = 'outside'
    original = deepcopy(spec)
    masks = [Image.new('L', (100, 100)) for _ in range(2)]
    ImageDraw.Draw(masks[0]).ellipse((30, 10, 69, 89), fill=255)
    ImageDraw.Draw(masks[1]).rectangle((10, 30, 89, 69), fill=255)
    monkeypatch.setattr('photo_windows.shape_mask', lambda *args: pytest.fail('Disabled window generated geometry'))
    blue = []
    for mask in masks:
        source = Image.new('RGBA', mask.size, '#00FF00')
        if not preserve:
            source.putalpha(mask)
        tile, content = apply_window(source, spec, mask if preserve else None)
        pixels = aligned(tile)
        blue.append(np.all(pixels == [0, 0, 255, 255], axis=2))
        assert pixels[50, 50].tolist() == [0, 255, 0, 255]
        assert pixels[0, 0, 3] == (255 if preserve else 0)
        assert content.size == tile.size
    assert blue[0][8, 50] and not blue[1][8, 50]
    assert blue[1][50, 8] and not blue[0][50, 8]
    assert spec == original


def test_fixed_window_and_subject_contours_stay_independent_when_customer_changes():
    spec = plan()['photos'][0]
    spec['mask']['shape'] = {'type': 'ellipse'}
    spec['subject']['preserve_background'] = False
    stroke(spec, 'mask', color='#FF0000')
    stroke(spec, 'subject', color='#0000FF')
    masks = [Image.new('L', (100, 100)) for _ in range(2)]
    ImageDraw.Draw(masks[0]).rectangle((0, 30, 99, 69), fill=255)
    ImageDraw.Draw(masks[1]).rectangle((30, 0, 69, 99), fill=255)
    colors = []
    for mask in masks:
        source = Image.new('RGBA', mask.size, '#00FF00'); source.putalpha(mask)
        tile, _ = apply_window(source, spec)
        colors.append(aligned(tile))
    red = [np.all(p == [255, 0, 0, 255], axis=2) for p in colors]
    blue = [np.all(p == [0, 0, 255, 255], axis=2) for p in colors]
    assert red[0].any() and np.array_equal(red[0], red[1])
    assert blue[0].any() and blue[1].any() and not np.array_equal(blue[0], blue[1])
    # Window clipping must not invent a vertical subject contour where a bar exits.
    spec['mask']['stroke'].update(enabled=False, width=0)
    source = Image.new('RGBA', masks[0].size, '#00FF00'); source.putalpha(masks[0])
    pixels = aligned(apply_window(source, spec)[0])
    assert pixels[50, 2].tolist() == [0, 255, 0, 255]


def test_ambiguous_polygon_stroke_is_saved_for_visual_review_without_rewriting(tmp_path):
    from types import SimpleNamespace
    from collage.storage import save, read
    from collage.workflow.stages import photo_windows as stage
    current = bundle()
    save(tmp_path/'analysis.json', current['analysis'])
    save(tmp_path/'text-units.json', current['text_units'])
    save(tmp_path/'input.json', {'reference_size': [1000, 1000]})
    (tmp_path/'prepared').mkdir()
    Image.new('RGB', (1000, 1000), 'white').save(tmp_path/'prepared/reference.png')
    answer = plan(); spec = answer['photos'][0]
    spec['placement'] = {'bbox': [200, 200, 800, 800], 'rotation': 0}
    spec['subject']['preserve_background'] = False
    spec['mask']['shape'] = {'type': 'polygon', 'points': [[0, 0], [1, 0], [.5, 1]]}
    stroke(spec, 'mask')
    original = deepcopy(answer)
    warnings = []; writes = []
    def ask(name, prompt, context, images, contract, validate):
        Draft202012Validator(contract).validate(answer)
        validate(answer)
        return answer
    ctx = SimpleNamespace(run=tmp_path, control=tmp_path/'workflow', client=SimpleNamespace(ask=ask),
                          reference_context=lambda: {'reference_size': [1000, 1000]},
                          analysis_images=lambda: [], write_reference=lambda value: writes.append(value),
                          warn=lambda *args: warnings.append(args))
    stage.execute(ctx)
    evidence = read(tmp_path/'workflow/window-geometry.json')
    assert evidence['status'] == 'needs_visual_review'
    assert evidence['drawing_review'][0]['status'] == 'needs_visual_review'
    assert warnings[0][0] == 'window_boundary_review' and len(writes) == 1
    saved = read(tmp_path/'photo-windows.json')['photos'][0]
    assert saved['mask'] == spec['mask'] and saved['subject'] == spec['subject']
    assert answer == original
