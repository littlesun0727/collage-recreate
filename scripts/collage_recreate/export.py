"""Portable exports contain only runtime code and used assets."""
from pathlib import Path
import shutil
import tempfile

from .core import png_bytes, require, relative_path, write_bytes, write_json
from .models import Picture, Project
from .render import compose


def export_task(task, destination, *, font_overrides=None):
    destination = Path(destination).resolve()
    require(not destination.exists(), "EXPORT_EXISTS", "Choose a new export directory")
    require(not destination.is_relative_to(task.root), "EXPORT_LOCATION", "Export outside the source task")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with task.lock():
        project = task.load()
        image, _ = compose(task, project, font_overrides=font_overrides)
        data = project.model_dump(mode="json")
        used = set()
        for layer in project.elements:
            if isinstance(layer, Picture):
                used.add(layer.asset)
                if layer.mask_asset:
                    used.add(layer.mask_asset)
            else:
                used.add(layer.font)
        data["assets"] = {k: v for k, v in data["assets"].items() if k in used}
        data["reference"] = None
        data["permissions"] = {"image_generation": False, "max_requests": 0, "max_requests_per_asset": 1, "upload_asset_ids": []}
        staging = Path(tempfile.mkdtemp(prefix=".collage-export-", dir=destination.parent))
        missing_fonts = {}
        try:
            for key, asset in project.assets.items():
                if key not in used:
                    continue
                if asset.kind == "font" and not asset.redistributable:
                    missing_fonts[key] = {"filename": Path(asset.path).name, "font_index": asset.font_index,
                                          "sha256": asset.sha256, "instruction": f"--font {key}=LOCAL_FONT_PATH"}
                    continue
                source = task.asset_path(asset)
                write_bytes(relative_path(staging, asset.path, exists=False), source.read_bytes())
                if asset.kind == "font":
                    require(bool(asset.license), "FONT_LICENSE_REQUIRED", "Font license missing")
                    license_path = relative_path(task.root, asset.license)
                    write_bytes(relative_path(staging, asset.license, exists=False), license_path.read_bytes())
            portable = Project.model_validate(data)
            write_json(staging / "project.json", portable.model_dump(mode="json"))
            write_bytes(staging / "cover.png", png_bytes(image))
            write_json(staging / "font-requirements.json", missing_fonts)
            root = Path(__file__).resolve().parents[2]
            runtime = staging / "runtime"
            runtime.mkdir()
            package = runtime / "collage_recreate"
            package.mkdir()
            for path in Path(__file__).resolve().parent.glob("*.py"):
                shutil.copy2(path, package / path.name)
            shutil.copy2(Path(__file__).resolve().parent.parent / "run.py", runtime / "run.py")
            shutil.copy2(root / "requirements.txt", staging / "requirements.txt")
            write_bytes(staging / "RUN.md", (
                "# Editable static collage\n\n"
                "Install Python 3.11+ and dependencies in your own environment:\n\n"
                "    python -m pip install -r requirements.txt\n"
                "    python runtime/run.py render --task .\n\n"
                "If fonts are listed in font-requirements.json, provide each explicitly:\n\n"
                "    python runtime/run.py render --task . --font FONT_ID=LOCAL_FONT_PATH\n\n"
                "Edit text through texts, replace image assets with apply, and move groups with dx/dy.\n"
                "Use the included project-format.md for edit examples. Bitmap strokes are not separately editable.\n"
                "cover.png has not been visually accepted automatically. No image-service credentials are included.\n"
            ).encode("utf-8"))
            format_path = root / "references" / "project.md"
            if not format_path.exists():
                format_path = root / "project-format.md"
            if format_path.exists():
                shutil.copy2(format_path, staging / "project-format.md")
            staging.rename(destination)
        except Exception:
            if staging.is_relative_to(destination.parent) and staging.name.startswith(".collage-export-"):
                shutil.rmtree(staging)
            raise
    return {"export": str(destination), "output": str(destination / "cover.png"),
            "font_requirements": list(missing_fonts), "visual_status": "unreviewed"}
