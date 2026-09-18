import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest
from PIL import Image
from collage_recreate.core import ToolError, read_json, write_json
from collage_recreate.analysis_pipeline import validate_draft_against_inventory
from collage_recreate.analysis_session import run_inventory_check, run_draft_operation, finish_draft, state_path

@pytest.fixture
def case(tmp_path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (100, 100), "white").save(reference)
    inventory = {"version":1, "objects":[
        {"id":"p1","visual_type":"photo","description":"left photo","text_content":None,"confidence":"high"},
        {"id":"d1","visual_type":"decoration","description":"sticker","text_content":None,"confidence":"high"}], "questions":[]}
    draft = {"background":{"mode":"fixed","kind":"solid","background_brief":"plain"},
             "slots":[{"id":"photo","label":"photo","source_bbox_1000":[0,0,500,800],"mode":"photo"}],
             "texts":[], "overlays":[{"id":"sticker","label":"sticker","source_bbox_1000":[600,600,900,900],
             "action":"reference_generate","generation_brief":"small sticker","requires_exact_content":False,
             "attachment":None,"review_notes":""}],
             "layer_order":[{"type":"background"},{"type":"slot","id":"photo"},{"type":"overlay","id":"sticker"}],
             "questions":[]}
    mapping={"version":1,"bindings":[{"type":"background","origin":"default","reason":"plain residual canvas"},
             {"type":"slot","id":"photo","source_objects":["p1"]},
             {"type":"overlay","id":"sticker","source_objects":["d1"]}],"ignored":[],"unresolved":[]}
    paths={}
    for name, data in (("inventory",inventory),("draft",draft),("mapping",mapping)):
        paths[name]=tmp_path/(name+".json");write_json(paths[name],data)
    return {"task":tmp_path/"task","ref":reference,"paths":paths,"inventory":inventory,"draft":draft,"mapping":mapping}

def save(case, name, suffix=""):
    path=case["paths"][name].with_name(name+suffix+".json")
    write_json(path,case[name]);return path

def coverage(c):
    return validate_draft_against_inventory(c["paths"]["inventory"],c["paths"]["draft"],c["paths"]["mapping"],c["ref"])

def register(c):
    return run_inventory_check(c["task"],c["paths"]["inventory"],c["ref"])

def run(c, **kwargs):
    return run_draft_operation(c["task"],kwargs.pop("input_path",c["paths"]["draft"]),c["ref"],kwargs.pop("operation","preview"),
        output=kwargs.pop("output",c["task"].parent/"preview"),mapping=kwargs.pop("mapping",c["paths"]["mapping"]),**kwargs)

def test_valid_mapping_and_legacy_draft_are_compatible(case):
    c=case
    before={k:p.read_bytes() for k,p in c["paths"].items()}
    assert coverage(c)["coverage_status"]=="covered"
    assert register(c)["workflow"]["next_action"]=="write_draft_and_mapping_then_refine"
    assert run(c)["ok"]
    result=finish_draft(c["task"],c["ref"],"usable","Viewed actual preview")
    assert result["draft_ready"] and result["decision"]=="usable"
    assert all(p.read_bytes()==before[k] for k,p in c["paths"].items())

def test_dropped_photo_is_reported_without_auto_repair(case):
    c=case;c["inventory"]["objects"].append({**c["inventory"]["objects"][0],"id":"p2"})
    save(c,"inventory")
    r=coverage(c)
    assert r["issues"][0]["code"]=="OBJECT_DROPPED"
    assert register(c)["ok"]
    assert run(c)["error"]["code"]=="COVERAGE_REVIEW_REQUIRED"
    assert not (c["task"].parent/"preview").exists()

def test_many_photos_in_one_slot_trigger_review(case):
    c=case;c["inventory"]["objects"].append({**c["inventory"]["objects"][0],"id":"p2"})
    c["mapping"]["bindings"][1]["source_objects"].append("p2")
    save(c,"inventory");save(c,"mapping")
    assert any(i["code"]=="POSSIBLE_SLOT_OMISSION" for i in coverage(c)["issues"])

def test_sticker_as_customer_slot_requires_visible_exception(case):
    c=case;c["inventory"]["objects"][0]["visual_type"]="decoration";save(c,"inventory")
    assert coverage(c)["issues"][0]["code"]=="POSSIBLE_WRONG_SLOT"
    c["mapping"]["bindings"][1]["reason"]="User explicitly requested a replaceable sticker";save(c,"mapping")
    result=coverage(c)
    assert not result["issues"] and result["warnings"][0]["code"]=="POSSIBLE_WRONG_SLOT"

@pytest.mark.parametrize("change",[
    lambda m:m["bindings"][1].update(source_objects=["absent"]),
    lambda m:m["bindings"][1].update(id="absent"),
    lambda m:m["bindings"].append(copy.deepcopy(m["bindings"][0])),
    lambda m:m["ignored"].append({"object_id":"p1","reason":"keep"}),
    lambda m:m["bindings"][2].update(source_objects=["p1"]),
])
def test_broken_mapping_references_are_rejected(case,change):
    change(case["mapping"]);save(case,"mapping")
    with pytest.raises(ToolError):coverage(case)

