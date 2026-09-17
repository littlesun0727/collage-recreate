"""Task files, immutable imported assets, and atomic project updates."""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PureWindowsPath
import tempfile

from filelock import FileLock, Timeout
from PIL import Image, ImageCms, ImageOps
from pydantic import ValidationError

from .models import Asset, Patch, Project, Request, Source


class ToolError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise ToolError(code, message)


def parse(model, data):
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        # Pydantic's default string includes input values; do not echo those.
        issues = [".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors(include_input=False)]
        raise ToolError("INPUT_INVALID", "; ".join(issues[:8])) from None


def read_json(path):
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            return json.load(stream, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except FileNotFoundError:
        raise ToolError("FILE_MISSING", f"Missing file: {Path(path).name}") from None
    except (ValueError, UnicodeError, OSError):
        raise ToolError("JSON_INVALID", f"Cannot read JSON: {Path(path).name}") from None


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(data):
    return hashlib.sha256(canonical(data)).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, data):
    write_bytes(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n")


def relative_path(root, value, *, exists=True):
    require(isinstance(value, str) and value != "", "PATH_INVALID", "Expected a relative file path")
    raw = value.replace("\\", "/")
    require(
        not PureWindowsPath(raw).drive and not raw.startswith("/") and ":" not in raw
        and all(part not in ("", ".", "..") for part in raw.split("/")),
        "PATH_INVALID", "Resource paths must stay inside the task",
    )
    root = Path(root).resolve()
    target = (root / raw).resolve()
    require(target.is_relative_to(root) and target != root, "PATH_INVALID", "Resource escapes the task")
    if exists:
        require(target.is_file(), "FILE_MISSING", f"Missing resource: {raw}")
    return target


def load_image(path):
    try:
        with Image.open(path) as raw:
            require(getattr(raw, "n_frames", 1) == 1, "STATIC_ONLY", "Use a single static image")
            require(raw.width * raw.height <= 40_000_000, "IMAGE_LIMIT", "Image exceeds 40 million pixels")
            oriented = ImageOps.exif_transpose(raw)
            rgba = oriented.convert("RGBA")  # Also preserves palette/tRNS transparency.
            icc = oriented.info.get("icc_profile")
            if icc:
                rgb = ImageCms.profileToProfile(
                    oriented.convert("RGB"), ImageCms.ImageCmsProfile(BytesIO(icc)),
                    ImageCms.createProfile("sRGB"), outputMode="RGB",
                )
                rgb.putalpha(rgba.getchannel("A"))
                rgba = rgb
            rgba.load()
            rgba.info.clear()
            return rgba
    except ToolError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, ImageCms.PyCMSError):
        raise ToolError("IMAGE_INVALID", "Cannot decode image or convert its color profile") from None


def png_bytes(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def resolve_input(base, value):
    path = Path(value)
    path = path if path.is_absolute() else Path(base) / path
    require(path.is_file(), "FILE_MISSING", f"Input file missing: {path.name}")
    return path.resolve()


def import_source(root, source, base):
    path = resolve_input(base, source.path)
    content = path.read_bytes()
    original_hash = hashlib.sha256(content).hexdigest()
    suffix = path.suffix.lower()
    # Keep source data private and immutable; rendered/exported images use normalized bytes.
    write_bytes(relative_path(root, f"private/originals/{original_hash}{suffix}", exists=False), content)
    if source.kind == "image":
        im = load_image(path)
        content = png_bytes(im)
        sha = hashlib.sha256(content).hexdigest()
        relative = f"assets/{sha}.png"
        alpha = list(im.getchannel("A").getextrema())
    else:
        from PIL import ImageFont
        try:
            ImageFont.truetype(str(path), 12, index=source.font_index)
        except (OSError, ValueError):
            raise ToolError("FONT_INVALID", f"Cannot load font: {path.name}") from None
        sha = original_hash
        relative = f"assets/{sha}{suffix}"
        alpha = None
    write_bytes(relative_path(root, relative, exists=False), content)
    license_relative = None
    if source.kind == "font" and source.redistributable:
        require(bool(source.license), "FONT_LICENSE_REQUIRED", "Redistributable fonts need a license file")
        license_source = resolve_input(base, source.license)
        license_data = license_source.read_bytes()
        license_relative = f"assets/licenses/{hashlib.sha256(license_data).hexdigest()}.txt"
        write_bytes(relative_path(root, license_relative, exists=False), license_data)
    return Asset(
        kind=source.kind, path=relative, sha256=sha, alpha_range=alpha,
        redistributable=source.redistributable, license=license_relative, font_index=source.font_index,
    )


class Task:
    def __init__(self, root):
        self.root = Path(root).resolve()

    @property
    def project_path(self):
        return self.root / "project.json"

    def load(self):
        return parse(Project, read_json(self.project_path))

    def save(self, project):
        write_json(self.project_path, project.model_dump(mode="json"))

    def lock(self):
        require(self.root.is_dir(), "TASK_MISSING", "Initialize the task first")
        target = relative_path(self.root, ".state/task.lock", exists=False)
        target.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(target, timeout=0)

    def asset_path(self, asset, *, verify=True):
        path = relative_path(self.root, asset.path)
        if verify:
            require(file_hash(path) == asset.sha256, "ASSET_CHANGED", "Asset content changed; import it with apply")
        return path


def initialize(task, input_path):
    request = parse(Request, read_json(input_path))
    require("reference" not in request.assets, "ID_RESERVED", "The reference asset ID is reserved")
    require(not task.project_path.exists(), "TASK_EXISTS", "This task already contains a project")
    task.root.mkdir(parents=True, exist_ok=True)
    with task.lock():
        require(not task.project_path.exists(), "TASK_EXISTS", "This task already contains a project")
        base = Path(input_path).resolve().parent
        assets = {"reference": import_source(task.root, Source(path=request.reference), base)}
        assets.update({key: import_source(task.root, value, base) for key, value in request.assets.items()})
        require(set(request.permissions.upload_asset_ids) <= set(assets), "INPUT_INVALID", "Unknown upload asset ID")
        project = Project(
            canvas=request.canvas, reference="reference", assets=assets,
            texts=request.texts, permissions=request.permissions,
        )
        task.save(project)
    return summary(task, project)


def summary(task, project=None):
    project = project or task.load()
    return {
        "project": str(task.project_path), "revision": project.revision,
        "canvas": project.canvas.model_dump(), "assets": list(project.assets),
        "texts": project.texts, "elements": [e.id for e in project.elements],
        "groups": [g.id for g in project.groups], "visual_status": "unreviewed",
    }


def apply_patch(task, input_path, font_overrides=None):
    patch = parse(Patch, read_json(input_path))
    with task.lock():
        project = task.load()
        require(project.revision == patch.expected_revision, "REVISION_CONFLICT", "Reload project.json and use its revision")
        data = project.model_dump(mode="json")
        data["revision"] += 1
        if patch.canvas is not None:
            data["canvas"] = patch.canvas.model_dump()
        for key, source in patch.assets.items():
            data["assets"][key] = import_source(task.root, source, Path(input_path).resolve().parent).model_dump()
        data["texts"].update(patch.texts)
        for collection, updates in (("elements", patch.element_updates), ("groups", patch.group_updates)):
            replacement = getattr(patch, collection)
            if replacement is not None:
                data[collection] = [e.model_dump() for e in replacement]
            entries = {e["id"]: e for e in data[collection]}
            for key, update in updates.items():
                require(key in entries, "ID_MISSING", f"Unknown {collection} ID: {key}")
                require("id" not in update or update["id"] == key, "ID_IMMUTABLE", "Do not rename stable IDs")
                entries[key].update(update)
        updated = parse(Project, data)
        if updated.elements:
            from .render import compose
            compose(task, updated, font_overrides=font_overrides)
        task.save(updated)
    return summary(task, updated)
