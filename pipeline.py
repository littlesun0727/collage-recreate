"""Run one collage case from its inputs/catalog.json and inputs/photos/."""
import argparse
import json
import re
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from collage import light_binding, pipeline, preview, vlm
from collage.timings import Timings, log
from collage.storage import save, sha


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate catalog key: {key}")
        result[key] = value
    return result


def prepare(case, artifacts):
    inputs = case/"inputs"
    with (inputs/"catalog.json").open(encoding="utf-8-sig") as source:
        manifest = json.load(source, object_pairs_hook=unique_keys)
    materials = manifest["materials"]
    log(f"已读取 {inputs/'catalog.json'}，开始检查素材编号和图片路径")
    if not isinstance(materials, dict):
        raise ValueError("catalog.materials must map material IDs to filenames")
    for ident in materials:
        if not re.fullmatch(r"materials_[0-9]+", ident):
            raise ValueError(f"Invalid material ID: {ident}")
    photos = (inputs/"photos").resolve(strict=True)
    prepared = artifacts/"prepared"
    prepared.mkdir()
    sources = {}
    for ident, name in [("reference", manifest["reference"]), *materials.items()]:
        if not isinstance(name, str) or not name.strip() or Path(name).name != name or any(c in name for c in '/\\:') or name in (".", ".."):
            raise ValueError(f"{ident}: expected a filename directly inside inputs/photos, got {name!r}")
        source = (photos/name).resolve()
        if source.parent != photos:
            raise ValueError(f"{ident}: image outside inputs/photos: {name}")
        if not source.is_file():
            raise ValueError(f"{ident}: 找不到图片 {name}")
        sources[ident] = source
    reference = sources.pop("reference")
    log(f"校正全尺寸参考图 {reference.name}")
    size = pipeline.prepare_image(reference, prepared/"reference.png", allow_mpo=True)
    return size, reference.name, sources


def prepare_materials(artifacts, reference, sources, timings):
    with timings.stage("prepare_materials"):
        catalog = pipeline.run_stage("prepare_materials", write_thumbnails, artifacts, reference, sources)
    with timings.stage("material_classification"):
        return pipeline.run_stage("material_classification", light_binding.classify, artifacts, catalog)


def write_thumbnails(artifacts, reference, sources):
    catalog = []
    for ident, source in sources.items():
        path = artifacts/"prepared"/(ident + ".jpg")
        log(f"生成选片缩略图 {ident} ← {source.name}")
        try:
            with pipeline.oriented_photo(source) as image:
                dims = list(image.size)
                vlm.save_binding_jpeg(image, path)
                log(f"写入 prepared/{path.name}，原图 {dims}，缩略图 {image.size}")
            catalog.append({"id": ident, "asset_id": ident, "filename": source.name,
                            "binding_preview": path.name, "size": dims, "sha256": sha(source)})
        except Exception as exc:
            raise ValueError(f"{ident} ({source.name}): {exc}") from exc
    save(artifacts/"catalog.json", {"reference": reference, "materials": catalog}, indent=2)
    return catalog


def archive_previous(artifacts, outputs):
    # A fresh invocation must never publish an old bundle alongside a new analysis.
    previous = artifacts/"history"/uuid4().hex
    for name in ("delivery.zip", "scene.json", "analysis.json"):
        path = outputs/name
        if path.exists():
            (previous/"outputs").mkdir(parents=True, exist_ok=True)
            path.rename(previous/"outputs"/name)
    for name in ("status.json", "prepared", "catalog.json", "bindings.json", "reference_clean.png",
                 "reveal", "fixed", "fixed-index.json", "lettering.json", "scene.tmp", "delivery.tmp",
                 "cutouts", "contact-sheets", "material-classification.json", "analysis-bbox.png", "final.png", "timings.json", "preview-errors.json", "pipeline.log"):
        path = artifacts/name
        if path.exists():
            previous.mkdir(parents=True, exist_ok=True)
            path.rename(previous/name)


def run_case(case_dir, output_dir=None):
    case = Path(case_dir).resolve(strict=True)
    root = case if output_dir is None else Path(output_dir).resolve()
    artifacts, outputs = root/"artifacts", root/"outputs"
    if output_dir is not None and any(path.resolve().is_relative_to(case)
                                      for path in (root, artifacts, outputs)):
        raise ValueError("Choose an output_dir outside case_dir, or omit it for an in-place run")
    artifacts.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    lock = artifacts/".running"
    try:
        lock.touch(exist_ok=False)
    except FileExistsError as exc:
        raise RuntimeError(f"Case locked: {lock}") from exc
    try:
        archive_previous(artifacts, outputs)
        timings = Timings(artifacts, case.name)
        timings.emit("case", f"启动，输入 {case/'inputs'}，日志 {timings.log_path}")
        issues = {}
        def draw(name, fn, *args):
            try:
                with timings.stage(name):
                    fn(*args)
            except Exception as exc:
                issues[name] = str(exc)
                save(artifacts/"preview-errors.json", issues, indent=2)

        stage = "prepare"
        try:
            save(artifacts/"status.json", {"status": "processing", "stage": stage})
            with timings.stage(stage):
                size, reference, originals = prepare(case, artifacts)
            with ThreadPoolExecutor(max_workers=1) as worker:
                materials = worker.submit(prepare_materials, artifacts, reference, originals, timings)
                stage = "analysis"
                save(artifacts/"status.json", {"status": "processing", "stage": stage})
                with timings.stage(stage):
                    analysis = vlm.analyze(artifacts, size)
                save(outputs/"analysis.json", analysis, indent=2)
                timings.emit(stage, f"已写入 outputs/analysis.json，对象数 {len(analysis['objects'])}")
                draw("analysis_bbox", preview.analysis_bbox, artifacts, analysis)
                stage = "assemble"
                save(artifacts/"status.json", {"status": "processing", "stage": stage})
                pipeline.assemble(artifacts, outputs, analysis, materials, timings, originals)
            draw("final_preview", preview.final, artifacts, outputs, originals)
            save(artifacts/"status.json", {"status": "ready"})
            timings.emit("case", f"交付完成 {outputs}；预览问题 {len(issues)} 项")
        except Exception as exc:
            save(artifacts/"status.json", {"status": "failed", "stage": getattr(exc, "stage", stage),
                                          "error": str(exc)})
            raise
        finally:
            timings.finish()
            timings.emit("case", f"本次运行结束，端到端 {timings.data['e2e_seconds']:.3f}s")
    finally:
        lock.unlink()
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("output_dir", type=Path, nargs="?", help="root for outputs/ and artifacts/")
    destination.add_argument("--output-dir", "--output_dir", type=Path, dest="output_option")
    from collage.api_config import configure
    parser.add_argument("--api-config", type=Path, help="API settings JSON (default: project api-config.json)")
    args = parser.parse_args()
    try:
        configure(args.api_config)
        print(run_case(args.case_dir, args.output_option or args.output_dir))
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
