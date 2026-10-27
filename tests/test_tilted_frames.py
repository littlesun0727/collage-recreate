from copy import deepcopy
import math
import numpy as np

import pytest
from PIL import Image

from frame_fallback import align_frames, draw_frame
from photo_windows import canvas_mask
from collage.storage import read, save
from collage.config import Config
from collage.workflow.stages.bind import read_structure, context_for


@pytest.mark.parametrize('angle', [0, 8, -17, 45])
def test_asymmetric_frame_and_photo_share_the_same_transform(angle):
    photo = {'id':'p', 'kind':'photo', 'bbox':[100,100,300,400], 'rotation':angle}
    frame = {'id':'f', 'kind':'frame', 'photo_id':'p', 'bbox':[40,40,450,480],
             'style':{'border':{'top':.04,'right':.06,'bottom':.18,'left':.03}, 'fill':'#FFFFFF'}}
    scene = {'objects':[photo, frame]}
    original = deepcopy(frame)
    align_frames(scene)
    l,t,r,b = frame['bbox']; cx,cy = (l+r)/2,(t+b)/2
    c,s = math.cos(math.radians(angle)),math.sin(math.radians(angle))
    # Local aperture center is left/up of asymmetric outer paper center.
    dx,dy = (6-12)/2,(8-36)/2
    assert cx+c*dx-s*dy == pytest.approx(200)
    assert cy+s*dx+c*dy == pytest.approx(250)
    assert frame['rotation'] == angle
    assert r-l == pytest.approx(218)
    assert b-t == pytest.approx(344)
    once = deepcopy(frame)
    align_frames(scene)
    assert frame == once
    # Returning to extracted pixels restores their original placement.
    frame['recovered'] = {'file':'extracted.png'}
    align_frames(scene)
    assert frame['bbox'] == original['bbox']
    assert 'rotation' not in frame and 'fallback_original_geometry' not in frame


@pytest.mark.parametrize('angle', [0, 8, -17])
def test_fallback_paper_covers_photo_and_has_no_remote_horizontal_corners(angle):
    from PIL import ImageChops
    photo = {'id':'p','kind':'photo','bbox':[140,120,340,420],'rotation':angle}
    frame = {'id':'f','kind':'frame','photo_id':'p','bbox':[20,20,490,490],
             'style':{'fill':'#FFFFFF','border':{'top':.04,'right':.04,'bottom':.15,'left':.04}}}
    scene = {'objects':[photo,frame]}; align_frames(scene)
    l,t,r,b = frame['bbox']
    tile,_ = draw_frame(frame, (round(r-l),round(b-t)), Image.new('RGB',(500,550)), [photo])
    tile = tile.rotate(-angle, Image.Resampling.BICUBIC, expand=True)
    outer = Image.new('L',(500,550))
    outer.paste(tile.getchannel('A'),(round((l+r-tile.width)/2),round((t+b-tile.height)/2)))
    aperture = canvas_mask((500,550),photo['bbox'],angle,{'shape':{'type':'rectangle'}})
    missing = ImageChops.subtract(aperture,outer)
    assert np.asarray(missing).sum() / (200*300*255) < .001
    ring = ImageChops.multiply(outer, ImageChops.invert(aperture))
    assert ring.getpixel((240,270)) == 0
    assert outer.getpixel((25,25)) == 0


def test_binding_uses_final_pixel_geometry_but_keeps_inventory_description(tmp_path):
    inventory = {'coordinate_mode':'normalized_1000','objects':[
        {'id':'p','kind':'photo','bbox':[100,100,900,900],'description':'portrait'}]}
    save(tmp_path/'structure.json',inventory)
    save(tmp_path/'input.json',{'reference_size':[900,1800]})
    save(tmp_path/'analysis.json',{'objects':[
        {'id':'p','kind':'photo','bbox':[100,200,500,700],'rotation':-8,'description':'engine label'}]})
    from photo_windows import defaults
    structure = read_structure(tmp_path,Config())
    context = context_for({'reference_size':[900,1800],'catalog':[]},structure,defaults(structure))
    slot = context['photo_slots'][0]
    assert slot['bbox'] == [100,200,500,700]
    assert slot['rotation'] == -8 and slot['aspect_ratio'] == .8
    assert slot['description'] == 'portrait'
    assert read(tmp_path/'structure.json') == inventory
