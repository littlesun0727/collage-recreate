"""Regression checks for streamed assignments and shared cutout capacity."""
import io
import json
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
from unittest.mock import patch
from urllib.error import HTTPError

from PIL import Image

from collage import cutout_api, pipeline


def test_batch_capacity_and_asset_deduplication():
    lock = Lock()
    full, release = Event(), Event()
    active = peak = calls = 0

    def process(run, asset, originals):
        nonlocal active, peak, calls
        with lock:
            active += 1
            calls += 1
            peak = max(peak, active)
            if active == 5:
                full.set()
        try:
            assert release.wait(5)
        finally:
            with lock:
                active -= 1
        return asset["id"]

    assets = [{"id": str(i)} for i in range(7)] * 2
    with patch.object(pipeline, "build_mask", process), ThreadPoolExecutor(2) as pool:
        cases = [pool.submit(pipeline.batch_masks, None, assets, None) for _ in range(2)]
        try:
            assert full.wait(5)
        finally:
            release.set()
        assert all(len(case.result()) == 7 for case in cases)
    assert (peak, calls) == (5, 14)


def test_cutout_overlaps_binding_and_reveal():
    cutting, revealing = Event(), Event()
    catalog = Future()
    catalog.set_result([{"id": "m"}])

    def match(layout, materials, on_cutout):
        on_cutout(materials[0])
        assert cutting.wait(5) and revealing.wait(5)
        return []

    def reveal(*args):
        revealing.set()
        assert cutting.wait(5)
        return {}

    def cut(*args):
        cutting.set()
        return Path("mask.png")

    with patch("collage.light_binding.match", match), patch.object(pipeline, "build_mask", cut), \
         patch.object(pipeline, "extract_and_save", reveal), patch.object(pipeline, "deliver") as delivery:
        with TemporaryDirectory() as temp:
            pipeline.assemble(Path(temp), Path(temp), {"objects":[{"id":"bg"}], "layer_order":["bg"]}, catalog)
    assert delivery.call_args.args[-1] == {"m": Path("mask.png")}


def test_download_retry_keeps_generated_result(monkeypatch):
    source = Image.new("RGBA", (50, 30), "red")
    source.putpixel((0, 0), (0, 0, 0, 0))
    png = io.BytesIO()
    source.save(png, "PNG")
    calls = []

    def open_request(req, timeout):
        calls.append(req)
        if not isinstance(req, str):
            assert json.loads(req.data)["background"] == "transparent"
            return io.BytesIO(b'{"data":[{"url":"https://example.com/result"}]}')
        if len(calls) == 2:
            raise HTTPError(req, 503, "unavailable", {}, None)
        return io.BytesIO(png.getvalue())

    monkeypatch.setenv("COLLAGE_CUTOUT_KEY", "test")
    monkeypatch.setenv("COLLAGE_CUTOUT_URL", cutout_api.DEFAULT_URL)
    monkeypatch.setattr(cutout_api, "urlopen", open_request)
    monkeypatch.setattr("collage.remote_retry.time.sleep", lambda seconds: None)
    assert cutout_api.mask(source).getextrema() == (0, 255)
    assert len(calls) == 3 and calls[1] == calls[2]
