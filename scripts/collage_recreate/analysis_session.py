"""A shared two-attempt budget for draft CLI operations; never calls a model."""
from __future__ import annotations

import os
from pathlib import Path

from filelock import FileLock, Timeout

from .core import ToolError, digest, file_hash, read_json, require, write_json

ISSUES = ("structural", "missing_element", "wrong_assignment", "wrong_object", "customer_requirement")
DECISIONS = ("usable", "usable_with_questions", "not_ready")
LIMIT = 2


def state_path(task, reference):
    root = Path(task).resolve()
    pinned = os.environ.get("COLLAGE_ANALYSIS_TASK_ROOT")
    require(not pinned or root == Path(pinned).resolve(), "TASK_ROOT_MISMATCH",
            "Use the existing analysis task; changing directories does not start a new budget")
    source_hash = file_hash(reference)
    folder = root / ".state" / "analysis"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / (source_hash + ".json"), source_hash


def fingerprint(path):
    """Ignore JSON formatting, paths and review-only notes, not production decisions."""
    try:
        data = read_json(path)
    except ToolError:
        return "raw:" + file_hash(path) if Path(path).is_file() else "missing:" + str(Path(path).resolve())
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in ("review_notes", "questions")}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return digest(clean(data))


def _load(path, source_hash):
    if path.exists():
        state = read_json(path)
        require(state.get("version") == 1 and state.get("source_sha256") == source_hash,
                "STATE_INVALID", "Analysis state is incompatible; preserve it and stop")
        return state
    return {"version": 1, "source_sha256": source_hash, "phase": "new",
            "attempts": [], "decision": None, "review_note": None}


def _response(state, path, result, *, reused=False, stop=False):
    count = len(state["attempts"])
    terminal = state["phase"] == "finished"
    action = ("return_existing_result" if terminal else
              "view_preview_once_then_finish" if result.get("ok") and result.get("preview") else
              "one_targeted_correction_or_finish_not_ready")
    return {**result, "workflow": {
        "state": str(path), "attempts_used": count, "max_attempts": LIMIT,
        "remaining_attempts": max(0, LIMIT-count), "reused": reused,
        "stop_rechecking": stop or terminal or count >= LIMIT,
        "next_action": action, "decision": state.get("decision"),
        "preview_bundles": sum(bool((a.get("result") or {}).get("preview")) for a in state["attempts"]),
        "note": "needs_review is a warning, not a retry request; do not inspect implementation source",
    }}


