"""Same-source pop-out relationships must survive every contract boundary."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw
from jsonschema import Draft202012Validator

from collage.workflow.stages import analyze as structure
from collage.workflow.stages import photo_windows as window_geometry
from collage.vlm.client import ModelError
from collage.vlm.gpt_schema import project, restore
from collage.workflow.stages.bind import context_for
from collage.storage import read, save
from photo_windows import defaults, check, configure, apply_window


def inventory():
    return {'objects': [
        {'id': 'bg', 'kind': 'background', 'background_type': 'solid_color',
         'bbox': [0, 0, 1000, 1000], 'description': 'White canvas', 'color': '#FFFFFF'},
        {'id': 'base', 'kind': 'photo', 'bbox': [100, 400, 900, 900],
         'description': 'Bottom photo retains grass and trees; head continues above the top edge.'},
        {'id': 'frame', 'kind': 'frame', 'photo_id': 'base', 'bbox': [80, 380, 920, 920],
         'description': 'Printed frame; foreground head covers its top edge.'},
        {'id': 'front', 'kind': 'photo', 'linked_to': 'base',
         'bbox': [200, 200, 800, 900], 'description': 'Same continuous person as base; head above frame, bottom clipped.'}]}


def bundle():
    return structure.compile_inventory(inventory(), [1000, 1000], 'normalized_1000')


def window_answer(current):
    answer = defaults(current['analysis'])
    by_id = {o['id']: o for o in current['analysis']['objects']}
    for spec in answer['photos']:
        spec['placement'] = {'bbox': by_id[spec['photo_id']]['bbox'], 'rotation': 0}
    return answer


def test_popout_survives_strict_schema_engine_and_binding_context(tmp_path):
    value = inventory()
    contract = structure.legacy_schema([1000, 1000], 'normalized_1000')
    fields = contract['properties']['objects']['items']['properties']
    strict = {'objects': [{k: row.get(k) for k in fields} for row in value['objects']],
              'layer_order': ['bg', 'base', 'frame', 'front']}
    Draft202012Validator(project(contract)).validate(strict)
    restored = structure.normalize(restore(strict, contract))
    save(tmp_path/'input.json', {'reference_size': [1000, 1000]})
    current = structure.compile_inventory(restored, [1000, 1000], 'normalized_1000', tmp_path)
    assert current['analysis']['objects'][-1]['linked_to'] == 'base'
    assert all('mode' not in o for o in current['analysis']['objects'])
    answer = window_answer(current)
    answer['photos'][-1].pop('linked_to')  # Optional model omission must not sever the source mapping.
    updated, plan, _ = window_geometry.compile_response(answer, current, tmp_path)
    assert 'linked_to' not in answer['photos'][-1]  # No mutation of raw evidence.
    assert plan['photos'][-1]['linked_to'] == 'base'
    context = context_for({'reference_size': [1000, 1000], 'catalog': []}, updated['analysis'], plan)
    base, front = context['photo_slots']
    assert front['linked_to'] == 'base'
    assert 'subject' not in base
    assert base['requires_segmentable_subject'] is True
    assert 'linked_foregrounds' not in base


@pytest.mark.parametrize('fault', ['self', 'missing', 'cycle', 'chain', 'nonphoto'])
def test_bad_popout_structure_cannot_be_accepted_or_null_patched(fault):
    value = inventory(); base, frame, front = value['objects'][1:]
    if fault == 'self': front['linked_to'] = 'front'
    if fault == 'missing': front['linked_to'] = 'missing'
    if fault == 'cycle': base['linked_to'] = 'front'
    if fault == 'chain': base['linked_to'] = 'another'
    if fault == 'nonphoto': frame['linked_to'] = 'base'
    with pytest.raises(ValueError):
        structure.compile_inventory(value, [1000, 1000], 'normalized_1000')
    assert structure.repair_plan(value, [1000, 1000], 'normalized_1000') is None


@pytest.mark.parametrize('order', [(0, 2, 1), (1, 0, 2), (2, 1, 0)])
def test_popout_order_is_preserved_through_analysis_and_window_validation(order, tmp_path):
    value = inventory()
    value['layer_order'] = ['bg'] + [value['objects'][i+1]['id'] for i in order]
    original = deepcopy(value)
    save(tmp_path/'input.json', {'reference_size': [1000, 1000]})
    current = structure.compile_inventory(value, [1000, 1000], 'normalized_1000', tmp_path)
    assert current['analysis']['layer_order'] == original['layer_order']
    by_id = {o['id']: o for o in current['analysis']['objects']}
    assert by_id['front']['linked_to'] == 'base'
    check(defaults(current['analysis']), current['analysis'])
    assert structure.repair_plan(value, [1000, 1000], 'normalized_1000') is None
    assert value == original


def test_independent_cutout_has_no_link_and_null_does_not_create_one():
    value = inventory(); value['objects'][-1]['linked_to'] = None
    current = structure.compile_inventory(value, [1000, 1000], 'normalized_1000')
    assert 'linked_to' not in current['analysis']['objects'][-1]
    assert all('linked_to' not in p for p in defaults(current['analysis'])['photos'])


@pytest.mark.parametrize('fault', ['change_source', 'remove_cutout', 'cutout_base'])
def test_window_stage_cannot_sever_explicit_popout(fault):
    current = bundle(); answer = window_answer(current)
    if fault == 'change_source': answer['photos'][-1]['linked_to'] = 'other'
    if fault == 'remove_cutout': answer['photos'][-1]['subject']['preserve_background'] = True
    if fault == 'cutout_base': answer['photos'][0]['subject']['preserve_background'] = False
    with pytest.raises(ValueError): window_geometry.compile_response(answer, current)


def test_linked_foreground_cannot_use_a_reference_head_outline_as_a_second_mask():
    from window_contract import empty_stroke
    current = bundle(); answer = window_answer(current)
    answer['photos'][-1]['mask'].update(enabled=True, stroke=empty_stroke())
    answer['photos'][-1]['mask']['shape'] = {'type': 'polygon', 'points': [[.5, 0], [1, .5], [.5, 1], [0, .5]]}
    with pytest.raises(ValueError, match='segmentation supplies the silhouette'):
        window_geometry.compile_response(answer, current)


def fake_runner(tmp_path, ask):
    current = bundle()
    save(tmp_path/'analysis.json', current['analysis'])
    save(tmp_path/'text-units.json', [])
    save(tmp_path/'input.json', {'reference_size': [1000, 1000]})
    (tmp_path/'prepared').mkdir()
    Image.new('RGB', (1000, 1000), 'white').save(tmp_path/'prepared/reference.png')
    return SimpleNamespace(run=tmp_path, control=tmp_path/'workflow',
        client=SimpleNamespace(ask=ask), reference_context=lambda: {'reference_size': [1000, 1000]},
        analysis_images=lambda: [], write_reference=lambda _: pytest.fail('Unexpected write'),
        warn=lambda *args: None)


def test_window_failure_retains_known_source_link_but_marks_it_unresolved(tmp_path):
    def fail(*args, **kwargs): raise ModelError('transport failed')
    runner = fake_runner(tmp_path, fail)
    window_geometry.execute(runner)
    plan = read(tmp_path/'photo-windows.json')
    assert plan['photos'][-1]['linked_to'] == 'base'
    assert plan['photos'][-1]['subject']['preserve_background'] is False
    evidence = read(tmp_path/'workflow/window-geometry.json')
    assert evidence['status'] == 'fallback'
    assert evidence['link_review'][0]['status'] == 'unresolved'


def test_missing_foreground_report_stops_instead_of_silently_rendering_cover(tmp_path):
    answer = window_answer(bundle())
    answer['structure_issues'] = ['base needs an additional foreground for a second crossing subject']
    def ask(stage, prompt, context, images, schema, validate):
        Draft202012Validator(schema).validate(answer)
        validate(answer)
        return answer
    runner = fake_runner(tmp_path, ask)
    with pytest.raises(window_geometry.StructureRepairRequired): window_geometry.execute(runner)
    evidence = read(tmp_path/'workflow/window-geometry.json')
    assert evidence['status'] == 'structure_repair_required'
    assert not (tmp_path/'photo-windows.json').exists()


@pytest.mark.parametrize('mirror', [False, True])
def test_shared_render_keeps_pixels_continuous_and_background_inside_base(mirror):
    from photo_links import attach, shared_tile
    current = bundle(); plan = defaults(current['analysis'])
    objects = [deepcopy(o) for o in current['analysis']['objects'] if o['kind'] == 'photo']
    for obj, spec in zip(objects, plan['photos']):
        obj.update(style={}, rotation=0, source={'id': obj['id']}, binding={
            'source_crop': [0, .3, 1, .925], 'crop_center': [.5, .5], 'mirror_x': mirror})
        configure(obj, spec)
    attach(objects)
    assert objects[1]['source'] == objects[0]['source']
    assert objects[1]['shared_photo'] == objects[0]['shared_photo']
    yy, xx = np.mgrid[:800, :800]
    rgb = np.stack([xx % 251, yy % 251, np.full_like(xx, 160), np.full_like(xx, 255)], axis=2).astype('uint8')
    source = Image.fromarray(rgb)
    alpha = Image.new('L', source.size)
    ImageDraw.Draw(alpha).rectangle((200, 60, 599, 799), fill=255)
    foreground = source.copy(); foreground.putalpha(alpha)
    tiles = []
    for obj, image in zip(objects, [source, foreground]):
        l, t, r, b = obj['bbox']
        tile = shared_tile(obj, image, (r-l, b-t), 1)
        tile, _ = apply_window(tile, obj['photo_window_spec'])
        layer = Image.new('RGBA', (1000, 1000))
        layer.alpha_composite(tile, (round((l+r-tile.width)/2), round((t+b-tile.height)/2)))
        tiles.append(layer)
    a, b = map(np.asarray, tiles)
    overlap = (a[:,:,3] == 255) & (b[:,:,3] == 255)
    assert overlap.sum() > 10000
    assert np.max(np.abs(a[:,:,:3][overlap].astype(int)-b[:,:,:3][overlap])) <= 1
    assert a[300, 500, 3] == 0 and b[300, 500, 3] == 255  # Only subject extends above the base.
    assert b[300, 220, 3] == 0  # No source background leaks out with it.
    assert not np.any(b[900:,:,3])  # Bottom remains clipped.
