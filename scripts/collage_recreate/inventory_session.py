"""Bounded inventory workflow; called under the shared analysis file lock."""
from pathlib import Path
from .core import ToolError, digest, file_hash, read_json, require
from .inventory import validate_inventory
from .analysis_pipeline import assert_geometry_only, validate_draft_against_inventory
from .analysis import validate_draft
from .analysis_session import ISSUES, _error, _response, _save, _terminal_result, fingerprint

def _result(state, path, result, **kwargs):
    value = _response(state, path, result, **kwargs)
    value["workflow"].update(inventory_checks=len(state["inventory_checks"]),
        remaining_corrections=0 if state["correction"] else 1)
    if state["phase"] == "inventory_ready":
        value["workflow"]["next_action"] = "write_draft_and_mapping_then_refine"
    elif state["phase"] == "needs_inventory_correction":
        value["workflow"]["next_action"] = "correct_inventory_once_or_finish_not_ready"
    return value

def _stop(state, path, code, message):
    state.update(phase="finished", decision="not_ready", review_note=message)
    _save(path, state)
    return _result(state, path, _error(code, message), stop=True)

def _take_correction(state, stage, reason, object_ids=None):
    require(not state["correction"], "REVIEW_LIMIT", "One shared correction has already been used; finish not_ready")
    require(bool(reason and reason.strip()), "CORRECTION_REQUIRED",
            "Give the affected objects, evidence and intended correction in --reason")
    state["correction"] = {"stage": stage, "reason": reason.strip(), "object_ids": object_ids or []}

def _verify_files(files, code="INPUT_CHANGED"):
    for label, row in files.items():
        require(Path(row["path"]).is_file() and file_hash(row["path"]) == row["sha256"], code,
                f"{label} changed after its check; preserve the original and stop")

def _file(path):
    return {"path": str(Path(path).resolve()), "sha256": file_hash(path)}

def _inventory(state, provided=None):
    records = state["inventory_checks"]
    require(bool(records) and records[-1]["result"]["ok"], "INVENTORY_REQUIRED",
            "Check the visual inventory before writing the production draft")
    record = records[-1]
    _verify_files({"inventory": record["file"]}, "INVENTORY_CHANGED")
    if provided:
        require(file_hash(provided) == record["file"]["sha256"], "INVENTORY_CHANGED",
                "Use the registered inventory; check a corrected inventory before using it")
    return record

def check_locked(state, path, input_path, reason):
    if state["phase"] == "running":
        return _stop(state, path, "CHECK_INTERRUPTED", "An interrupted operation cannot be restarted automatically")
    if state["phase"] == "finished":
        return _result(state, path, _error("TASK_FINISHED", "Return the saved result"), stop=True)
    key = fingerprint(input_path, full=True)
    records = state["inventory_checks"]
    last = records[-1] if records else None
    if last and last["input_hash"] == key:
        if last["result"]["ok"]:
            _verify_files({"inventory": last["file"]}, "INVENTORY_CHANGED")
            return _result(state, path, last["result"], reused=True)
        return _stop(state, path, "UNCHANGED_FAILED_INPUT", "Inventory did not change after its failed check")
    if last:
        _take_correction(state, "inventory", reason)
    record = {"input_hash": key, "input": str(Path(input_path).resolve()), "result": None}
    records.append(record)
    state["phase"] = "running"
    _save(path, state)
    try:
        inventory = validate_inventory(input_path)
        record["file"] = _file(input_path)
        result = {"ok": True, "inventory": record["file"]["path"],
                  "inventory_sha256": record["file"]["sha256"], "object_count": len(inventory.objects),
                  "questions": inventory.questions, "unknown_ids": [
                      o.id for o in inventory.objects if o.visual_type == "unknown"],
                  "visual_status": "unreviewed"}
        state["phase"] = "inventory_ready"
    except (ToolError, OSError) as exc:
        result = _error(exc.code if isinstance(exc, ToolError) else "FILE_IO",
                        str(exc) if isinstance(exc, ToolError) else "Cannot read inventory")
        state["phase"] = "needs_inventory_correction"
        if state["correction"]:
            state.update(phase="finished", decision="not_ready", review_note=result["error"]["message"])
    record["result"] = result
    _save(path, state)
    return _result(state, path, result)

