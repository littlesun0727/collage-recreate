import copy
import pytest
from collage_recreate.core import ToolError, write_json
from collage_recreate.inventory import validate_inventory

def sample():
    kinds = ["photo", "text", "decoration", "shape", "line", "background_candidate", "unknown"]
    return {"version": 1, "objects": [
        {"id": f"obj_{i}", "visual_type": kind, "description": "画面里的独立对象",
         "text_content": "准确文字" if kind == "text" else None, "confidence": "low" if kind == "unknown" else "high"}
        for i, kind in enumerate(kinds)], "questions": ["obj_6：用途不明确"]}

def test_inventory_preserves_unknown_and_source(tmp_path):
    path = tmp_path / "清单.json"
    write_json(path, sample())
    before = path.read_bytes()
    result = validate_inventory(path)
    assert result.objects[-1].visual_type == "unknown"
    assert result.objects[1].text_content == "准确文字"
    assert result.model_dump() == sample()
    assert path.read_bytes() == before

@pytest.mark.parametrize("change", [
    lambda d: d.update(version=True), lambda d: d.update(version="1"),
    lambda d: d.update(version=2), lambda d: d.update(objects=[]),
    lambda d: d["objects"].append(copy.deepcopy(d["objects"][0])),
    lambda d: d["objects"][0].update(id="Bad ID"),
    lambda d: d["objects"][0].update(visual_type="slot"),
    lambda d: d["objects"][0].update(confidence="certain"),
    lambda d: d["objects"][0].update(description="  "),
    lambda d: d["objects"][0].update(text_content=7),
    lambda d: d["objects"][0].update(source_bbox_1000=[0,0,1000,1000]),
    lambda d: d["objects"][0].update(attachment=None),
    lambda d: d.update(questions="unknown"),
    lambda d: d.update(questions=[" "]),
])
def test_invalid_inventory_does_not_rewrite_input(tmp_path, change):
    data = sample()
    change(data)
    path = tmp_path / "input.json"
    write_json(path, data)
    before = path.read_bytes()
    with pytest.raises(ToolError):
        validate_inventory(path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
