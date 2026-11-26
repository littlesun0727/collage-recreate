"""Stage and HTTP timings, including complete response reads and failed attempts."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from itertools import count
import re
import sys
from threading import Event, Lock, Thread
from traceback import extract_tb
from time import perf_counter
from urllib.request import urlopen as open_url

from .storage import save

_active = ContextVar("timing_stage", default=None)
_console = Lock()


def error_info(exc):
    # Validation exceptions can include the entire model response. Keep the
    # diagnostic short and never include request URLs, headers or payloads.
    if hasattr(exc, "absolute_path"):
        detail = f"schema={exc.validator} path={list(exc.absolute_path)}"
    else:
        detail = str(exc).splitlines()[0][:350] if str(exc) else ""
        detail = re.sub(r"(?:https?://|data:|Bearer\s+)\S+", "[redacted]", detail, flags=re.I)
    frames = extract_tb(exc.__traceback__)
    where = f" ({frames[-1].name}:{frames[-1].lineno})" if frames else ""
    return f"{type(exc).__name__} {detail}{where}"


def log(message):
    active = _active.get()
    if active:
        timing, stage = active
        timing.emit(stage, message)


class Timings:
    def __init__(self, folder, case):
        self.path = folder / "timings.json"
        self.log_path = folder / "pipeline.log"
        self.case = case
        self.request_ids = count(1)
        self.started = perf_counter()
        self.data = {"stages": {}, "requests": []}
        self.lock = Lock()

    def emit(self, stage, message):
        stamp = datetime.now().isoformat(timespec="milliseconds")
        line = f"{stamp} [{self.case}] [{stage}] {message}"
        with _console:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line+"\n")
            print(line, file=sys.stderr, flush=True)

    @contextmanager
    def stage(self, name):
        start = perf_counter()
        token = _active.set((self, name))
        ok = False
        self.emit(name, "开始")
        try:
            yield
            ok = True
        except Exception as exc:
            self.emit(name, "失败 " + error_info(exc))
            raise
        finally:
            _active.reset(token)
            duration = round(perf_counter()-start, 3)
            self.emit(name, f"结束 {'成功' if ok else '未完成'}，耗时 {duration:.3f}s")
            with self.lock:
                self.data["stages"][name] = {"seconds": duration, "ok": ok}
                save(self.path, self.data, indent=2)

    def finish(self):
        with self.lock:
            self.data["e2e_seconds"] = round(perf_counter()-self.started, 3)
            save(self.path, self.data, indent=2)


@contextmanager
def urlopen(request, *args, **kwargs):
    start = perf_counter()
    ok = False
    active = _active.get()
    method = getattr(request, "get_method", lambda: "GET")()
    done = Event()
    if active:
        timing, stage = active
        number = next(timing.request_ids)
        prefix = f"HTTP #{number} {method}"
        timing.emit(stage, f"{prefix} 开始，超时设置 {kwargs.get('timeout', '默认')}s")

        def heartbeat():
            while not done.wait(30):
                timing.emit(stage, f"{prefix} 等待响应或读取响应中，已过 {perf_counter()-start:.0f}s")

        Thread(target=heartbeat, daemon=True).start()
    try:
        with open_url(request, *args, **kwargs) as response:
            yield response
        ok = True
    except Exception as exc:
        if active:
            log(f"{prefix} 失败 {error_info(exc)}")
        raise
    finally:
        done.set()
        if active:
            duration = round(perf_counter()-start, 3)
            timing.emit(stage, f"{prefix} 结束 {'成功' if ok else '未完成'}，耗时 {duration:.3f}s")
            with timing.lock:
                timing.data["requests"].append({"stage": stage, "ok": ok,
                    "id": number, "method": method, "seconds": duration})
                save(timing.path, timing.data, indent=2)
