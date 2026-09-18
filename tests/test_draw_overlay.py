from pathlib import Path

from PIL import Image
import pytest

from collage_recreate.core import ToolError, write_json
from collage_recreate.draw_overlay import render_basic_shape


def case(tmp_path, kind="line"):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (200, 120), "white").save(reference)
    shape = {"kind":kind, "fill":None, "outline":"#1266ee", "width":4,
             "points":[[0,0],[500,1000],[1000,0]]}
    if kind == "line":
        shape["points"] = [[0,500],[1000,500]]
    data={"background":{"mode":"fixed","kind":"solid","background_brief":"plain"},
          "slots":[],"texts":[],"overlays":[{"id":"rule","label":"line","source_rect":[20,30,100,40],
          "action":"basic_shape","generation_brief":"","requires_exact_content":False,
          "attachment":None,"review_notes":"","shape":shape}],
          "layer_order":[{"type":"background"},{"type":"overlay","id":"rule"}],"questions":[]}
    draft=tmp_path/"draft.json"
    write_json(draft,data)
    return draft,reference,data


@pytest.mark.parametrize("kind",["line","polyline"])
def test_deterministic_line_outputs_transparent_png(tmp_path,kind):
    draft,reference,_=case(tmp_path,kind)
    output=tmp_path/f"{kind}.png"
    result=render_basic_shape(draft,reference,"rule",output)
    assert result["ok"] and result["alpha_range"][0]==0 and result["alpha_range"][1]>0
    with Image.open(output) as image:
        assert image.size==(100,40) and image.mode=="RGBA"


def test_dashed_line_and_existing_output_guard(tmp_path):
    draft,reference,data=case(tmp_path)
    data["overlays"][0]["shape"].update(dash=8,gap=5)
    write_json(draft,data)
    output=tmp_path/"line.png"
    render_basic_shape(draft,reference,"rule",output)
    with pytest.raises(ToolError) as error:
        render_basic_shape(draft,reference,"rule",output)
    assert error.value.code=="OUTPUT_EXISTS"


@pytest.mark.parametrize("change",["bad_points","fill","zero_width","half_dash"])
def test_line_contract_rejects_invalid_specs(tmp_path,change):
    draft,reference,data=case(tmp_path)
    shape=data["overlays"][0]["shape"]
    if change=="bad_points":
        shape["points"]=[[0,0],[1001,1]]
    elif change=="fill":
        shape["fill"]="#fff"
    elif change=="zero_width":
        shape["width"]=0
    else:
        shape["dash"]=5
    write_json(draft,data)
    with pytest.raises(ToolError):
        render_basic_shape(draft,reference,"rule",tmp_path/"out.png")


def test_renderer_rejects_reference_generate(tmp_path):
    draft,reference,data=case(tmp_path)
    overlay=data["overlays"][0]
    overlay.update(action="reference_generate",generation_brief="hand drawn")
    overlay.pop("shape")
    write_json(draft,data)
    with pytest.raises(ToolError) as error:
        render_basic_shape(draft,reference,"rule",tmp_path/"out.png")
    assert error.value.code=="OVERLAY_NOT_DETERMINISTIC"
