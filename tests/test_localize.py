"""Geometry assertions use known pixels, not schema-only success."""
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from collage_recreate.analysis import Draft, check_draft
from collage_recreate.core import ToolError, write_json
from collage_recreate.localize import refine_layout


def make_case(tmp_path, image, *, slots=None, texts=None, overlays=None):
    reference=tmp_path/"reference.png"; image.save(reference)
    data={"background":{"mode":"fixed","kind":"solid","background_brief":"plain","review_notes":""},
          "slots":slots or [],"texts":texts or [],"overlays":overlays or [],
          "layer_order":[{"type":"background"}], "questions":[]}
    for key, kind in (("slots","slot"),("texts","text"),("overlays","overlay")):
        data["layer_order"].extend({"type":kind,"id":item["id"]} for item in data[key])
    path=tmp_path/"draft.json";write_json(path,data)
    return reference,path,data


def overlay(rect, colors=()):
    return {"id":"compound","label":"two marks","source_rect":rect,"locate_colors":list(colors),
            "action":"reference_generate","generation_brief":"Separate marks made as one asset",
            "requires_exact_content":False,"review_notes":"","attachment":None}


def test_compound_keeps_disconnected_marks_and_expands_clipped_rough_box(tmp_path):
    im=Image.new("RGB",(260,190),"#dde6ef");d=ImageDraw.Draw(im)
    d.ellipse((47,55,77,95),fill="#d86226");d.rectangle((151,111,170,143),fill="#d86226")
    ref,path,_=make_case(tmp_path,im,overlays=[overlay([64,60,100,79],["#d86226"])])
    before=(path.read_bytes(),ref.read_bytes())
    result=refine_layout(path,ref,tmp_path/"out")
    row=json.loads(Path(result["report"]).read_text())["items"][0]
    assert row["status"]=="refined"
    assert row["components"]==2
    assert row["expansions"]>=1
    x,y,w,h=row["effective_rect"]
    assert x<=47 and y<=55 and x+w>=171 and y+h>=144
    assert row["effective_rect"]!=row["rough_rect"]
    assert (path.read_bytes(),ref.read_bytes())==before
    assert "target_rect" not in Path(result["draft"]).read_text()
    assert check_draft(result["draft"],ref)["counts"]["overlays"]==1


def test_photo_snaps_to_inner_content_not_outer_frame(tmp_path):
    im=Image.new("RGB",(230,190),"#d7e0ed");d=ImageDraw.Draw(im)
    d.rectangle((32,30,156,164),outline="#23337c",width=4)
    rng=np.random.default_rng(4)
    photo=rng.integers(20,210,(110,100,3),dtype=np.uint8)
    im.paste(Image.fromarray(photo),(45,42))
    slot={"id":"photo","label":"photo","source_rect":[47,40,99,114],"mode":"photo","upload_hint":""}
    ref,path,_=make_case(tmp_path,im,slots=[slot])
    row=json.loads(Path(refine_layout(path,ref,tmp_path/"out")["report"]).read_text())["items"][0]
    assert row["status"]=="refined",row
    assert row["effective_rect"]==[45,42,100,110]


def test_uniform_text_background_recovers_glyph_extent_without_rewriting_text(tmp_path):
    im=Image.new("RGB",(310,130),"#274976");d=ImageDraw.Draw(im)
    font=ImageFont.truetype(str(Path(__file__).parents[1]/"assets/fonts/DejaVuSans.ttf"),26)
    d.text((45,35),"Original gy",font=font,fill="white")
    b=d.textbbox((45,35),"Original gy",font=font)
    rough=[b[0]+4,b[1]+2,b[2]-b[0]-8,b[3]-b[1]-4]
    text={"id":"caption","label":"caption","source_rect":rough,"default_text":"Customer text"}
    ref,path,_=make_case(tmp_path,im,texts=[text])
    result=refine_layout(path,ref,tmp_path/"out")
    row=json.loads(Path(result["report"]).read_text())["items"][0]
    assert row["status"]=="refined",row
    assert row["effective_rect"][1]+row["effective_rect"][3]>=b[3]
    assert json.loads(Path(result["draft"]).read_text())["texts"][0]["default_text"]=="Customer text"


