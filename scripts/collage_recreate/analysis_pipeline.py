"""Inventory-to-draft provenance and deterministic coverage checks."""
from collections import Counter, defaultdict
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .models import ID, Strict
from .inventory import Version, Words, validate_inventory
from .core import parse, read_json, require
from .analysis import SlotBackground, validate_draft

class Binding(Strict):
    type: Literal["slot", "text", "overlay", "background"]
    id: ID | None = None
    source_objects: list[ID] = Field(default_factory=list)
    origin: Literal["reference", "user", "default"] = "reference"
    reason: str = ""
    split_reason: str = ""

    @model_validator(mode="after")
    def target(self):
        if (self.type == "background") != (self.id is None):
            raise ValueError("background has no id; other targets require id")
        if len(set(self.source_objects)) != len(self.source_objects):
            raise ValueError("source_objects must be unique within a binding")
        if self.origin == "reference" and not self.source_objects:
            raise ValueError("reference targets require source_objects")
        if self.origin != "reference" and (self.source_objects or not self.reason.strip()):
            raise ValueError("user/default targets need a reason and no invented source objects")
        if self.origin == "default" and self.type != "background":
            raise ValueError("only a fixed background may have default origin")
        return self

class Ignored(Strict):
    object_id: ID
    reason: Words

class Unresolved(Strict):
    object_id: ID
    question: Words
    blocking: bool = True

class DraftMapping(Strict):
    version: Version
    bindings: list[Binding]
    ignored: list[Ignored] = Field(default_factory=list)
    unresolved: list[Unresolved] = Field(default_factory=list)

def issue(stage, code, ids, evidence, action):
    return {"stage": stage, "code": code, "object_ids": sorted(set(ids)),
            "evidence": evidence, "suggested_action": action}

def validate_draft_against_inventory(inventory_path, draft_path, mapping_path, reference):
    inventory = validate_inventory(inventory_path)
    draft, _ = validate_draft(draft_path, reference)
    mapping = parse(DraftMapping, read_json(mapping_path))
    objects = {o.id: o for o in inventory.objects}
    targets = {("slot", s.id) for s in draft.slots} | {("text", t.id) for t in draft.texts} | {
        ("overlay", o.id) for o in draft.overlays}
    bg = draft.background.slot_id if isinstance(draft.background, SlotBackground) else None
    if bg is None:
        targets.add(("background", None))
    actual = [(b.type, b.id) for b in mapping.bindings]
    require(len(set(actual)) == len(actual), "MAPPING_DUPLICATE", "Each production target has one binding")
    require(set(actual) == targets, "MAPPING_TARGET_INVALID",
            "Every actual production item needs one mapping; do not map nonexistent targets")
    consumed = defaultdict(list)
    issues, warnings = [], []
    for binding in mapping.bindings:
        for oid in binding.source_objects:
            require(oid in objects, "MAPPING_SOURCE_INVALID", f"Unknown inventory object: {oid}")
            consumed[oid].append(binding)
        members = [objects[oid] for oid in binding.source_objects]
        photo_ids = [o.id for o in members if o.visual_type == "photo"]
        is_bg = binding.type == "background" or (binding.type == "slot" and binding.id == bg)
        if len(photo_ids) > 1 and (binding.type != "slot" or is_bg):
            issues.append(issue("draft", "POSSIBLE_SLOT_OMISSION", photo_ids,
                "Several independently observed photos share one background/decoration.",
                "Review each photo's editable destination; preserve distinct replaceable subjects."))
        if len(photo_ids) > 1 and binding.type == "slot" and not is_bg:
            issues.append(issue("draft", "POSSIBLE_SLOT_OMISSION", photo_ids,
                "Several distinct photo objects share one customer slot.", "Separate independently replaceable photos."))
        for obj in members:
            risk = None
            if obj.visual_type == "photo" and (is_bg or binding.type != "slot"):
                risk = "POSSIBLE_WRONG_BACKGROUND" if is_bg else "POSSIBLE_SLOT_OMISSION"
            elif obj.visual_type in ("decoration", "shape", "line") and binding.type == "slot":
                risk = "POSSIBLE_WRONG_SLOT"
            elif obj.visual_type == "unknown":
                risk = "UNKNOWN_UNRESOLVED"
            if risk:
                row = issue("draft", risk, [obj.id], f"{obj.visual_type} mapped to {binding.type}:{binding.id}",
                            "Review this assignment against the image and task; explain a justified exception in reason.")
                # A typed assignment with an explicit explanation retains a visible warning,
                # never silently converts it to a visual pass.
                (warnings if binding.reason.strip() else issues).append(row)
    ignored = {o.object_id: o for o in mapping.ignored}
    unresolved = {o.object_id: o for o in mapping.unresolved}
    require(len(ignored) == len(mapping.ignored) and len(unresolved) == len(mapping.unresolved),
            "MAPPING_DUPLICATE", "Ignored/unresolved objects cannot repeat")
    for oid in set(ignored) | set(unresolved):
        require(oid in objects, "MAPPING_SOURCE_INVALID", f"Unknown inventory object: {oid}")
        require(not consumed[oid] and not (oid in ignored and oid in unresolved),
                "MAPPING_CONFLICT", f"{oid}: choose a binding, ignored reason, or unresolved question")
    for oid, obj in objects.items():
        bindings = consumed[oid]
        if len(bindings) > 1:
            require(all(b.split_reason.strip() for b in bindings), "MAPPING_CONFLICT",
                    f"{oid}: multiple targets require explicit split_reason on each binding")
        if not bindings and oid not in ignored and oid not in unresolved:
            issues.append(issue("draft", "OBJECT_DROPPED", [oid], "No production destination or explicit disposition.",
                                "Assign this object, or record an ignored reason / unresolved question."))
        if oid in ignored and obj.visual_type in ("photo", "unknown"):
            warnings.append(issue("draft", "IGNORED_IMPORTANT_OBJECT", [oid], ignored[oid].reason,
                                  "Verify the explicit exclusion against the task."))
        if oid in unresolved:
            row = issue("inventory" if obj.visual_type == "unknown" else "draft",
                        "UNKNOWN_UNRESOLVED", [oid], unresolved[oid].question,
                        "Resolve the blocking uncertainty once, or finish not_ready.")
            (issues if unresolved[oid].blocking else warnings).append(row)
    return {"ok": True, "coverage_status": "needs_revision" if issues else "covered",
            "issues": issues, "warnings": warnings, "object_count": len(objects),
            "mapped_count": sum(bool(consumed[oid]) for oid in objects),
            "ignored_count": len(ignored), "unresolved_count": len(unresolved),
            "visual_status": "unreviewed",
            "unresolved": [u.question for u in mapping.unresolved],
            "inventory_questions": inventory.questions}

def assert_geometry_only(previous, current, object_ids):
    """Only explicitly named production items may change coordinate fields."""
    from copy import deepcopy
    before, after = deepcopy(previous), deepcopy(current)
    require(bool(object_ids), "REVIEW_SCOPE", "Geometry correction requires --object-id")
    known = {item["id"] for cat in ("slots", "texts", "overlays") for item in before.get(cat, [])}
    require(set(object_ids) <= known, "REVIEW_SCOPE", "Unknown geometry correction target")
    for data in (before, after):
        for cat in ("slots", "texts", "overlays"):
            for item in data.get(cat, []):
                if item.get("id") in object_ids:
                    item.pop("source_rect", None)
                    item.pop("source_bbox_1000", None)
    require(before == after, "REVIEW_SCOPE",
            "Geometry-only correction may change only the named coordinates; keep other content unchanged")
