from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from collage_recreate.core import Task, apply_patch, initialize, write_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def task_factory(tmp_path):
    def make(name="任务 sample", permissions=None):
        inputs = tmp_path / (name + "-inputs")
        inputs.mkdir()
        reference = Image.new("RGB", (120, 90), "#aabbcc")
        reference.save(inputs / "reference.png")
        photo = Image.new("RGB", (90, 30), "red")
        draw = ImageDraw.Draw(photo)
        draw.rectangle((30, 0, 59, 29), fill="green")
        draw.rectangle((60, 0, 89, 29), fill="blue")
        photo.save(inputs / "photo.png")
        Image.new("RGBA", (30, 30), (255, 0, 0, 128)).save(inputs / "alpha.png")
        req = {
            "reference": "reference.png",
            "canvas": {"width": 160, "height": 120, "background": "#ffffff"},
            "assets": {
                "photo": {"path": "photo.png"}, "alpha": {"path": "alpha.png"},
                "font": {"kind": "font", "path": str(ROOT / "assets/fonts/DejaVuSans.ttf"),
                         "redistributable": True, "license": str(ROOT / "assets/fonts/LICENSE_DEJAVU")},
            },
            "texts": {"title": "Hello"},
            "permissions": permissions or {},
        }
        write_json(inputs / "request.json", req)
        task = Task(tmp_path / name)
        initialize(task, inputs / "request.json")
        return task
    return make


@pytest.fixture
def task(task_factory):
    return task_factory()


def apply_data(task, data):
    path = task.root / "private" / "test-patch.json"
    write_json(path, {"expected_revision": task.load().revision, **data})
    return apply_patch(task, path)
