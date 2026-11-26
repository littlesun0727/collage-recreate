import io
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

from collage.remote_retry import with_retries
from collage.timings import Timings, urlopen


class RunLoggingTest(unittest.TestCase):
    def test_case_isolation_and_retry_diagnostics(self):
        def attempt():
            with urlopen(Request("https://example.invalid/?secret=private"), timeout=5) as response:
                return response.read()

        with tempfile.TemporaryDirectory() as temp, redirect_stderr(io.StringIO()):
            root = Path(temp)
            def work(name):
                folder = root/name
                folder.mkdir()
                timings = Timings(folder, name)
                with timings.stage("analysis"):
                    if name == "case-a":
                        with_retries(attempt)
            error = HTTPError("https://example.invalid/?secret=private", 502, "Bad Gateway", {}, None)
            with patch("collage.timings.open_url", side_effect=[error, io.BytesIO(b"ok")]), patch("collage.remote_retry.time.sleep"):
                with ThreadPoolExecutor(2) as pool:
                    list(pool.map(work, ["case-a", "case-b"]))
            first = (root/"case-a/pipeline.log").read_text(encoding="utf-8")
            second = (root/"case-b/pipeline.log").read_text(encoding="utf-8")
            for fragment in ("HTTP #1 GET 开始", "HTTP #1 GET 结束 未完成", "502", "2s 后重试", "HTTP #2 GET 结束 成功"):
                self.assertIn(fragment, first)
            self.assertNotIn("private", first)
            self.assertNotIn("case-b", first)
            self.assertNotIn("HTTP #", second)