def test_unknown_is_preserved_and_blocking_question_prevents_ready(case):
    c=case;c["inventory"]["objects"].append({"id":"unknown","visual_type":"unknown","description":"unclear",
                                          "text_content":None,"confidence":"low"})
    c["mapping"]["unresolved"].append({"object_id":"unknown","question":"unclear major object"})
    save(c,"inventory");save(c,"mapping")
    assert register(c)["ok"]
    assert run(c)["coverage"]["issues"][0]["code"]=="UNKNOWN_UNRESOLVED"
    ended=finish_draft(c["task"],c["ref"],"not_ready","Unclear major object")
    assert ended["ok"] and not ended["draft_ready"]
    assert finish_draft(c["task"],c["ref"],"not_ready","same")["workflow"]["reused"]

def test_inventory_is_required_and_pipeline_cannot_switch(case):
    c=case
    with pytest.raises(ToolError,match="Check the visual inventory"):
        run(c,pipeline="inventory_v1")
    register(c)
    with pytest.raises(ToolError,match="Changing pipelines"):
        run(c,pipeline="legacy")

def test_single_correction_shared_across_inventory_and_draft(case):
    c=case;c["inventory"]["objects"][0]["confidence"]="bad";save(c,"inventory")
    assert not register(c)["ok"]
    c["inventory"]["objects"][0]["confidence"]="high"
    corrected=save(c,"inventory",".corrected")
    assert run_inventory_check(c["task"],corrected,c["ref"],reason="p1: fix enum")["ok"]
    c["draft"]["layer_order"]=[];save(c,"draft")
    assert run(c)["workflow"]["decision"]=="not_ready"
    assert finish_draft(c["task"],c["ref"],"not_ready","Invalid layers")["ok"]

def test_notes_field_repair_is_not_unchanged_failure(case):
    c=case;register(c)
    c["draft"]["overlays"][0].pop("review_notes");save(c,"draft")
    assert not run(c)["ok"]
    c["draft"]["overlays"][0]["review_notes"]=""
    corrected=save(c,"draft",".corrected")
    assert run(c,input_path=corrected,issue="structural",reason="sticker: restore required notes")["ok"]

def test_inventory_change_invalidates_previous_draft(case):
    c=case;register(c);run(c)
    c["inventory"]["questions"]=["p1: photo content unclear"]
    corrected=save(c,"inventory",".corrected")
    run_inventory_check(c["task"],corrected,c["ref"],reason="p1: preserve newly observed uncertainty")
    with pytest.raises(ToolError,match="current valid draft"):
        finish_draft(c["task"],c["ref"],"usable","claim")
    assert finish_draft(c["task"],c["ref"],"not_ready","Dependent draft needs refresh")["ok"]

@pytest.mark.parametrize("target",["state","inventory","mapping","preview"])
def test_modified_checked_artifact_cannot_claim_success(case,target):
    c=case;register(c);r=run(c)
    if target=="state":
        p,_=state_path(c["task"],c["ref"]);data=read_json(p);data["decision"]="usable";write_json(p,data)
    elif target=="preview":
        Path(r["preview"]).write_text("changed")
    else:
        c["paths"][target].write_text("{}")
    with pytest.raises(ToolError):
        finish_draft(c["task"],c["ref"],"usable","claim")

def test_geometry_only_rejects_business_changes(case):
    c=case;register(c);r=run(c)
    c["draft"]["slots"][0]["mode"]="cutout"
    corrected=save(c,"draft",".corrected")
    with pytest.raises(ToolError,match="Geometry-only"):
        run(c,input_path=corrected,review_stage="geometry",object_ids=["photo"],
            issue="wrong_object",reason="photo: boundary",output=c["task"].parent/"second")

def test_geometry_only_accepts_only_named_coordinates(case):
    c=case;register(c);run(c)
    c["draft"]["slots"][0]["source_bbox_1000"]=[10,10,500,800]
    corrected=save(c,"draft",".corrected")
    result=run(c,input_path=corrected,review_stage="geometry",object_ids=["photo"],
        issue="wrong_object",reason="photo: crop excess background",output=c["task"].parent/"second")
    assert result["ok"] and result["workflow"]["remaining_corrections"]==0

def test_not_ready_finish_cli_returns_success(case):
    c=case;register(c)
    script=Path(__file__).resolve().parents[1]/"scripts/review_analysis.py"
    result=subprocess.run([sys.executable,str(script),"finish","--task",str(c["task"]),"--reference",str(c["ref"]),
                           "--decision","not_ready","--note","Missing reliable draft"],
                           capture_output=True,encoding="utf-8")
    assert result.returncode==0
    assert json.loads(result.stdout)["decision"]=="not_ready"