def test_flat_roi_is_review_required_and_keeps_original_geometry(tmp_path):
    ref,path,data=make_case(tmp_path,Image.new("RGB",(140,120),"gray"),
                            overlays=[overlay([30,30,60,45],["#ff4400"])])
    result=refine_layout(path,ref,tmp_path/"out")
    row=json.loads(Path(result["report"]).read_text())["items"][0]
    assert row["status"]=="needs_review" and "no_foreground" in row["reasons"]
    assert row["effective_rect"]==data["overlays"][0]["source_rect"]


def test_touching_final_search_boundary_is_not_silently_accepted(tmp_path):
    im=Image.new("RGB",(220,180),"white")
    ImageDraw.Draw(im).line((0,90,219,90),fill="#922c72",width=5)
    ref,path,data=make_case(tmp_path,im,overlays=[overlay([70,80,50,20],["#922c72"])])
    result=refine_layout(path,ref,tmp_path/"out")
    row=json.loads(Path(result["report"]).read_text())["items"][0]
    assert row["status"]=="needs_review"
    assert "foreground_touches_search_edge" in row["reasons"]
    assert row["effective_rect"]==data["overlays"][0]["source_rect"]


def test_cache_is_deterministic_and_invalidated_by_reference_or_hint(tmp_path):
    im=Image.new("RGB",(120,100),"white");ImageDraw.Draw(im).ellipse((30,25,75,65),fill="#bb4020")
    ref,path,data=make_case(tmp_path,im,overlays=[overlay([25,20,55,55],["#bb4020"])])
    first=refine_layout(path,ref,tmp_path/"one")
    second=refine_layout(path,ref,tmp_path/"two",reuse=tmp_path/"one")
    assert second["cache_hits"]==1
    assert Path(first["draft"]).read_bytes()==Path(second["draft"]).read_bytes()
    data["overlays"][0]["locate_colors"]=["#00ff00"];write_json(path,data)
    third=refine_layout(path,ref,tmp_path/"three",reuse=tmp_path/"one")
    assert third["cache_hits"]==0
    data["overlays"][0]["locate_colors"]=["#bb4020"];write_json(path,data)
    im.putpixel((0,0),(1,2,3));im.save(ref)
    assert refine_layout(path,ref,tmp_path/"four",reuse=tmp_path/"one")["cache_hits"]==0


def test_background_stays_full_canvas_and_unsupported_cutout_stays_pending(tmp_path):
    im=Image.new("RGB",(140,110),"#7799aa")
    slots=[{"id":"bg","label":"bg","source_rect":[0,0,140,110],"mode":"photo"},
           {"id":"subject","label":"subject","source_rect":[20,20,50,60],"mode":"cutout"}]
    ref,path,data=make_case(tmp_path,im,slots=slots)
    data["background"]={"mode":"slot","kind":"photo","slot_id":"bg","review_notes":""}
    data["layer_order"].pop(0);write_json(path,data)
    result=refine_layout(path,ref,tmp_path/"out")
    rows=json.loads(Path(result["report"]).read_text())["items"]
    assert [r["status"] for r in rows]==["unchanged","needs_review"]
    assert rows[0]["effective_rect"]==[0,0,140,110]


def test_output_overwrite_and_invalid_input_have_no_side_effects(tmp_path):
    ref,path,_=make_case(tmp_path,Image.new("RGB",(50,50),"white"))
    before=ref.read_bytes()
    with pytest.raises(ToolError,match="new refinement"):
        refine_layout(path,ref,ref)
    assert ref.read_bytes()==before
    data=json.loads(path.read_text());data["texts"]=[{"id":"t","label":"t","source_rect":[0,0,0,5],"default_text":"x"}]
    write_json(path,data)
    with pytest.raises(ToolError):
        refine_layout(path,ref,tmp_path/"out")
    assert not (tmp_path/"out").exists()


def test_cli_reports_review_separately_from_technical_success(tmp_path):
    ref,path,_=make_case(tmp_path,Image.new("RGB",(90,80),"gray"),
                          overlays=[overlay([20,20,30,30],["red"])])
    proc=subprocess.run([sys.executable,str(Path(__file__).parents[1]/"scripts/refine_layout.py"),
                         "--reference",str(ref),"--input",str(path),"--output",str(tmp_path/"cli")],
                         capture_output=True,text=True,encoding="utf-8")
    assert proc.returncode==0,proc.stderr
    result=json.loads(proc.stdout)
    assert result["ok"] and result["needs_review"]==["compound"]
    assert result["visual_status"]=="unreviewed"