def run_draft_locked(state, path, input_path, reference, operation, *, output, reuse, issue, reason,
                     inventory, mapping, review_stage, object_ids):
    if state["phase"] == "running":
        return _stop(state, path, "CHECK_INTERRUPTED", "An interrupted analysis check is not automatically repeated")
    require(operation in ("check", "preview", "refine"), "OPERATION_INVALID", "Use check, preview or refine")
    inv = _inventory(state, inventory)
    require(mapping, "MAPPING_REQUIRED", "inventory_v1 requires --mapping draft_mapping.json")
    key = digest({"inventory": inv["file"]["sha256"], "draft": fingerprint(input_path, full=True),
                  "mapping": fingerprint(mapping, full=True)})
    attempts = state["attempts"]
    last = attempts[-1] if attempts else None
    same = bool(last and key in last["fingerprints"])
    if last and (last.get("result") or {}).get("ok"):
        _verify_files(last["files"], "SAVED_DRAFT_CHANGED")
    if state["phase"] == "finished":
        if same and last and last["result"]["ok"]:
            return _result(state, path, last["result"], reused=True, stop=True)
        return _result(state, path, _error("TASK_FINISHED", "Return the saved result"), stop=True)
    upgrade = same and last["result"]["ok"] and operation != "check" and not last["result"].get("preview")
    if same and not upgrade:
        if last["result"]["ok"]:
            return _result(state, path, last["result"], reused=True)
        return _stop(state, path, "UNCHANGED_FAILED_INPUT", "Draft and mapping did not change after the failed check")
    require(len(attempts) < 2, "CHECK_LIMIT", "Two draft checks have been used; finish not_ready")
    if last and not upgrade:
        dependent_rebuild = (last["inventory_sha256"] != inv["file"]["sha256"]
                            and (state["correction"] or {}).get("stage") == "inventory")
        if not dependent_rebuild:
            require(issue in ISSUES, "CORRECTION_REQUIRED", "Supply a concrete --issue and --reason")
            stage = review_stage or "draft"
            require(stage in ("draft", "geometry"), "REVIEW_SCOPE", "Use draft or geometry correction")
            if stage == "geometry":
                require(last.get("canonical_draft"), "REVIEW_SCOPE", "Geometry correction needs a valid previous draft")
                current, _ = validate_draft(input_path, reference)
                require(fingerprint(mapping, full=True) == last["mapping_hash"], "REVIEW_SCOPE",
                        "Geometry correction cannot change mapping")
                assert_geometry_only(last["canonical_draft"], current.model_dump(), object_ids)
            _take_correction(state, stage, reason, object_ids)
    attempt = {"number": len(attempts)+1, "operation": operation, "input": str(Path(input_path).resolve()),
               "input_hash": key, "fingerprints": [key], "inventory_sha256": inv["file"]["sha256"],
               "mapping_hash": fingerprint(mapping, full=True), "issue": issue, "reason": reason, "result": None}
    attempts.append(attempt)
    state["phase"] = "running"
    _save(path, state)
    try:
        coverage = validate_draft_against_inventory(inv["file"]["path"], input_path, mapping, reference)
        attempt["coverage"] = coverage
        if coverage["issues"]:
            result = {**_error("COVERAGE_REVIEW_REQUIRED", "Review the listed object destinations once, or finish not_ready"),
                      "coverage": coverage}
        else:
            if operation == "refine":
                from .localize import refine_layout
                if reuse is None and last and (last.get("result") or {}).get("report"):
                    reuse = str(Path(last["result"]["report"]).parent)
                result = refine_layout(input_path, reference, output, reuse=reuse)
            elif operation == "preview":
                from .analysis import preview_draft
                result = preview_draft(input_path, reference, output)
            else:
                from .analysis import check_draft
                result = check_draft(input_path, reference)
            result["coverage"] = coverage
            adopted = result["draft"]
            attempt["files"] = {"inventory": inv["file"], "mapping": _file(mapping),
                                "original_draft": _file(input_path), "adopted_draft": _file(adopted)}
            if result.get("preview"):
                attempt["files"]["preview"] = _file(result["preview"])
            attempt["draft_sha256"] = file_hash(adopted)
            attempt["fingerprints"].append(digest({"inventory": inv["file"]["sha256"],
                "draft": fingerprint(adopted, full=True), "mapping": fingerprint(mapping, full=True)}))
            canonical, _ = validate_draft(adopted, reference)
            attempt["canonical_draft"] = canonical.model_dump()
            attempt["questions"] = list(dict.fromkeys([*canonical.questions, *coverage["inventory_questions"],
                                                      *coverage["unresolved"]]))
    except ImportError:
        result = _error("DEPENDENCY_MISSING", "Required local dependency unavailable")
    except ToolError as exc:
        result = _error(exc.code, str(exc))
    except OSError:
        result = _error("FILE_IO", "Cannot read or write the requested analysis files")
    attempt["result"] = result
    state["phase"] = "awaiting_review" if result["ok"] else "needs_correction"
    if not result["ok"] and (state["correction"] or len(attempts) >= 2
                            or result["error"]["code"] == "DEPENDENCY_MISSING"):
        state.update(phase="finished", decision="not_ready", review_note=result["error"]["message"])
    _save(path, state)
    return _result(state, path, result)

def finish_locked(state, path, decision, note):
    last = state["attempts"][-1] if state["attempts"] else {}
    result = last.get("result") or _error("DRAFT_NOT_READY", "No checked production draft")
    if state["phase"] == "finished":
        if state["decision"] != "not_ready":
            _inventory(state)
            _verify_files(last["files"], "SAVED_DRAFT_CHANGED")
        return _result(state, path, _terminal_result(state, result, last), reused=True, stop=True)
    if decision != "not_ready":
        inv = _inventory(state)
        require(result["ok"] and result.get("preview") and last["inventory_sha256"] == inv["file"]["sha256"],
                "DRAFT_NOT_READY", "A current valid draft, mapping and preview are required")
        _verify_files(last["files"], "SAVED_DRAFT_CHANGED")
        require(not last["coverage"]["issues"], "COVERAGE_REVIEW_REQUIRED", "Blocking coverage issues remain")
        if result.get("needs_review") or last.get("questions") or last["coverage"]["warnings"]:
            decision = "usable_with_questions"
    state.update(phase="finished", decision=decision, review_note=note.strip())
    _save(path, state)
    return _result(state, path, _terminal_result(state, result, last), stop=True)
