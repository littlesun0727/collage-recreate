"""Visual observations only: no geometry, production decisions or model calls."""
from typing import Annotated, Literal
from pydantic import Field
from .models import ID, Strict
from .core import parse, read_json, require

Words = Annotated[str, Field(min_length=1, pattern=r"\S")]
Version = Annotated[int, Field(ge=1, le=1)]
VisualType = Literal["photo", "text", "decoration", "shape", "line", "background_candidate", "unknown"]

class VisualObject(Strict):
    id: ID
    visual_type: VisualType
    description: Words
    text_content: str | None
    confidence: Literal["high", "medium", "low"]

class Inventory(Strict):
    version: Version
    objects: Annotated[list[VisualObject], Field(min_length=1)]
    questions: list[Words]

def validate_inventory(input_path):
    inventory = parse(Inventory, read_json(input_path))
    ids = [item.id for item in inventory.objects]
    require(len(ids) == len(set(ids)), "ID_DUPLICATE", "Inventory object IDs must be unique")
    return inventory
