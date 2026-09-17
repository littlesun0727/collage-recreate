from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys

from PIL import Image
import pytest

from collage_recreate.analysis import Draft, check_draft, preview_draft, validate_draft
from collage_recreate.core import ToolError, file_hash, write_json

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def draft_case(tmp_path):
    reference = tmp_path / "原图 sample.png"
    im = Image.new("RGBA", (240, 160))
    im.putdata([(x, y, 90, 180 + x % 70) for y in range(160) for x in range(240)])
    im.save(reference)
    path = tmp_path / "制作草案" / "draft.json"
    data = {
        "background": {"mode": "fixed", "kind": "texture", "background_brief": "保留虚构纸张底板，清除旧照片与文字。", "review_notes": ""},
        "slots": [
            {"id": "photo_a", "label": "照片", "mode": "photo",
             "source_rect": [20, 20, 80, 90],
             "upload_hint": "提供照片", "review_notes": ""},
        ],
        "texts": [
            {"id": "caption", "label": "文案",
             "source_rect": [120, 30, 100, 50],
             "style_brief": "", "review_notes": "", "default_text": "“测试” Ta\n下一行"},
        ],
        "overlays": [
            {"id": "frame", "label": "边框", "source_rect": [15, 15, 90, 100],
             "action": "reference_generate",
             "generation_brief": "虚构手绘纸边，不含照片。", "requires_exact_content": False,
             "review_notes": "", "attachment": {"slot_id": "photo_a", "position": "above"}},
            {"id": "accent", "label": "几何装饰", "source_rect": [170, 100, 40, 40],
             "action": "basic_shape", "generation_brief": "",
             "requires_exact_content": False, "review_notes": "", "attachment": None,
             "shape": {"kind": "ellipse", "fill": "#ff990080", "outline": None, "width": 0}},
        ],
        "layer_order": [{"type": "background"}, {"type": "slot", "id": "photo_a"},
                        {"type": "overlay", "id": "accent"}, {"type": "text", "id": "caption"}],
        "questions": [],
    }
    write_json(path, data)
    return reference, path, data


def save_check(case):
    reference, path, data = case
    write_json(path, data)
    return check_draft(path, reference)


def test_check_program_records_source_and_expands_attachment(draft_case):
    reference, path, data = draft_case
    before = (reference.read_bytes(), path.read_bytes())
    result = check_draft(path, reference)
    assert result["source"] == {"path": str(reference.resolve()), "sha256": file_hash(reference), "width": 240, "height": 160}
    assert result["draft_sha256"] == file_hash(path)
    assert result["visual_status"] == "unreviewed"
    assert result["expanded_layer_order"] == [
        {"type": "background"}, {"type": "slot", "id": "photo_a"},
        {"type": "overlay", "id": "frame"}, {"type": "overlay", "id": "accent"},
        {"type": "text", "id": "caption"}]
    assert (reference.read_bytes(), path.read_bytes()) == before


def test_below_and_above_attachments_keep_array_order(draft_case):
    data = draft_case[2]
    for name in ("shadow_a", "shadow_b"):
        extra = deepcopy(data["overlays"][0])
        extra.update(id=name, attachment={"slot_id": "photo_a", "position": "below"})
        data["overlays"].append(extra)
    result = save_check(draft_case)
    assert [l.get("id") for l in result["expanded_layer_order"]] == [
        None, "shadow_a", "shadow_b", "photo_a", "frame", "accent", "caption"]


def background_slot(data):
    slot = deepcopy(data["slots"][0])
    slot.update(id="background_photo", source_rect=[0, 0, 240, 160])
    data["slots"].insert(0, slot)
    data["background"] = {"mode": "slot", "kind": "photo", "slot_id": slot["id"], "review_notes": "客户已明确替换背景"}
    data["layer_order"][0] = {"type": "slot", "id": slot["id"]}


def test_customer_background_uses_one_bottom_photo(draft_case):
    background_slot(draft_case[2])
    assert save_check(draft_case)["expanded_layer_order"][0] == {"type": "slot", "id": "background_photo"}


