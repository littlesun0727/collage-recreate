"""Reference line breaks and typography survive analysis, scaling and drawing."""
from copy import deepcopy
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest
from jsonschema import Draft202012Validator

from collage import prompts
from collage.storage import read, save
from collage.vlm.gpt_schema import project, restore
from collage.workflow.stages import analyze
from text import render_text


def inventory():
    return {'objects': [{'id': 'caption', 'kind': 'text', 'bbox': [100, 100, 900, 800],
                         'text': 'First line\nSecond line\n\nLast line', 'color': '#123456',
                         'font': 'sans', 'align': 'left', 'bold': True, 'italic': False}],
            'layer_order': ['caption']}


@pytest.mark.parametrize('mode', ['pixel', 'normalized_1000'])
def test_analysis_preserves_newlines_and_simple_typography(mode):
    value = inventory(); original = deepcopy(value)
    value = json.loads(json.dumps(value))
    bundle = analyze.compile_inventory(value, [1200, 1600], mode)
    text = bundle['analysis']['objects'][0]
    assert text['text'] == bundle['text_units'][0]['content'] == original['objects'][0]['text']
    assert text['style'] == {'fill': '#123456', 'font': 'sans',
                             'align': 'left', 'bold': True, 'italic': False}
    assert value == original


def test_typography_survives_strict_provider_projection():
    value = inventory()
    schema = analyze.schema([1200, 1600], 'normalized_1000')
    strict = project(schema)
    wire = {'objects': [{k: value['objects'][0].get(k) for k in schema['properties']['objects']['items']['properties']}],
            'layer_order': value['layer_order']}
    Draft202012Validator(strict).validate(wire)
    assert analyze.normalize(restore(wire, schema)) == value


@pytest.mark.parametrize('key,value', [('font', 'unknown'), ('italic', 'false'), ('align', 'justify'), ('bold', 'true')])
def test_invalid_typography_can_be_repaired_without_changing_text_or_geometry(key, value):
    original = inventory(); broken = deepcopy(original)
    broken['objects'][0][key] = value
    plan = analyze.repair_plan(broken, [1200, 1600], 'normalized_1000')
    assert plan['fields'] == {'caption': [key]}
    repaired = analyze.merge_repair(broken, {'updates': {'caption': {key: original['objects'][0][key]}}}, plan)
    assert repaired == original


def test_typography_is_not_accepted_on_photo_objects():
    value = {'objects': [{'id': 'photo', 'kind': 'photo', 'bbox': [0, 0, 100, 100],
                           'description': 'photo', 'font': 'sans'}], 'layer_order': ['photo']}
    with pytest.raises(ValueError, match='typography'):
        analyze.compile_inventory(value, [200, 200], 'pixel')


@pytest.mark.parametrize('mode', ['pixel', 'normalized_1000'])
def test_analysis_and_repair_prompts_explain_text_units_and_line_breaks(mode):
    for name in ('analyze', 'analyze_full_retry', 'analyze_repair'):
        prompt = prompts.render(name, mode=mode)
        assert '\\n' in prompt and all(key in prompt for key in ('font', 'color', 'align', 'bold', 'italic'))
        assert 'font_size' not in prompt and 'line_spacing' not in prompt
        assert '{{' not in prompt
    properties = analyze.schema([1200, 1600], mode)['properties']['objects']['items']['properties']
    assert 'font_size' not in properties and 'line_spacing' not in properties


def ink_rows(tile):
    occupied = np.any(np.asarray(tile.getchannel('A')) > 32, axis=1)
    starts = np.flatnonzero(occupied & ~np.r_[False, occupied[:-1]])
    ends = np.flatnonzero(occupied & ~np.r_[occupied[1:], False]) + 1
    return list(zip(starts, ends))


@pytest.mark.parametrize('size', [(400, 260), (120, 400), (800, 120)])
def test_rendered_lines_autofit_box_with_at_least_single_spacing(size):
    tile, meta = render_text('HELLO\nHELLO\nHELLO', size, {'font': 'sans'})
    rows = ink_rows(tile)
    px = meta['font_size_px']
    assert meta['line_count'] == 3 and meta['line_spacing'] >= 1
    assert meta['line_spacing_mode'] == 'fit_bbox'
    assert meta['line_advance_px'] >= sum(ImageFont.truetype(meta['font'], px).getmetrics())
    assert len(rows) == 3
    assert all(abs(b[0]-a[0]-meta['line_advance_px']) <= 1 for a, b in zip(rows, rows[1:]))
    # One size larger must exceed the width or height: no undersized default.
    bigger = ImageFont.truetype(meta['font'], px+1)
    left, top, right, bottom = ImageDraw.Draw(tile).textbbox((0, 0), 'HELLO', font=bigger, anchor='ls')
    assert right-left > size[0] or bottom-top+2*sum(bigger.getmetrics()) > size[1]


