"""Render linked customer subjects beyond reference bounds without moving them."""
from copy import deepcopy

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import pytest

from collage.storage import read, save
from collage.workflow.stages.analyze import compile_inventory
from common import sha
from photo_windows import defaults
from prepare import prepare
from render import render
from scene import compile_scene
from window_contract import empty_stroke


def prepare_popout(tmp_path, monkeypatch, *, fixed=False, mirror=False, angle=0):
    reference = tmp_path/'reference.png'
    Image.new('RGB', (200, 300), 'white').save(reference)
    customers = tmp_path/'customers'; customers.mkdir()
    yy, xx = np.mgrid[:200, :100]
    source = Image.fromarray(np.stack([xx*2, yy, np.full_like(xx, 120)], axis=2).astype('uint8'))
    source.save(customers/'source.png')
    run = tmp_path/'task'
    prepare(reference, [customers], run, width=200)
    analysis = compile_inventory({'objects': [
        {'id': 'base', 'kind': 'photo', 'bbox': [20, 160, 180, 280],
         'description': 'Retained photo background'},
        {'id': 'front', 'kind': 'photo', 'linked_to': 'base', 'bbox': [80, 130, 120, 170],
         'description': 'Linked subject continues above base'},
    ]}, [200, 300], 'pixel')['analysis']
    analysis['objects'][0]['rotation'] = angle
    save(run/'analysis.json', analysis)
    plan = defaults(analysis)
    plan['photos'][0]['mask'] = {'enabled': True, 'shape': {'type': 'rectangle'}, 'stroke': empty_stroke()}
    if fixed:
        plan['photos'][1]['mask'] = deepcopy(plan['photos'][0]['mask'])
    save(run/'photo-windows.json', plan)
    asset = read(run/'prepared/catalog.json')['assets'][0]
    save(run/'bindings.json', {'schema_version': 'collage-bindings-v1', 'photos': [
        {'slot_id': oid, 'asset_id': asset['id'], 'source_crop': [0, 0, 1, 1],
         'crop_center': [.5, .5], 'mirror_x': mirror, 'reason': 'Use the same customer source'}
        for oid in ('base', 'front')], 'texts': []})
    def cutout(image, *args):
        foreground = image.copy()
        alpha = Image.new('L', image.size)
        ImageDraw.Draw(alpha).rectangle((10, 20, 65, 120), fill=255)
        foreground.putalpha(alpha)
        return foreground
    monkeypatch.setattr('photos.cutout', cutout)
    compile_scene(run, {'enabled': False})
    return run


@pytest.mark.parametrize('fixed', [False, True])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('angle', [0, 17])
def test_popout_keeps_complete_silhouette_and_shared_mapping(tmp_path, monkeypatch, fixed, mirror, angle):
    run = prepare_popout(tmp_path, monkeypatch, fixed=fixed, mirror=mirror, angle=angle)
    protected = {name: sha(run/name) for name in ('analysis.json', 'photo-windows.json', 'bindings.json', 'scene.json')}
    render(run, capture_layers=True)
    result = read(run/'result.json')
    resources = {r['id']: r for r in result['resources']}
    with Image.open(run/'editor_layers/front.png') as image:
        front = np.asarray(image.convert('RGBA'))
    with Image.open(run/'editor_layers/base.png') as image:
        base = np.asarray(image.convert('RGBA'))
    outside = front[:, :, 3] > 16
    outside[128:172, 78:122] = False
    if fixed:
        assert not resources['front']['metadata'].get('canvas_aligned')
        assert not outside.any()
    else:
        assert resources['front']['metadata']['canvas_aligned']
        assert outside.sum() > 1000
        assert np.count_nonzero(front[:128, :, 3] > 16) > 100
        # Empty foreground surroundings must not become another photo background.
        assert front[70, 100, 3] == 0
    # Compare the interior: bicubic RGBA sampling can ring at alpha edges,
    # while the retained photo has no segmentation edge there.
    overlap_alpha = Image.fromarray(np.where((front[:, :, 3] == 255) &
                                             (base[:, :, 3] == 255), 255, 0).astype('uint8'))
    overlap = np.asarray(overlap_alpha.filter(ImageFilter.MinFilter(7))) == 255
    assert overlap.sum() > 50
    delta = np.abs(front[:, :, :3].astype(int)-base[:, :, :3].astype(int))
    assert np.percentile(delta[overlap], 99) <= 2
    assert result['customer_photos_verified']
    assert {name: sha(run/name) for name in protected} == protected
    # Cached tiles and the editor must reproduce the same expanded placement.
    digest = sha(run/'final.png')
    monkeypatch.setattr('photos.cutout', lambda *args: pytest.fail('Cached/editor render repeated cutout'))
    render(run)
    render(run, capture_layers=True, editor_export=True)
    assert sha(run/'final.png') == digest


def test_failed_cutout_fallback_stays_inside_original_photo_box(tmp_path, monkeypatch):
    monkeypatch.setenv('COLLAGE_BEST_EFFORT', '1')
    run = prepare_popout(tmp_path, monkeypatch)
    def fail(*args):
        raise RuntimeError('Segmentation unavailable')
    monkeypatch.setattr('photos.cutout', fail)
    render(run, capture_layers=True)
    front = next(r for r in read(run/'result.json')['resources'] if r['id'] == 'front')
    assert front['metadata']['fallback']
    assert not front['metadata'].get('canvas_aligned')
    with Image.open(run/'editor_layers/front.png') as image:
        assert image.getchannel('A').getbbox() == (80, 130, 120, 170)


def test_expanded_sampling_preserves_subject_stroke_size(monkeypatch):
    from photo_windows import apply_window
    import window_drawing
    spec = defaults(compile_inventory({'objects': [
        {'id': 'photo', 'kind': 'photo', 'bbox': [0, 0, 100, 80], 'description': 'subject'},
    ]}, [200, 300], 'pixel')['analysis'])['photos'][0]
    spec['subject']['preserve_background'] = False
    spec['subject']['stroke'].update(enabled=True, width=.04, placement='outside')
    image = Image.new('RGBA', (200, 300))
    ImageDraw.Draw(image).rectangle((30, 40, 170, 260), fill='red')
    sizes = []
    draw_stroke = window_drawing.stroke_mask
    def capture(boundary, stroke, short):
        sizes.append(short)
        return draw_stroke(boundary, stroke, short)
    monkeypatch.setattr(window_drawing, 'stroke_mask', capture)
    tile, _ = apply_window(image, spec, placement_size=(100, 80))
    assert sizes == [80]
    assert tile.getchannel('A').getbbox()
