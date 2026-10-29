import numpy as np
from PIL import Image, ImageDraw
from collage.decomposition.geometry import clean_layer


def test_remote_speckle_removed_without_trimming_soft_edges():
    im = Image.new('RGBA', (100, 100))
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, 29, 29), fill=(255, 0, 0, 255))
    d.line((9, 10, 9, 29), fill=(255, 0, 0, 1))
    d.point((10, 90), fill=(255, 0, 0, 1))
    cleaned, report = clean_layer(im, [100, 100, 300, 300], [1000, 1000])
    assert cleaned.getchannel('A').getbbox() == (9, 10, 30, 30)
    assert cleaned.getpixel((9, 10))[3] == 1
    assert im.getpixel((10, 90))[3] == 1
    assert len(report['removed_components']) == 1


def test_multiple_letters_and_detached_local_detail_are_preserved():
    im = Image.new('RGBA', (100, 100))
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, 14, 30), fill='white')
    d.rectangle((20, 10, 24, 30), fill='white')
    d.point((30, 20), fill='white')
    cleaned, report = clean_layer(im, [10, 10, 31, 31], [100, 100])
    assert np.array_equal(np.array(im), np.array(cleaned))
    assert not report['removed_components']


def test_large_remote_region_retained_for_review():
    im = Image.new('RGBA', (100, 100))
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, 29, 29), fill='white')
    d.rectangle((10, 80, 29, 99), fill='white')
    cleaned, report = clean_layer(im, [10, 10, 30, 30], [100, 100])
    assert np.array_equal(np.array(im), np.array(cleaned))
    assert 'substantial_detached_region_outside_source_bbox' in report['review_reasons']


def test_no_anchor_or_faint_subject_is_not_erased():
    for alpha in (1, 255):
        im = Image.new('RGBA', (100, 100))
        ImageDraw.Draw(im).rectangle((70, 70, 79, 79), fill=(1, 2, 3, alpha))
        cleaned, report = clean_layer(im, [10, 10, 30, 30], [100, 100])
        assert np.array_equal(np.array(im), np.array(cleaned))
        assert report['review_reasons'] == ['no_confident_region_near_source_bbox']


def test_connected_breakout_is_not_clipped_to_source_box():
    im = Image.new('RGBA', (100, 100))
    ImageDraw.Draw(im).rectangle((10, 10, 29, 70), fill='white')
    cleaned, report = clean_layer(im, [10, 10, 30, 30], [100, 100])
    assert cleaned.getchannel('A').getbbox() == (10, 10, 30, 71)
    assert 'connected_region_extends_beyond_source_bbox' in report['review_reasons']
