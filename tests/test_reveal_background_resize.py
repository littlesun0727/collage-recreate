from pathlib import Path

import pytest
from PIL import Image

from collage import contracts  # Registers standalone engine modules.
from reveal_chain import layer_zero
from reveal_grouped import resize_mapping_matches, restore


def mapping(source=(592, 1024), target=(1080, 1912)):
    before = [[640, 111, 1030, 486], [615, 70, 1056, 601]]
    after = [[v * source[i % 2] / target[i % 2] for i, v in enumerate(box)] for box in before]
    return before, after


def test_verified_service_resize_normalizes_background_without_changing_download(tmp_path):
    raw = tmp_path / 'layers_aug_00.png'
    Image.new('RGBA', (592, 1024), (40, 80, 120, 255)).save(raw)
    original = raw.read_bytes()
    normalized = layer_zero(tmp_path, (1080, 1912), mapping())
    assert normalized != raw and raw.read_bytes() == original
    with Image.open(normalized) as image:
        assert image.size == (1080, 1912)
        assert image.getpixel((600, 800)) == (40, 80, 120, 255)
    assert layer_zero(tmp_path, (1080, 1912), mapping()).read_bytes() == normalized.read_bytes()


@pytest.mark.parametrize('boxes', [None, ([], []), ([[0, 0, 100, 100]], [[0, 0, 100, 100]]),
                                  ([[0, 0, 100, 100]], [[0, 0, float('nan'), 100]])])
def test_unexplained_aspect_change_is_still_rejected(tmp_path, boxes):
    Image.new('RGB', (592, 1024)).save(tmp_path / 'layers_aug_00.png')
    with pytest.raises(ValueError, match='No usable layer 0'):
        layer_zero(tmp_path, (1080, 1912), boxes)
    assert not list(tmp_path.glob('*-normalized.png'))


def test_normal_proportional_resize_keeps_original_cache_path(tmp_path):
    raw = tmp_path / 'layers_aug_00.png'
    Image.new('RGB', (578, 1024)).save(raw)
    assert layer_zero(tmp_path, (1080, 1912)) == raw


def test_material_and_background_layers_share_the_same_verified_geometry(tmp_path):
    raw = tmp_path / 'layer.png'
    Image.new('RGBA', (592, 1024), (10, 20, 30, 255)).save(raw)
    assert resize_mapping_matches((592, 1024), (1080, 1912), mapping())
    target = tmp_path / 'restored.png'
    restore(raw, [0, 0, 1080, 1912], [1080, 1912], target, mapping())
    with Image.open(target) as image:
        assert image.size == (1080, 1912)
