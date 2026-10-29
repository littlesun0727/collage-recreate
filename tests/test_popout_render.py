"""Fit linked subjects to their bbox and apply the same mapping to the base."""
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
        {'id': 'bg', 'kind': 'background', 'background_type': 'solid_color',
         'bbox': [0, 0, 200, 300], 'color': '#FFFFFF', 'description': 'White canvas'},
        {'id': 'base', 'kind': 'photo', 'bbox': [20, 160, 180, 280],
         'description': 'Retained photo background'},
        {'id': 'front', 'kind': 'photo', 'linked_to': 'base', 'bbox': [80, 130, 120, 170],
         'description': 'Linked subject continues above base'},
    ]}, [200, 300], 'pixel')['analysis']
    analysis['objects'][1]['rotation'] = angle
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
        assert outside.any()
        assert (front[:, :, 3] > 128).any()
        # Empty foreground surroundings must not become another photo background.
        assert front[0, 0, 3] == 0
    # The entire rotated base interior must contain original photo pixels.
    from photo_links import _tile_to_canvas
    transform = _tile_to_canvas([20,160,180,280], angle)
    corners = transform @ np.array([[0,160,160,0],[0,0,120,120],[1,1,1,1]])
    expected = Image.new('L', (200,300))
    ImageDraw.Draw(expected).polygon([tuple(p) for p in corners[:2].T], fill=255)
    interior = np.asarray(expected.filter(ImageFilter.MinFilter(7))) == 255
    assert (base[:,:,3][interior] == 255).all()
    yy,xx=np.mgrid[:front.shape[0],:front.shape[1]]
    inverse=np.linalg.inv(transform)
    base_y=inverse[1,0]*(xx+.5)+inverse[1,1]*(yy+.5)+inverse[1,2]
    assert not front[:,:,3][base_y > 122].any()
    # Compare the interior: bicubic RGBA sampling can ring at alpha edges,
    # while the retained photo has no segmentation edge there.
    overlap_alpha = Image.fromarray(np.where((front[:, :, 3] == 255) &
                                             (base[:, :, 3] == 255), 255, 0).astype('uint8'))
    overlap = np.asarray(overlap_alpha.filter(ImageFilter.MinFilter(7))) == 255
    assert overlap.sum() > 20
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


def test_live_linked_layers_use_the_same_subject_anchor_as_static(tmp_path, monkeypatch):
    from motion_render import fixed_photo_layer
    from scene import load_scene
    import photos
    from common import verify_source
    run = prepare_popout(tmp_path, monkeypatch, mirror=True)
    render(run, capture_layers=True)
    scene = load_scene(run)
    objects = {o['id']:o for o in scene['objects']}
    with Image.open(verify_source(objects['base']['source'])) as raw:
        source = raw.convert('RGBA')
    foreground = photos.cutout(source)
    bounds = foreground.getchannel('A').getbbox()
    for oid, image in [('base',source),('front',foreground)]:
        live = fixed_photo_layer(objects[oid], image, bounds, scene)
        with Image.open(run/f'editor_layers/{oid}.png') as raw:
            static = raw.convert('RGBA')
        assert live.getchannel('A').getbbox() == static.getchannel('A').getbbox()
        mask = (np.asarray(live)[:,:,3]==255) & (np.asarray(static)[:,:,3]==255)
        delta = np.abs(np.asarray(live).astype(int)-np.asarray(static).astype(int))
        assert np.percentile(delta[mask],99) <= 2


@pytest.mark.parametrize('angle', [0,17,-25])
def test_shared_bottom_clip_is_generic_and_runs_after_stroke(angle):
    from photo_links import clip_linked_bottom, _tile_to_canvas
    obj={'bbox':[0,0,200,200], 'rotation':0, 'mode':'cutout',
         'shared_photo':{'bbox':[40,60,160,140], 'rotation':angle}}
    # Simulate a foreground and its already-rendered outline spanning the canvas.
    image=Image.new('RGBA',(200,200),'white')
    content=Image.new('L',(200,200),255)
    result,mask=clip_linked_bottom(obj,image,1,content)
    yy,xx=np.mgrid[:200,:200]
    inverse=np.linalg.inv(_tile_to_canvas([40,60,160,140],angle))
    base_y=inverse[1,0]*(xx+.5)+inverse[1,1]*(yy+.5)+inverse[1,2]
    alpha=np.asarray(result.getchannel('A'))
    assert not alpha[base_y>81].any()
    assert (alpha[base_y<79]==255).all()
    assert np.array_equal(alpha,np.asarray(mask))
    assert result.getpixel((100,20))[3] == 255  # top pop-out stays visible
    assert image.getchannel('A').getextrema() == (255,255)


def test_fully_bottom_clipped_subject_does_not_fall_back_to_a_photo(tmp_path, monkeypatch):
    run=prepare_popout(tmp_path,monkeypatch)
    def fully_clipped(obj,image,scale,content_alpha=None):
        return Image.new('RGBA',image.size), Image.new('L',image.size)
    monkeypatch.setattr('photo_links.clip_linked_bottom',fully_clipped)
    monkeypatch.setenv('COLLAGE_BEST_EFFORT','1')
    render(run,capture_layers=True)
    resource=next(r for r in read(run/'result.json')['resources'] if r['id']=='front')
    assert resource['metadata']['clipped_by_base_bottom']
    assert not resource['metadata'].get('fallback')
    with Image.open(run/'editor_layers/front.png') as image:
        assert image.getchannel('A').getbbox() is None


def test_fill_can_translate_down_without_enlarging_subject():
    from photo_links import shared_tile
    root = {'bbox':[0,40,100,140], 'rotation':0,
            'binding':{'source_crop':[0,0,1,1]},
            'subject_anchor':{'bbox':[20,0,80,60], 'rotation':0},
            'subject_bounds':[.2,.6,.8,.9]}
    image = Image.new('RGBA',(100,200))
    ImageDraw.Draw(image).rectangle((20,120,79,179), fill='red')
    obj = {'bbox':[0,0,160,200], 'rotation':0, 'shared_photo':root}
    foreground = shared_tile(obj,image,(160,200),1)
    assert foreground.getchannel('A').getbbox() == (20,60,80,120)
    base = shared_tile({'bbox':root['bbox'],'rotation':0,'shared_photo':root},
                       Image.new('RGBA',(100,200),'blue'),(100,100),1)
    assert base.getchannel('A').getextrema() == (255,255)


def test_fill_uses_pixels_outside_declared_crop_before_scaling():
    from photo_links import shared_tile
    root = {'bbox':[0,0,100,100], 'rotation':0,
            'binding':{'source_crop':[.4,.4,.6,.6]},
            'subject_anchor':{'bbox':[40,40,60,60], 'rotation':0},
            'subject_bounds':[.4,.4,.6,.6]}
    obj = {'bbox':[0,0,100,100],'rotation':0,'shared_photo':root}
    image = Image.new('RGBA',(100,100),'blue')
    assert shared_tile(obj,image,(100,100),1).tobytes() == image.tobytes()


def test_expanded_sampling_preserves_subject_stroke_size(monkeypatch):
    from photo_windows import apply_window
    import window_drawing
    spec = defaults(compile_inventory({'objects': [
        {'id': 'bg', 'kind': 'background', 'background_type': 'solid_color',
         'bbox': [0, 0, 100, 80], 'color': '#FFFFFF', 'description': 'White canvas'},
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
