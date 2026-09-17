from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageChops
import pytest

from collage_recreate.core import ToolError, load_image, parse, write_json
from collage_recreate.models import Project
from collage_recreate.render import compose, fit_image, render
from conftest import apply_data


def picture(**overrides):
    return {"id": "photo", "type": "image", "asset": "photo",
            "x": 10, "y": 20, "width": 60, "height": 60, **overrides}


def title(**overrides):
    return {"id": "title", "type": "text", "text": "title", "font": "font",
            "x": 5, "y": 2, "width": 140, "height": 18, "font_size": 14, **overrides}


def test_cover_focus_and_contain_keep_aspect(task):
    image = load_image(task.asset_path(task.load().assets["photo"]))
    for focus, color in [((0, 0.5), (255, 0, 0)), ((0.5, 0.5), (0, 128, 0)), ((1, 0.5), (0, 0, 255))]:
        fitted = fit_image(image, (30, 30), "cover", focus)
        assert fitted.getpixel((15, 15))[:3] == color
    contained = fit_image(image, (90, 90), "contain")
    assert contained.getbbox() == (0, 30, 90, 60)
    cropped = fit_image(image, (30, 30), crop=[2/3, 0, 1, 1])
    assert cropped.getpixel((15, 15))[:3] == (0, 0, 255)


def test_vertical_source_fits_without_stretch():
    image = Image.new("RGBA", (20, 80), "red")
    assert fit_image(image, (80, 80), "contain").getbbox() == (30, 0, 50, 80)
    assert fit_image(image, (80, 20), "cover").size == (80, 20)


def test_alpha_and_layer_order_match_known_pixels(task):
    apply_data(task, {"elements": [picture(asset="alpha", width=30, height=30)]})
    canvas, _ = compose(task)
    assert canvas.getpixel((15, 25)) == (255, 127, 127, 255)
    apply_data(task, {"elements": [picture(asset="alpha", width=30, height=30), picture(id="above", focus=[1.0, 0.5], width=30, height=30)]})
    canvas, _ = compose(task)
    assert canvas.getpixel((15, 25)) == (0, 0, 255, 255)


def test_rotation_has_no_hidden_color_halo(task):
    image = Image.new("RGBA", (20, 20), (0, 255, 0, 0))
    for x in range(5, 15):
        for y in range(5, 15):
            image.putpixel((x, y), (255, 0, 0, 255))
    path = task.root / "private" / "edge.png"
    image.save(path)
    apply_data(task, {"assets": {"edge": {"path": str(path)}},
                     "canvas": {"width": 160, "height": 120, "background": "#00000000"},
                     "elements": [picture(asset="edge", rotation=23, x=40, y=30, width=40, height=40)]})
    canvas, _ = compose(task)
    visible = [p for p in canvas.get_flattened_data() if p[3] > 20]
    assert visible and max(p[1] for p in visible) == 0
    assert min(p[0] for p in visible) >= 250


def test_group_move_preserves_photo_label_relationship(task):
    apply_data(task, {"groups": [{"id": "card"}], "elements": [
        picture(group="card"), title(group="card", y=90),
    ]})
    before, old_bounds = compose(task)
    apply_data(task, {"group_updates": {"card": {"dx": 5, "dy": 3}}})
    after, new_bounds = compose(task)
    for key in old_bounds:
        assert new_bounds[key] == [old_bounds[key][0]+5, old_bounds[key][1]+3, old_bounds[key][2]+5, old_bounds[key][3]+3]
    assert before.crop((10, 20, 70, 80)).tobytes() == after.crop((15, 23, 75, 83)).tobytes()