@pytest.mark.parametrize("change", ["size", "mode", "missing", "stack", "attached"])
def test_rejects_invalid_background(draft_case, change):
    data = draft_case[2]
    background_slot(data)
    if change == "size":
        data["slots"][0]["source_rect"][2] -= 1
    elif change == "mode":
        data["slots"][0]["mode"] = "cutout"
    elif change == "missing":
        data["background"]["slot_id"] = "absent"
    elif change == "stack":
        data["layer_order"].reverse()
    elif change == "attached":
        data["overlays"][0]["attachment"]["slot_id"] = "background_photo"
    with pytest.raises(ToolError) as error:
        save_check(draft_case)
    assert error.value.code in ("BACKGROUND_INVALID", "ATTACHMENT_INVALID")


@pytest.mark.parametrize("target", ["caption", "absent", "frame"])
def test_attachment_only_to_photo(draft_case, target):
    draft_case[2]["overlays"][0]["attachment"]["slot_id"] = target
    with pytest.raises(ToolError) as error:
        save_check(draft_case)
    assert error.value.code == "ATTACHMENT_INVALID"


@pytest.mark.parametrize("change", ["duplicate", "missing", "attached", "unknown", "wrong_type", "background_top"])
def test_layer_order_must_cover_independent_items_once(draft_case, change):
    order = draft_case[2]["layer_order"]
    if change == "duplicate":
        order.append(order[1].copy())
    elif change == "missing":
        order.pop()
    elif change == "attached":
        order.append({"type": "overlay", "id": "frame"})
    elif change == "unknown":
        order.append({"type": "overlay", "id": "absent"})
    elif change == "wrong_type":
        order[1]["type"] = "overlay"
    else:
        order.reverse()
    with pytest.raises(ToolError) as error:
        save_check(draft_case)
    assert error.value.code in ("LAYER_ORDER_INVALID", "BACKGROUND_INVALID")


@pytest.mark.parametrize("rect", [[20, 10, 0, 20], [-1, 0, 20, 20], [200, 20, 80, 90], [20, 100, 80, 90]])
def test_xywh_bounds_catches_extent_not_just_right_edge(draft_case, rect):
    draft_case[2]["slots"][0]["source_rect"] = rect
    with pytest.raises(ToolError) as error:
        save_check(draft_case)
    assert error.value.code == "RECT_INVALID"


@pytest.mark.parametrize("change", ["relation_graph", "duplicate_id", "image_mode_null", "text_mode_photo", "wrong_rect_type", "blank_question"])
def test_reject_invalid_draft_fields(draft_case, change):
    data = draft_case[2]
    if change == "relation_graph":
        data["relations"] = []
    elif change == "duplicate_id":
        data["overlays"][0]["id"] = "photo_a"
    elif change == "image_mode_null":
        data["slots"][0]["mode"] = None
    elif change == "text_mode_photo":
        data["texts"][0]["mode"] = "photo"
    elif change == "wrong_rect_type":
        data["slots"][0]["source_rect"][0] = "20"
    else:
        data["questions"] = ["  "]
    with pytest.raises(ToolError):
        save_check(draft_case)


@pytest.mark.parametrize("change", ["missing_shape", "bad_color", "missing_radius", "missing_dash", "irrelevant_radius", "empty_brief", "generated_shape"])
def test_action_visual_parameters(draft_case, change):
    generated, basic = draft_case[2]["overlays"]
    if change == "missing_shape":
        basic.pop("shape")
    elif change == "bad_color":
        basic["shape"]["fill"] = "not-a-color"
    elif change == "missing_radius":
        basic["shape"]["kind"] = "rounded_rectangle"
    elif change == "missing_dash":
        basic["shape"]["kind"] = "dashed_rectangle"
    elif change == "irrelevant_radius":
        basic["shape"]["radius"] = 4
    elif change == "empty_brief":
        generated["generation_brief"] = " "
    else:
        generated["shape"] = basic["shape"]
    with pytest.raises(ToolError):
        save_check(draft_case)


def test_unknown_content_can_remain_question(draft_case):
    data = draft_case[2]
    data["slots"][0]["mode"] = "unknown"
    data["texts"][0]["default_text"] = None
    data["questions"] = ["照片边缘处理待确认；文案不清楚，需客户提供原文。"]
    assert save_check(draft_case)["counts"]["questions"] == 1


