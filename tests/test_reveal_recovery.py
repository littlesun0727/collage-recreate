import io
import tempfile
import time
from collections import Counter
from threading import Event, Lock
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from collage.layout.analysis import normalize
from collage.reveal import layer_image
from collage.reveal_transport import fetch_batch
from collage.reveal_download import download_layers
from collage.storage import read, save


class RecoveryTest(unittest.TestCase):
    def test_refill_slots_retry_and_skip_only_failed_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            png = io.BytesIO()
            Image.new("RGBA", (10, 10), "red").save(png, "PNG")
            response = Mock(content=png.getvalue())
            calls = Counter()
            lock = Lock()
            release = Event()
            from concurrent.futures import ThreadPoolExecutor

            def get(url, timeout):
                with lock:
                    calls[url] += 1
                if url == "a0":
                    self.assertTrue(release.wait(5))
                if url == "a2":
                    release.set()  # Third file starts while the first is still waiting.
                if url in {"a3", "b3"} or (url == "a1" and calls[url] == 1):
                    raise TimeoutError("offline")
                return response

            output = {"layers_aug": ["a0", "a1", "a2", "a3"], "layers_base": ["b0","b1","b2","b3"]}
            with ThreadPoolExecutor(2) as pool, patch("collage.reveal_download.download_pool", return_value=pool):
                report = download_layers(task, output, (10, 10), get, time.monotonic()+30, time.sleep)
            self.assertEqual(calls, {"a0":1, "a1":2, "a2":1, "a3":3, "b3":3})
            self.assertEqual(report["skipped_indices"], [3])
            self.assertFalse(report["complete"])

    def test_numeric_stroke_and_hex_color(self):
        fixed = normalize({"background": {"type": "solid_color", "color": "FFFFFF"},
                           "objects": [{"id": "p", "kind": "photo", "stroke": 0.018}]})
        self.assertEqual(fixed["background"]["color"], "#FFFFFF")
        self.assertEqual(fixed["objects"][0]["stroke"], {"width": 0.018, "color": "#FFFFFF"})

    def test_locked_download_reuses_bytes_and_falls_back_to_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            ref = task / "ref.png"
            Image.new("RGBA", (12, 10), "red").save(ref)
            response = Mock(content=ref.read_bytes())
            get = Mock(return_value=response)
            save(task/"submit_response.json", {"response_status": 0, "task_id": "cached"})
            save(task/"query_response.json", {"status": "done", "output": {
                "layers_base": ["base"], "layers_aug": ["aug"]}})
            replace = Path.replace
            attempts = []

            def replace_locked(src, dst):
                if src.suffix == ".part":
                    attempts.append(src.stem)
                    if attempts.count(src.stem) == 1 or src.stem.startswith("layers_aug"):
                        raise PermissionError("simulated file lock")
                return replace(src, dst)

            with patch.object(Path, "replace", replace_locked):
                fetch_batch(task, ref, [], "unused", get=get, sleep=lambda _: None)
            self.assertEqual(get.call_count, 2)
            self.assertEqual(attempts.count("layers_base_00"), 2)
            self.assertEqual(attempts.count("layers_aug_00"), 4)
            self.assertEqual(read(task/"download-report.json")["errors"][0]["file"], "layers_aug_00.png")
            self.assertEqual(layer_image(task, 0, (12, 10), True).getpixel((0, 0)), (255, 0, 0, 255))
            with self.assertRaisesRegex(ValueError, "Missing usable Reveal layer"):
                layer_image(task, 1, (12, 10), True)