def test_neighbouring_text_line_outside_seed_is_not_absorbed(tmp_path):
    im=Image.new("RGB",(250,140),"#4e689c");d=ImageDraw.Draw(im)
    d.rectangle((45,40,185,52),fill="white")
    d.rectangle((35,59,200,73),fill="white")
    ref,path,_=make_case(tmp_path,im,texts=[{"id":"caption","label":"first line",
        "source_rect":[42,38,147,17],"default_text":"first"}])
    result=refine_layout(path,ref,tmp_path/"out")
    row=json.loads(Path(result["report"]).read_text())["items"][0]
    assert row["status"]=="refined",row
    assert row["effective_rect"]==[44,39,143,15]


def test_low_contrast_colour_miss_does_not_become_exact_text(tmp_path):
    im=Image.new("RGB",(250,120),"#274976");d=ImageDraw.Draw(im)
    d.rectangle((35,40,44,60),fill="#bcc3ce")
    d.rectangle((55,40,175,60),fill="white")
    ref,path,_=make_case(tmp_path,im,texts=[{"id":"caption","label":"line","source_rect":[30,35,150,30],
        "default_text":"do not drop first glyph","locate_colors":["white"]}])
    row=json.loads(Path(refine_layout(path,ref,tmp_path/"out")["report"]).read_text())["items"][0]
    assert row["status"]=="needs_review" or row["effective_rect"][0]<=35


def test_shifted_photo_box_does_not_accept_frame_bottom_as_photo_edge(tmp_path):
    im=Image.new("RGB",(240,230),"#ede4cf");d=ImageDraw.Draw(im)
    d.rectangle((42,32,158,178),outline="#754f3b",width=5)
    # Smooth image content has a distinct edge inside the outer drawing.
    yy,xx=np.mgrid[:130,:100]
    photo=np.stack((70+xx//3,100+yy//3,140+(xx+yy)//7),axis=-1).astype(np.uint8)
    im.paste(Image.fromarray(photo),(50,40))
    slot={"id":"photo","label":"photo","source_rect":[50,40,100,139],"mode":"photo"}
    ref,path,_=make_case(tmp_path,im,slots=[slot])
    row=json.loads(Path(refine_layout(path,ref,tmp_path/"out")["report"]).read_text())["items"][0]
    assert row["status"]=="needs_review" or row["effective_rect"]==[50,40,100,130]


def test_expanded_decoration_cannot_silently_absorb_another_overlay(tmp_path):
    im=Image.new("RGB",(260,220),"white");d=ImageDraw.Draw(im)
    d.rectangle((100,35,150,125),fill="#dc7521")
    d.ellipse((50,145,70,165),fill="#dc7521")
    first=overlay([45,110,115,65],["#dc7521"])
    peer=overlay([95,30,60,100],["#dc7521"]);peer.update(id="other",label="independent decoration")
    ref,path,_=make_case(tmp_path,im,overlays=[first,peer])
    rows=json.loads(Path(refine_layout(path,ref,tmp_path/"out")["report"]).read_text())["items"]
    assert rows[0]["status"]=="needs_review"
    assert rows[0]["effective_rect"]==first["source_rect"]


def test_mixed_color_foreground_is_not_automatically_a_single_text_line(tmp_path):
    im=Image.new("RGB",(220,130),"#293654");d=ImageDraw.Draw(im)
    d.rectangle((35,65,170,77),fill="white")
    d.ellipse((90,45,120,68),fill="#eeda32")
    text={"id":"caption","label":"text","source_rect":[30,55,150,30],"default_text":"white text only"}
    ref,path,_=make_case(tmp_path,im,texts=[text])
    row=json.loads(Path(refine_layout(path,ref,tmp_path/"out")["report"]).read_text())["items"][0]
    assert row["status"]=="needs_review"
    assert "mixed_foreground_colors" in row["reasons"]
