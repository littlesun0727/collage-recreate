"""Run a directory of collage cases and write an offline visual report."""
import argparse
import html
import os
import shutil
import stat
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from filelock import FileLock, Timeout

from pipeline import run_case
from collage.storage import read, save
from collage.api_config import configure


def load(path):
    return read(path) if path.is_file() else {}


def report(root, cases, results, elapsed):
    cards = []
    for case in cases:
        name = case.name
        folder = root/name/"artifacts"
        status = results.get(name, "待完成")
        timings = load(folder/"timings.json")
        issue = load(folder/"preview-errors.json")
        pictures = []
        for label, file in (("参考图", "prepared/reference.png"), ("分析框与照片轮廓", "analysis-bbox.png"), ("最终拼贴", "final.png")):
            url = quote(name+"/artifacts/"+file)
            media = f'<a href="{url}"><img loading="lazy" src="{url}" alt="{label}"></a>' if (folder/file).is_file() else '<p>暂无图片</p>'
            pictures.append(f'<figure><figcaption>{label}</figcaption>{media}</figure>')
        rows = []
        calls = timings.get("requests", [])
        for stage in ("prepare","prepare_materials","analysis","analysis_bbox","binding","reveal","reference_clean","delivery","final_preview"):
            item = timings.get("stages", {}).get(stage)
            attempts = [r for r in calls if r["stage"] == stage]
            durations = " / ".join(f'{r["method"]} {r["seconds"]:.2f}s' + ("" if r["ok"] else " 失败") for r in attempts)
            seconds = f'{item["seconds"]:.2f}s' if item else "—"
            rows.append(f'<tr><td>{stage}</td><td>{seconds}</td><td>{durations or "—"}</td></tr>')
        total = timings.get("e2e_seconds")
        duration = f"{total:.2f}s" if total is not None else "—"
        notes = html.escape("；".join(f"{k} · {v}" for k,v in issue.items()))
        if (folder/"pipeline.log").is_file():
            notes += f' <a href="{quote(name)}/artifacts/pipeline.log">查看运行日志</a>'
        cards.append(f"<article><h2>{html.escape(name)}</h2><p>{html.escape(status)}　端到端 {duration}</p><section>{''.join(pictures)}</section><p>{notes}</p><table><tr><th>阶段</th><th>阶段耗时</th><th>每次 HTTP 请求（含读取响应）</th></tr>{''.join(rows)}</table></article>")
    head = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>拼贴批测</title>
<style>*{box-sizing:border-box}body{margin:24px auto;padding:0 16px;max-width:1250px;background:#f4f5f5;color:#1c2930;font:16px system-ui}article{background:white;padding:18px;margin:18px 0;border-radius:12px}section{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}figure{margin:0}figcaption,th{font-weight:600}img{width:100%;height:310px;object-fit:contain;background:#eee}table{border-collapse:collapse;width:100%}td,th{text-align:left;border-bottom:1px solid #ddd;padding:4px}h2{margin:0}@media(max-width:650px){section{grid-template-columns:1fr}}</style><h1>拼贴批测</h1>"""
    page = head + f"<p>已结束 {len(results)} / {len(cases)}　批次经过 {elapsed:.1f}s</p><p>橙色为 bbox，青色为照片窗口轮廓。并行阶段耗时会重叠。点击图片查看原尺寸。</p>" + "".join(cards)+"</html>"
    temp = root/(uuid4().hex+".tmp")
    temp.write_text(page, encoding="utf-8")
    temp.replace(root/"index.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", "--case_dir", type=Path, required=True)
    parser.add_argument("--output-dir", "--output_dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--api-config", type=Path, help="API settings JSON (default: project api-config.json)")
    args = parser.parse_args()
    source, root = args.case_dir.resolve(), args.output_dir.resolve()
    if args.workers < 1 or root.is_relative_to(source) or source.is_relative_to(root):
        parser.error("Use separate input/output trees and at least one worker")
    cases = [source] if (source/"inputs").is_dir() else sorted(p for p in source.iterdir() if p.is_dir() and (p.name.startswith("case-") or (p/"inputs").is_dir()))
    if not cases:
        parser.error("No case directories found")
    repo = Path(__file__).resolve().parent
    if root == Path(root.anchor) or repo.is_relative_to(root) or root == Path.home().resolve():
        parser.error(f"不能清空工程目录、主目录或磁盘根目录：{root}")
    if args.output_dir.absolute() != root:
        parser.error(f"请直接指定输出目录的实际路径：{root}")
    root.mkdir(parents=True, exist_ok=True)
    try:
        configure(args.api_config)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    lock_path = root/".benchmark.lock"
    try:
        with FileLock(lock_path, timeout=0):
            previous = [child for child in root.iterdir() if child != lock_path]
            if previous:
                print(f"清空旧批测产物：{root}", flush=True)
            for child in previous:
                # Remove a link itself. Never traverse its target during cleanup.
                attrs = getattr(child.lstat(), "st_file_attributes", 0)
                if child.is_symlink():
                    child.unlink()
                elif attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT and child.is_dir():
                    child.rmdir()
                elif child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            return run_batch(root, cases, args.workers)
    except Timeout:
        parser.error(f"已有批测正在使用输出目录：{root}")


def run_batch(root, cases, workers):
    print(f"批测启动：{len(cases)} 个 case，并发 {workers}，报告 {root/'index.html'}", flush=True)
    results = {}
    start = time.perf_counter()
    report(root, cases, results, 0)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(run_case, case, root/case.name): case for case in cases}
        for job in as_completed(pending):
            name = pending[job].name
            try:
                job.result()
                status = "交付完成"
            except Exception as exc:
                failure = load(root/name/"artifacts/status.json")
                status = f"失败 · {failure.get('stage', '启动')} · {exc}"
            results[name] = status
            print(f"[{len(results)}/{len(cases)}] {name} {status}", flush=True)
            report(root, cases, results, time.perf_counter()-start)
    save(root/"summary.json", {"batch_seconds": round(time.perf_counter()-start,3),
         "analysis_model": os.environ["COLLAGE_VLM_MODEL"], "binding_model": os.environ["COLLAGE_BIND_MODEL"],
         "cases": results}, indent=2)
    return int(any(s.startswith("失败") for s in results.values()))


if __name__ == "__main__":
    sys.exit(main())