def test_width_limited_paragraph_uses_spare_height_between_lines():
    text = 'Winter paints the world in soft whites,\nturning every breath into a whisper of poetry.'
    size = (394, 79); style = {'font': 'serif', 'italic': True}
    tile, meta = render_text(text, size, style)
    compact, old = render_text(text, size, {**style, 'line_spacing': 1})
    rows, old_rows = ink_rows(tile), ink_rows(compact)
    assert len(rows) == len(old_rows) == 2
    assert meta['font_size_px'] == old['font_size_px']
    assert meta['line_advance_px'] > old['line_advance_px']*2
    assert rows[1][0]-rows[0][1] > old_rows[1][0]-old_rows[0][1]+25
    assert rows[0][0] <= 2 and size[1]-rows[-1][1] <= 2
    assert old['line_spacing_mode'] == 'explicit' and old['line_spacing'] == 1


def test_empty_line_and_windows_newlines_are_preserved():
    tile, meta = render_text('HELLO\r\n\r\nHELLO', (400, 240), {'font': 'sans'})
    rows = ink_rows(tile)
    assert meta['text'] == 'HELLO\n\nHELLO' and meta['line_count'] == 3
    assert len(rows) == 2 and abs(rows[1][0]-rows[0][0]-2*meta['line_advance_px']) <= 1


@pytest.mark.parametrize('align', ['left', 'center', 'right'])
def test_each_line_uses_requested_alignment(align):
    tile, _ = render_text('WWWW\nW', (300, 160),
                          {'font': 'sans', 'align': align})
    edges = []
    for top, bottom in ink_rows(tile):
        left, _, right, _ = tile.crop((0, top, tile.width, bottom)).getchannel('A').getbbox()
        edges.append((left, right))
    assert len(edges) == 2
    if align == 'left': assert abs(edges[0][0]-edges[1][0]) <= 2
    elif align == 'right': assert abs(edges[0][1]-edges[1][1]) <= 2
    else: assert abs(sum(edges[0])-sum(edges[1])) <= 3


def test_reference_paragraph_does_not_shrink_to_a_single_tiny_line():
    text = ('I have only to break into\nthe tightness of strawberry,\n'
            'and i see summer\nits dust and lowering skies.')
    tile, multi = render_text(text, (510, 229),
                              {'font': 'hand', 'align': 'left', 'bold': True})
    _, single = render_text(text.replace('\n', ' '), (510, 229), {'font': 'hand'})
    assert multi['line_count'] == 4 and len(ink_rows(tile)) == 4
    assert multi['font_size_px'] >= 30 and multi['font_size_px'] > single['font_size_px']*2


def test_real_scene_compilation_and_export_scale_keep_text_layout(tmp_path):
    from common import sha
    from prepare import prepare
    from scene import compile_scene
    from render import render
    reference = tmp_path/'reference.png'
    customers = tmp_path/'customers'; customers.mkdir()
    Image.new('RGB', (400, 300), 'white').save(reference)
    Image.new('RGB', (100, 100), 'blue').save(customers/'photo.png')
    run = tmp_path/'task'
    prepare(reference, [customers], run, width=800)
    value = inventory(); value['objects'][0].update(text='FIRST LINE\nSECOND LINE')
    bundle = analyze.compile_inventory(value, [400, 300], 'normalized_1000')
    save(run/'analysis.json', bundle['analysis'])
    save(run/'bindings.json', {'schema_version': 'collage-bindings-v1', 'photos': [], 'texts': []})
    compile_scene(run, {'enabled': False})
    scene = read(run/'scene.json')
    style = scene['objects'][0]['style']
    assert 'font_size' not in style and 'line_spacing' not in style
    bbox = scene['objects'][0]['bbox']
    _, small = render_text(value['objects'][0]['text'], (bbox[2]-bbox[0], bbox[3]-bbox[1]), style)
    render(run)
    meta = read(run/'result.json')['resources'][0]['metadata']
    assert abs(meta['font_size_px']-2*small['font_size_px']) <= 2 and meta['line_count'] == 2
    assert meta['line_spacing'] >= 1 and meta['align'] == 'left'
    digest = sha(run/'final.png')
    render(run, capture_layers=True, editor_export=True)
    assert sha(run/'final.png') == digest
