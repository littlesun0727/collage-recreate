"""The deliberately static v1 contract."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")]
Size = Annotated[int, Field(ge=1, le=8192)]
Coord = Annotated[float, Field(ge=-16384, le=16384)]
Unit = Annotated[float, Field(ge=0, le=1)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Canvas(Strict):
    width: Size
    height: Size
    background: Color = "#ffffff"

    @model_validator(mode="after")
    def area(self):
        if self.width * self.height > 40_000_000:
            raise ValueError("Canvas exceeds 40 million pixels")
        return self


class Source(Strict):
    kind: Literal["image", "font"] = "image"
    path: str = Field(min_length=1)
    redistributable: bool = False
    license: str | None = None
    font_index: int = Field(default=0, ge=0, le=100)


class Asset(Source):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    alpha_range: list[int] | None = None


class Permissions(Strict):
    image_generation: bool = False
    max_requests: int = Field(default=0, ge=0, le=100)
    max_requests_per_asset: int = Field(default=1, ge=1, le=10)
    upload_asset_ids: list[ID] = Field(default_factory=list)


class Request(Strict):
    version: Literal[1] = 1
    canvas: Canvas
    reference: str = Field(min_length=1)
    assets: dict[ID, Source] = Field(default_factory=dict)
    texts: dict[ID, str] = Field(default_factory=dict)
    permissions: Permissions = Field(default_factory=Permissions)


class Group(Strict):
    id: ID
    dx: Coord = 0
    dy: Coord = 0


class Layer(Strict):
    id: ID
    x: Coord
    y: Coord
    width: Size
    height: Size
    group: ID | None = None
    rotation: float = Field(default=0, ge=-360, le=360)
    opacity: Unit = 1
    allow_clip: bool = False


class Picture(Layer):
    type: Literal["image"]
    asset: ID
    fit: Literal["cover", "contain"] = "cover"
    focus: list[Unit] = Field(default_factory=lambda: [0.5, 0.5], min_length=2, max_length=2)
    crop: list[Unit] | None = Field(default=None, min_length=4, max_length=4)
    mask_shape: Literal["ellipse", "rounded"] | None = None
    radius: int = Field(default=0, ge=0, le=4096)
    mask_asset: ID | None = None

    @model_validator(mode="after")
    def crop_and_mask(self):
        if self.crop and (self.crop[0] >= self.crop[2] or self.crop[1] >= self.crop[3]):
            raise ValueError("crop must have positive width and height")
        if self.mask_shape and self.mask_asset:
            raise ValueError("Use one mask type")
        return self


class Text(Layer):
    type: Literal["text"]
    text: ID
    font: ID
    font_size: int = Field(ge=1, le=2048)
    color: Color = "#000000"
    align: Literal["left", "center", "right"] = "left"
    wrap: bool = True
    line_spacing: float = Field(default=1.2, ge=1, le=4)


Element = Annotated[Picture | Text, Field(discriminator="type")]


class Project(Strict):
    version: Literal[1] = 1
    revision: int = Field(default=1, ge=1)
    canvas: Canvas
    reference: ID | None = None
    assets: dict[ID, Asset] = Field(default_factory=dict)
    texts: dict[ID, str] = Field(default_factory=dict)
    groups: list[Group] = Field(default_factory=list, max_length=256)
    elements: list[Element] = Field(default_factory=list, max_length=1024)
    permissions: Permissions = Field(default_factory=Permissions)

    @model_validator(mode="after")
    def links(self):
        ids = [x.id for x in self.elements] + [g.id for g in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("Element and group IDs must be unique")
        groups = {g.id for g in self.groups}
        for element in self.elements:
            if element.group is not None and element.group not in groups:
                raise ValueError(f"Missing group for {element.id}")
            if isinstance(element, Picture):
                refs = [element.asset] + ([element.mask_asset] if element.mask_asset else [])
                if any(ref not in self.assets or self.assets[ref].kind != "image" for ref in refs):
                    raise ValueError(f"Missing image asset for {element.id}")
            else:
                if element.text not in self.texts:
                    raise ValueError(f"Missing text for {element.id}")
                if element.font not in self.assets or self.assets[element.font].kind != "font":
                    raise ValueError(f"Missing font for {element.id}")
        if self.reference is not None and (
            self.reference not in self.assets or self.assets[self.reference].kind != "image"
        ):
            raise ValueError("Missing reference image")
        return self


class Patch(Strict):
    expected_revision: int = Field(ge=1)
    canvas: Canvas | None = None
    assets: dict[ID, Source] = Field(default_factory=dict)
    texts: dict[ID, str] = Field(default_factory=dict)
    elements: list[Element] | None = None
    groups: list[Group] | None = None
    element_updates: dict[ID, dict] = Field(default_factory=dict)
    group_updates: dict[ID, dict] = Field(default_factory=dict)


class Reference(Strict):
    asset: ID
    role: str = Field(min_length=1, max_length=200)


class Generation(Strict):
    asset_id: ID
    prompt: str = Field(min_length=1, max_length=12000)
    references: list[Reference] = Field(default_factory=list, max_length=14)
    size: str = Field(default="1024x1024", pattern=r"^[1-9][0-9]{2,3}x[1-9][0-9]{2,3}$")
