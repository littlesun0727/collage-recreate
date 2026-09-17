"""Stop rules must bound actual work, survive CLI switches, and retain useful warnings."""
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from PIL import Image

from collage_recreate.analysis_session import finish_draft, run_draft_operation, state_path
from collage_recreate.core import ToolError, read_json, write_json


@pytest.fixture
def case(tmp_path):
    reference=tmp_path/"reference.png";Image.new("RGB",(80,60),"gray").save(reference)
    draft=tmp_path/"draft.json"
    data={"background":{"mode":"fixed","kind":"solid","background_brief":"plain"},
          "slots":[],"texts":[],"overlays":[],"layer_order":[{"type":"background"}],"questions":[]}
    write_json(draft,data)
    return tmp_path/"task",reference,draft,data


def run(case, operation="preview", **kwargs):
    task,ref,path,_=case
    output=kwargs.pop("output", task.parent/("out-"+str(len(list(task.parent.glob("out-*"))))))
    return run_draft_operation(task,path,ref,operation,output=output,**kwargs)


def test_unchanged_input_reuses_artifacts_across_paths_and_operations(case):
    result=run(case)
    task,ref,path,data=case
    copy=path.with_name("renamed.json")
    data["questions"]=["pending detail"];data["background"]["review_notes"]="review-only change"
    copy.write_text(json.dumps(data,indent=4))
    refcopy=ref.with_name("same-image.png");shutil.copyfile(ref,refcopy)
    second=run_draft_operation(task,copy,refcopy,"refine",output=task.parent/"unused")
    assert second["workflow"]["reused"] and second["workflow"]["attempts_used"]==1
    assert second["preview"]==result["preview"]
    assert not (task.parent/"unused").exists()


def test_budget_is_shared_and_third_attempt_does_no_work(case):
    first=run(case);task,ref,path,data=case
    data["background"]["background_brief"]="corrected design";write_json(path,data)
    second=run(case, "check", issue="wrong_assignment",reason="background: corrected material category")
    assert second["ok"] and second["workflow"]["attempts_used"]==2
    data["background"]["background_brief"]="third";write_json(path,data)
    blocked=run(case, output=task.parent/"must-not-exist",issue="structural",reason="another change")
    assert blocked["error"]["code"]=="CHECK_LIMIT"
    assert not (task.parent/"must-not-exist").exists()
    assert first["workflow"]["max_attempts"]==2


def test_failed_structure_consumes_attempt_and_can_be_corrected_once(case):
    task,ref,path,data=case
    data["layer_order"]=[];write_json(path,data)
    failed=run(case)
    assert not failed["ok"] and failed["workflow"]["attempts_used"]==1
    data["layer_order"]=[{"type":"background"}];write_json(path,data)
    fixed=run(case,issue="structural",reason="background: add missing bottom layer")
    assert fixed["ok"] and fixed["workflow"]["stop_rechecking"]
    ended=finish_draft(task,ref,"usable","Preview checked; primary objects accounted for")
    assert ended["decision"]=="usable"


def test_repeating_same_failure_ends_instead_of_revalidating(case):
    task,ref,path,data=case
    data["layer_order"]=[];write_json(path,data)
    run(case)
    again=run(case)
    assert again["error"]["code"]=="UNCHANGED_FAILED_INPUT"
    assert again["workflow"]["attempts_used"]==1
    data["layer_order"]=[{"type":"background"}];write_json(path,data)
    assert run(case,issue="structural",reason="fixed")["error"]["code"]=="TASK_FINISHED"


def test_correction_requires_concrete_category_and_reason(case):
    run(case);task,ref,path,data=case
    data["background"]["background_brief"]="different";write_json(path,data)
    blocked=run(case)
    assert blocked["error"]["code"]=="CORRECTION_REQUIRED"
    assert blocked["workflow"]["attempts_used"]==1
    assert run(case,issue="wrong_assignment",reason="background: fix observed category")["ok"]


