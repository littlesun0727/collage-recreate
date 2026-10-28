import hashlib
import io
import json
import zipfile

from PIL import Image
import pytest

from import_cutout_results import validate


def bundle(tmp_path, mask):
    source = Image.new('RGB', (4, 3), (42, 80, 130))
    stream = io.BytesIO()
    source.save(stream, format='PNG')
    raw = stream.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    manifest = {'items': [{'filename': 'photo.png', 'source_sha256': digest, 'width': 4, 'height': 3}]}
    inputs = tmp_path / 'inputs.zip'
    with zipfile.ZipFile(inputs, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        z.writestr('images/photo.png', raw)
    stream = io.BytesIO()
    mask.save(stream, format='PNG')
    data = stream.getvalue()
    report = {'input_zip_sha256': hashlib.sha256(inputs.read_bytes()).hexdigest(),
              'model_sha256': 'model', 'items': [{'source_sha256': digest, 'mask_sha256': hashlib.sha256(data).hexdigest()}]}
    results = tmp_path / 'results.zip'
    with zipfile.ZipFile(results, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest))
        z.writestr('processing-report.json', json.dumps(report))
        z.writestr('masks/photo.png', data)
    return results, inputs, digest


def test_soft_mask_preserves_original_rgb(tmp_path):
    results, inputs, digest = bundle(tmp_path, Image.new('L', (4, 3), 127))
    accepted, rejected, _ = validate(results, inputs)
    with Image.open(io.BytesIO(accepted[digest])) as image:
        assert image.getpixel((0, 0)) == (42, 80, 130, 127)
    assert rejected == []


def test_empty_mask_is_rejected(tmp_path):
    results, inputs, digest = bundle(tmp_path, Image.new('L', (4, 3), 0))
    accepted, rejected, _ = validate(results, inputs)
    assert accepted == {}
    assert rejected[0]['source_sha256'] == digest


def test_resized_mask_stops_import(tmp_path):
    results, inputs, _ = bundle(tmp_path, Image.new('L', (2, 3), 255))
    with pytest.raises(ValueError, match='full-size'):
        validate(results, inputs)


def test_archive_checksum_stops_import(tmp_path):
    results, inputs, _ = bundle(tmp_path, Image.new('L', (4, 3), 255))
    with inputs.open('ab') as f:
        f.write(b'changed')
    with pytest.raises(ValueError, match='checksum'):
        validate(results, inputs)
