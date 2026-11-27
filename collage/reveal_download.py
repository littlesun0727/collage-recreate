"""Download one usable PNG per index, sharing a pool across all active cases."""
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from http.client import IncompleteRead
from threading import Lock
from urllib.error import HTTPError

from PIL import Image
from .timings import log


_pool_lock = Lock()
_pool = None


def download_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            count = int(os.getenv("COLLAGE_REVEAL_DOWNLOAD_WORKERS", "5"))
            _pool = ThreadPoolExecutor(max_workers=count, thread_name_prefix="reveal-download")
        return _pool


def validate_png(path, index, size):
    with Image.open(path) as image:
        if image.format != "PNG" or abs(image.width / image.height / (size[0] / size[1]) - 1) > .02:
            raise ValueError("Invalid Reveal PNG format or canvas")
        image.load()
        if index and ("A" not in image.getbands() or not image.getchannel("A").getbbox()):
            raise ValueError("Missing visible alpha layer")


def fetch_png(path, url, index, size, get, deadline, sleep):
    if path.exists():
        try:
            validate_png(path, index, size)
            return
        except (OSError, ValueError, SyntaxError):
            pass
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError("Reveal download deadline reached")
    response = get(url, timeout=(min(30, left), min(120, left)))
    response.raise_for_status()
    tmp = path.with_suffix(".part")
    for attempt in range(4):
        try:
            tmp.write_bytes(response.content)
            validate_png(tmp, index, size)
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 3:
                raise
            sleep(.15 * 2**attempt)


def download_layers(task, output, size, get, deadline, sleep):
    urls = [output.get("layers_aug") or [], output.get("layers_base") or []]
    total = max(len(urls[0]), len(urls[1]), 1 + len(output.get("boxes_mapping_index", [])))
    pool = download_pool()
    pending = {}
    delayed = []
    errors = []
    skipped = []

    def submit(index, variant, attempt=1):
        while variant < 2 and (index >= len(urls[variant]) or not urls[variant][index]):
            variant += 1
        if variant == 2:
            skipped.append(index)
            return
        name = ("aug", "base")[variant]
        path = task / f"layers_{name}_{index:02d}.png"
        log(f"{task.name} 下载 {path.name}，第 {attempt}/3 次")
        future = pool.submit(copy_context().run, fetch_png, path, urls[variant][index],
                             index, size, get, deadline, sleep)
        pending[future] = index, variant, attempt, path

    for index in range(total):
        submit(index, 0)
    while pending or delayed:
        now = time.monotonic()
        for entry in delayed.copy():
            when, index, variant, attempt = entry
            if when <= now:
                delayed.remove(entry)
                submit(index, variant, attempt)
        timeout = max(0, min(item[0] for item in delayed) - now) if delayed else None
        if not pending:
            if timeout:
                time.sleep(timeout)
            continue
        done, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
        for future in done:
            index, variant, attempt, path = pending.pop(future)
            try:
                future.result()
            except (OSError, ValueError, IncompleteRead, SyntaxError) as exc:
                code = getattr(exc, "code", None)
                retry = not isinstance(exc, PermissionError) and (
                    not isinstance(exc, HTTPError) or code in {408, 429, 500, 502, 503, 504})
                when = time.monotonic() + attempt
                if retry and attempt < 3 and when < deadline:
                    delayed.append((when, index, variant, attempt + 1))
                    continue
                errors.append({"file": path.name, "error": type(exc).__name__,
                               "http_status": code, "attempts": attempt})
                submit(index, variant + 1)
    return {"errors": errors, "skipped_indices": sorted(skipped), "complete": not skipped}