def test_needs_review_can_finish_normally_without_second_refine(case):
    task,ref,path,data=case
    data["slots"]=[{"id":"photo","label":"photo","source_rect":[10,10,30,30],"mode":"cutout"}]
    data["layer_order"].append({"type":"slot","id":"photo"});write_json(path,data)
    result=run(case,"refine")
    assert result["needs_review"]==["photo"]
    ended=finish_draft(task,ref,"usable","Rough position is usable; fine edge remains unresolved")
    assert ended["decision"]=="usable_with_questions"
    assert ended["workflow"]["attempts_used"]==1
    assert ended["visual_status"]=="unreviewed"
    assert ended["workflow"]["stop_rechecking"]


def test_finish_prevents_later_changed_input_and_is_idempotent(case):
    run(case);task,ref,path,data=case
    finish_draft(task,ref,"usable","Checked once")
    again=finish_draft(task,ref,"not_ready","Do not overwrite the earlier review")
    assert again["decision"]=="usable"
    data["background"]["background_brief"]="changed";write_json(path,data)
    blocked=run(case,issue="customer_requirement",reason="new internal guess")
    assert blocked["error"]["code"]=="TASK_FINISHED"


def test_mutation_after_preview_cannot_claim_ready(case):
    run(case);task,ref,path,data=case
    data["background"]["background_brief"]="unreviewed";write_json(path,data)
    with pytest.raises(ToolError,match="changed after"):
        finish_draft(task,ref,"usable","claim")
    ended=finish_draft(task,ref,"not_ready","Input changed; current draft is not ready")
    assert ended["decision"]=="not_ready"


def test_two_different_references_have_independent_budgets(case):
    run(case);task,ref,path,data=case
    other=ref.with_name("other.png");Image.new("RGB",(90,70),"white").save(other)
    second=run_draft_operation(task,path,other,"preview",output=task.parent/"other")
    assert second["workflow"]["attempts_used"]==1


def test_pinned_task_root_cannot_be_changed(case,monkeypatch):
    task,ref,path,data=case;monkeypatch.setenv("COLLAGE_ANALYSIS_TASK_ROOT",str(task))
    run(case)
    with pytest.raises(ToolError,match="existing analysis task"):
        run_draft_operation(task.parent/"reset",path,ref,"check")


def test_lock_blocks_concurrent_checks(case,monkeypatch):
    from collage_recreate import analysis
    original=analysis.preview_draft;entered=Event();release=Event()
    def slow(*args):
        entered.set();assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(analysis,"preview_draft",slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(run,case);assert entered.wait(5)
        try:
            with pytest.raises(ToolError,match="already running"):
                run(case)
        finally:release.set()
        assert first.result()["ok"]
    path,_=state_path(case[0],case[1]);assert len(read_json(path)["attempts"])==1


def test_interrupted_reservation_does_not_start_a_new_attempt(case):
    task,ref,path,data=case;statefile,sha=state_path(task,ref)
    write_json(statefile,{"version":1,"source_sha256":sha,"phase":"running","attempts":[
        {"number":1,"fingerprints":[],"result":None}],"decision":None})
    result=run(case)
    assert result["error"]["code"]=="CHECK_INTERRUPTED"
    assert result["workflow"]["attempts_used"]==1


def test_cached_refinement_rejects_mutated_adopted_draft(case):
    result=run(case,"refine")
    adopted=Path(result["draft"]);data=read_json(adopted)
    data["background"]["background_brief"]="changed after check";write_json(adopted,data)
    blocked=run(case,"refine")
    assert blocked["error"]["code"]=="SAVED_DRAFT_CHANGED"
    assert blocked["workflow"]["attempts_used"]==1


def test_second_failure_is_terminal(case):
    task,ref,path,data=case
    data["layer_order"]=[];write_json(path,data);run(case)
    data["background"]["background_brief"]="another invalid input";write_json(path,data)
    second=run(case,issue="structural",reason="attempted layer repair")
    assert not second["ok"] and second["workflow"]["decision"]=="not_ready"
    data["layer_order"]=[{"type":"background"}];write_json(path,data)
    assert run(case,issue="structural",reason="third attempt")["error"]["code"]=="TASK_FINISHED"


def test_check_only_can_upgrade_once_to_preview(case):
    first=run(case,"check")
    assert "preview" not in first
    second=run(case,"preview")
    assert second["ok"] and second["preview"]
    assert second["workflow"]["attempts_used"]==2
    assert finish_draft(case[0],case[1],"usable","Preview checked")["decision"]=="usable"