def _error(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


def _finish_failed(state, path, code, message):
    state.update(phase="finished", decision="not_ready", review_note=message)
    write_json(path, state)
    return _response(state, path, _error(code, message), stop=True)


def run_draft_operation(task, input_path, reference, operation, *, output=None, reuse=None,
                        issue=None, reason=None):
    path, source_hash = state_path(task, reference)
    try:
        with FileLock(str(path)+".lock", timeout=0):
            state = _load(path, source_hash)
            key = fingerprint(input_path)
            attempts = state["attempts"]
            last = attempts[-1] if attempts else None
            if state["phase"] == "running":
                return _finish_failed(state, path, "CHECK_INTERRUPTED",
                                      "A previous check was interrupted; preserve its artifacts and stop")
            same = bool(last and key in last.get("fingerprints", []))
            if last and (last.get("result") or {}).get("ok") and (state["phase"] != "finished" or same):
                saved = Path(last["result"]["draft"])
                # A changed rough input may be the intentional correction. Generated
                # adopted drafts, or cached previews, must still match their check.
                source_is_saved = saved == Path(last["input"])
                correcting_source = source_is_saved and not same and state["phase"] != "finished"
                if not correcting_source and (not saved.is_file() or file_hash(saved) != last["draft_sha256"]):
                    return _finish_failed(state, path, "SAVED_DRAFT_CHANGED",
                        "The saved checked draft changed; preserve artifacts and report not_ready")
            if state["phase"] == "finished":
                if same and last.get("result", {}).get("ok"):
                    return _response(state, path, last["result"], reused=True, stop=True)
                return _response(state, path, _error("TASK_FINISHED",
                    "This reference analysis has ended; return its saved result and unresolved issues"), stop=True)
            if same:
                if not last["result"]["ok"]:
                    return _finish_failed(state, path, "UNCHANGED_FAILED_INPUT",
                                          "Input has not changed since the failed check; stop repeating it")
                # A standalone check may be upgraded once to an actual preview.
                needs_preview = operation != "check" and not last["result"].get("preview")
                if not needs_preview:
                    return _response(state, path, last["result"], reused=True, stop=True)
            else:
                needs_preview = False
            if len(attempts) >= LIMIT:
                return _response(state, path, _error("CHECK_LIMIT",
                    "Two checks have been used; finish with the existing draft or report not_ready"), stop=True)
            if last and not needs_preview:
                if issue not in ISSUES or not reason or not reason.strip():
                    return _response(state, path, _error("CORRECTION_REQUIRED",
                        "Only one concrete blocking correction is allowed: provide --issue and --reason, or finish"), stop=True)
            attempt = {"number": len(attempts)+1, "operation": operation, "input": str(Path(input_path).resolve()),
                       "fingerprints": [key], "issue": issue, "reason": reason, "result": None}
            attempts.append(attempt)
            state["phase"] = "running"
            write_json(path, state)  # Reserve before validation: failures and interruptions count too.
            try:
                if operation == "refine":
                    from .localize import refine_layout
                    if reuse is None and last and last["result"].get("report"):
                        reuse = str(Path(last["result"]["report"]).parent)
                    result = refine_layout(input_path, reference, output, reuse=reuse)
                elif operation == "preview":
                    from .analysis import preview_draft
                    result = preview_draft(input_path, reference, output)
                else:
                    require(operation == "check", "OPERATION_INVALID", "Use refine, check or preview")
                    from .analysis import check_draft
                    result = check_draft(input_path, reference)
            except ImportError:
                result = _error("DEPENDENCY_MISSING", "Required dependency is unavailable; stop and report the environment issue")
            except ToolError as exc:
                result = _error(exc.code, str(exc))
            except OSError:
                result = _error("FILE_IO", "Cannot read or write requested files; preserve existing artifacts")
            attempt["result"] = result
            state["phase"] = "awaiting_review" if result["ok"] else "needs_correction"
            if result["ok"]:
                artifact = Path(result["draft"])
                attempt["draft_sha256"] = file_hash(artifact)
                attempt["fingerprints"].append(fingerprint(artifact))
                attempt["questions"] = read_json(artifact).get("questions", [])
            elif len(attempts) >= LIMIT or result["error"]["code"] == "DEPENDENCY_MISSING":
                state.update(phase="finished", decision="not_ready", review_note=result["error"]["message"])
            write_json(path, state)
            return _response(state, path, result)
    except Timeout:
        raise ToolError("TASK_BUSY", "A check is already running for this reference; do not start another") from None


def finish_draft(task, reference, decision, note):
    require(decision in DECISIONS and bool(note.strip()), "DECISION_INVALID", "Choose a decision and record the actual review")
    path, source_hash = state_path(task, reference)
    try:
        with FileLock(str(path)+".lock", timeout=0):
            state = _load(path, source_hash)
            attempts = state["attempts"]
            require(bool(attempts), "CHECK_MISSING", "No checked draft exists; preserve the draft and report not_ready")
            last = attempts[-1]
            if state["phase"] == "finished":
                result = last.get("result") or _error("CHECK_INTERRUPTED", "The previous check did not finish")
                return _response(state, path, {**result, "decision": state["decision"]}, reused=True, stop=True)
            result = last.get("result") or _error("CHECK_INTERRUPTED", "The previous check did not finish")
            if decision != "not_ready":
                require(result["ok"] and result.get("preview"), "DRAFT_NOT_READY",
                        "A valid draft with a preview is required; finish not_ready instead")
                require(file_hash(result["draft"]) == last["draft_sha256"], "DRAFT_CHANGED",
                        "The reviewed draft changed after its check; do not claim it is ready")
                if result.get("needs_review") or last.get("questions"):
                    decision = "usable_with_questions"
            state.update(phase="finished", decision=decision, review_note=note.strip())
            write_json(path, state)
            return _response(state, path, {**result, "decision": decision,
                             "review_note": note.strip(), "unresolved": last.get("questions", [])}, stop=True)
    except Timeout:
        raise ToolError("TASK_BUSY", "A check is already running; do not finish it concurrently") from None