def test_replace_photo_and_edit_text_leave_other_pixels_unchanged(task):
    apply_data(task, {"elements": [picture(), title()]})
    before, _ = compose(task)
    source = task.root / "private" / "replacement.png"
    Image.new("RGB", (30, 60), "yellow").save(source)
    font_sha = task.load().assets["font"].sha256
    apply_data(task, {"assets": {"photo": {"path": str(source)}}})
    swapped, _ = compose(task)
    assert swapped.crop((0, 0, 160, 20)).tobytes() == before.crop((0, 0, 160, 20)).tobytes()
    assert swapped.getpixel((40, 45)) == (255, 255, 0, 255)
    apply_data(task, {"texts": {"title": "New text"}})
    edited, _ = compose(task)
    assert edited.crop((0, 20, 160, 120)).tobytes() == swapped.crop((0, 20, 160, 120)).tobytes()
    assert task.load().assets["font"].sha256 == font_sha
    assert not (task.root / ".state/requests.json").exists()


def test_overflow_and_bad_revisions_preserve_project(task):
    apply_data(task, {"elements": [title()]})
    original = task.project_path.read_bytes()
    with pytest.raises(ToolError, match="does not fit"):
        apply_data(task, {"texts": {"title": "A" * 300}})
    assert task.project_path.read_bytes() == original
    path = task.root / "private" / "stale.json"
    write_json(path, {"expected_revision": 1, "texts": {"title": "Stale"}})
    from collage_recreate.core import apply_patch
    with pytest.raises(ToolError) as error:
        apply_patch(task, path)
    assert error.value.code == "REVISION_CONFLICT"
    assert task.project_path.read_bytes() == original


def test_glyph_missing_and_multiline(task):
    apply_data(task, {"texts": {"title": "A\nB"}, "elements": [title(height=55)]})
    assert compose(task)[0].getbbox()
    with pytest.raises(ToolError) as error:
        apply_data(task, {"texts": {"title": "中文"}})
    assert error.value.code == "GLYPH_MISSING"


def test_chinese_multiline_with_explicit_font(task):
    path = Path("C:/Windows/Fonts/msyh.ttc")
    if not path.is_file():
        pytest.skip("CJK font unavailable on this test host")
    apply_data(task, {"assets": {"chinese": {"kind": "font", "path": str(path)}},
                     "texts": {"title": "中文\n测试"}, "elements": [title(font="chinese", height=60)]})
    canvas, _ = compose(task)
    assert ImageChops.difference(canvas.convert("RGB"), Image.new("RGB", canvas.size, "white")).getbbox()


def test_masks_and_intentional_clipping(task):
    apply_data(task, {"elements": [picture(mask_shape="ellipse")]})
    image, _ = compose(task)
    assert image.getpixel((10, 20)) == (255, 255, 255, 255)
    with pytest.raises(ToolError) as error:
        apply_data(task, {"element_updates": {"photo": {"mask_shape": None, "mask_asset": "photo"}}})
    assert error.value.code == "ALPHA_REQUIRED"
    with pytest.raises(ToolError) as error:
        apply_data(task, {"element_updates": {"photo": {"x": -20}}})
    assert error.value.code == "LAYER_OUTSIDE"
    apply_data(task, {"element_updates": {"photo": {"x": -20, "allow_clip": True}}})
    assert compose(task)[0].size == (160, 120)


@pytest.mark.parametrize("change", [
    {"elements": [picture(), picture()]},
    {"elements": [picture(group="absent")]},
    {"elements": [picture(asset="absent")]},
    {"duration": 2},
])
def test_bad_graph_and_timeline_rejected(task, change):
    data = task.load().model_dump()
    data.update(change)
    with pytest.raises(ToolError):
        parse(Project, data)


def test_missing_or_changed_resource_is_detected(task):
    apply_data(task, {"elements": [picture()]})
    asset = task.asset_path(task.load().assets["photo"])
    asset.write_bytes(b"corrupt")
    with pytest.raises(ToolError) as error:
        render(task)
    assert error.value.code == "ASSET_CHANGED"
    asset.unlink()
    with pytest.raises(ToolError) as error:
        render(task)
    assert error.value.code == "FILE_MISSING"


def test_render_recovery_is_deterministic(task):
    apply_data(task, {"elements": [picture(), title()]})
    first = render(task)
    from collage_recreate.core import Task
    second = render(Task(task.root))
    assert first["sha256"] == second["sha256"]
    assert second["cache_hit"] is True
