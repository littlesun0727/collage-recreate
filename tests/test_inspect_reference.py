from pathlib import Path
import json
import subprocess
import sys

from PIL import Image
import pytest

from collage_recreate.core import ToolError, file_hash
from inspect_reference import inspect_reference


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "原图 sample.png"
    image = Image.new("RGBA", (8, 6))
    image.putdata([(x * 20, y * 30, 90, 120 + x) for y in range(6) for x in range(8)])
    image.save(path)
    return path


def test_metadata_leaves_source_and_directory_unchanged(source):
    original = source.read_bytes()
    before = set(source.parent.iterdir())
    result = inspect_reference(source)
    assert result["source_size"] == [8, 6]
    assert result["alpha_range"] == [120, 127]
    assert result["source_sha256"] == file_hash(source)
    assert source.read_bytes() == original
    assert set(source.parent.iterdir()) == before


def test_crop_preserves_pixels_alpha_and_source_coordinates(source):
    original = source.read_bytes()
    output = source.parent / "局部 图" / "detail.png"
    result = inspect_reference(source, crop=[2, 1, 6, 5], output=output)
    assert result["crop_pixels"] == [2, 1, 6, 5]
    assert result["crop_normalized"] == [0.25, 1 / 6, 0.75, 5 / 6]
    assert result["output_size"] == [4, 4]
    with Image.open(output) as actual, Image.open(source) as full:
        assert actual.tobytes() == full.crop((2, 1, 6, 5)).tobytes()
    assert source.read_bytes() == original


@pytest.mark.parametrize("box", [[-1, 0, 3, 3], [0, 0, 9, 3], [3, 0, 2, 3], [0, 2, 3, 2]])
def test_invalid_crop_does_not_write(source, box):
    output = source.parent / "invalid.png"
    with pytest.raises(ToolError) as exc:
        inspect_reference(source, crop=box, output=output)
    assert exc.value.code == "CROP_INVALID"
    assert not output.exists()


def test_source_and_existing_outputs_cannot_be_overwritten(source):
    original = source.read_bytes()
    with pytest.raises(ToolError) as exc:
        inspect_reference(source, crop=[0, 0, 4, 4], output=source)
    assert exc.value.code == "SOURCE_OVERWRITE"
    output = source.parent / "existing.png"
    output.write_bytes(b"keep me")
    with pytest.raises(ToolError) as exc:
        inspect_reference(source, crop=[0, 0, 4, 4], output=output)
    assert exc.value.code == "OUTPUT_EXISTS"
    assert output.read_bytes() == b"keep me"
    assert source.read_bytes() == original


def test_actual_cli_crop_with_unicode_and_spaces(source):
    script = Path(__file__).resolve().parents[1] / "scripts/inspect_reference.py"
    output = source.parent / "CLI 局部.png"
    run = subprocess.run(
        [sys.executable, "-X", "utf8", str(script), "--input", str(source),
         "--crop", "0", "0", "8", "6", "--output", str(output)],
        capture_output=True, encoding="utf-8", check=True,
    )
    result = json.loads(run.stdout)
    assert result["ok"] and result["output_size"] == [8, 6]
    with Image.open(output) as actual, Image.open(source) as original:
        assert actual.tobytes() == original.tobytes()
