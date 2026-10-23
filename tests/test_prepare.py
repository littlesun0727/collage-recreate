"""Prepared material IDs stay consistent across deduplication, VLM input and Live media."""
from pathlib import Path

from PIL import Image
import pytest

from collage.config import Config
from collage.storage import file_sha, read
from collage.workflow.pipeline import Workflow
from live_media import encode_frames, prepare_live
from prepare import prepare


@pytest.mark.parametrize('count', [2, 101])
def test_material_ids_survive_duplicates_invalid_files_and_reordered_roots(tmp_path, count):
    reference = tmp_path/'reference.png'
    Image.new('RGB', (64, 48), 'white').save(reference)
    roots = [tmp_path/'a', tmp_path/'b']
    for root in roots:
        root.mkdir()
    originals = []
    for index in range(count):
        # The same filename in different folders must still identify distinct images.
        path = roots[index % 2]/f'{index // 2:03d}.png'
        Image.new('RGB', (32, 24), (index, 255-index, 100)).save(path)
        originals.append(path)
    (roots[0]/'zzz-duplicate.png').write_bytes(originals[0].read_bytes())
    invalid = roots[0]/'000-invalid.png'
    invalid.write_bytes(b'not an image')
    source_hashes = {str(path): file_sha(path) for path in originals}
    catalogs = []
    for index, inputs in enumerate((roots, roots[::-1])):
        run = tmp_path/f'run-{index}'
        result = prepare(reference, inputs, run, width=128)
        catalog = read(run/'prepared/catalog.json')['assets']
        catalogs.append(catalog)
        assert result['asset_count'] == count
        assert [warning['file'] for warning in result['warnings']] == [str(invalid)]
        assert [asset['id'] for asset in catalog] == [f'material_{n:02d}' for n in range(count)]
        assert [asset['file'] for asset in catalog] == [str(path) for path in sorted(originals)]
        assert {asset['file']: asset['sha256'] for asset in catalog} == source_hashes
        assert all(Path(asset['thumbnail']).is_file() for asset in catalog)
        assert all(Path(asset['thumbnail']).stem == asset['id'] for asset in catalog)
    assert [(a['id'], a['sha256']) for a in catalogs[0]] == [(a['id'], a['sha256']) for a in catalogs[1]]
    assert {str(path): file_sha(path) for path in originals} == source_hashes

    runner = Workflow(run, config=Config(coordinate_mode='normalized_1000'))
    images = runner.material_images()
    singles = images[-count:]
    assert [path.stem for _, path in singles] == [asset['id'] for asset in catalog]
    assert all(label.endswith(path.stem) and path.is_file() for label, path in singles)


def test_live_posters_use_catalog_ids_with_mixed_photos_and_duplicate_videos(tmp_path):
    pytest.importorskip('av')
    reference = tmp_path/'reference.png'
    Image.new('RGB', (64, 48), 'white').save(reference)
    materials = tmp_path/'materials'
    materials.mkdir()
    Image.new('RGB', (32, 24), 'green').save(materials/'photo.png')
    for index, color in enumerate(('red', 'blue')):
        frame = Image.new('RGB', (32, 24), color)
        encode_frames(materials/f'video-{index}.mp4', frame.size, [0, 500_000],
                      1_000_000, [frame, frame], validate=False)
    (materials/'video-duplicate.mov').write_bytes((materials/'video-0.mp4').read_bytes())

    run = tmp_path/'run'
    result = prepare_live(reference, [materials], run, width=128)
    assets = read(run/'prepared/catalog.json')['assets']
    videos = read(run/'media/manifest.json')['videos']
    by_id = {asset['id']: asset for asset in assets}
    assert result['asset_count'] == 3
    assert result['live_count'] == len(videos) == 2
    assert set(by_id) == {'material_00', 'material_01', 'material_02'}
    assert len({video['asset_id'] for video in videos}) == 2
    for video in videos:
        asset = by_id[video['asset_id']]
        assert asset['sha256'] == video['poster']['sha256'] == file_sha(video['poster']['file'])
        assert asset['file'] == video['poster']['file']