def test_preview_binds_json_reference_and_preserves_inputs(draft_case, tmp_path):
    reference, path, data = draft_case
    unusual = '</script><img onerror="bad()"> __SOURCE__ __PAYLOAD__ __HASH__ & 测试'
    data["texts"][0]["default_text"] = unusual
    write_json(path, data)
    before = (path.read_bytes(), reference.read_bytes())
    result = preview_draft(path, reference, tmp_path / "review")
    html = Path(result["preview"]).read_text(encoding="utf-8")
    embedded = json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S)[1])
    assert embedded["draft"] == Draft.model_validate(data).model_dump()
    assert unusual not in html
    manifest = json.loads(Path(result["validation"]).read_text(encoding="utf-8"))
    assert embedded["source"] == manifest["source"]
    assert embedded["expanded_layer_order"] == manifest["expanded_layer_order"]
    assert manifest["element_numbers"] == {"1": "photo_a", "2": "caption", "3": "frame", "4": "accent"}
    with Image.open(result["numbered"]) as im:
        assert im.size == (240, 160)
        assert im.getpixel((99, 109))[:3] == (225, 29, 72)  # photo xywh extent
    assert (path.read_bytes(), reference.read_bytes()) == before
    with pytest.raises(ToolError) as error:
        preview_draft(path, reference, tmp_path / "review")
    assert error.value.code == "OUTPUT_EXISTS"


def test_invalid_draft_creates_no_preview(draft_case, tmp_path):
    draft_case[2]["layer_order"] = []
    write_json(draft_case[1], draft_case[2])
    output = tmp_path / "invalid-review"
    with pytest.raises(ToolError):
        preview_draft(draft_case[1], draft_case[0], output)
    assert not output.exists()


def test_cli_structured_success_and_failure(draft_case, tmp_path):
    reference, path, data = draft_case
    argv = [sys.executable, str(ROOT / "scripts/review_analysis.py"), "preview",
            "--reference", str(reference), "--input", str(path), "--output", str(tmp_path / "cli-preview")]
    success = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    assert success.returncode == 0
    assert json.loads(success.stdout)["visual_status"] == "unreviewed"
    data["layer_order"].pop()
    write_json(path, data)
    failure = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    assert failure.returncode == 1
    assert json.loads(failure.stdout)["error"]["code"] == "LAYER_ORDER_INVALID"


def test_fixed_background_only_is_valid(tmp_path):
    ref = tmp_path / "reference.png"
    Image.new("RGB", (60, 40)).save(ref)
    path = tmp_path / "draft.json"
    write_json(path, {"background": {"mode": "fixed", "kind": "texture", "background_brief": "整体固定底板", "review_notes": ""},
                     "slots": [], "texts": [], "overlays": [], "layer_order": [{"type": "background"}], "questions": []})
    assert check_draft(path, ref)["counts"] == {"slots": 0, "texts": 0, "overlays": 0, "questions": 0}
def test_photo_background_requires_explicit_preserve_reason(draft_case):
    data=draft_case[2]
    data["background"]["kind"]="photo"
    with pytest.raises(ToolError):
        save_check(draft_case)
    data["background"]["preserve_reason"]="用户明确要求保留原摄影背景"
    assert save_check(draft_case)["ok"]


@pytest.mark.parametrize("change", ["target_rect", "image_text", "duplicate_text_id", "bad_color"])
def test_new_draft_keeps_images_texts_and_reference_geometry_separate(draft_case, change):
    data=draft_case[2]
    if change=="target_rect":
        data["slots"][0]["target_rect"]=[20,20,80,90]
    elif change=="image_text":
        data["slots"][0]["default_text"]="not an image"
    elif change=="duplicate_text_id":
        data["texts"][0]["id"]=data["slots"][0]["id"]
    else:
        data["overlays"][0]["locate_colors"]=["not-a-color"]
    with pytest.raises(ToolError):
        save_check(draft_case)


def test_basic_shape_cannot_claim_to_render_a_label(draft_case):
    data=draft_case[2]
    data["overlays"][1]["text_content"]="LABEL"
    with pytest.raises(ToolError, match="not embedded text"):
        save_check(draft_case)
