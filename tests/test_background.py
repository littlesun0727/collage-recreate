import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from PIL import Image
from collage import background


class CleanBackground(unittest.TestCase):
    @patch.dict(os.environ, {"XUNXING_API_KEY": "offline"})
    @patch("collage.remote_retry.time.sleep")
    @patch("collage.background.urlopen")
    def test_generation_and_download_retry_independently(self, request, sleep):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "prepared").mkdir()
            ref = run / "prepared/reference.png"
            Image.new("RGB", (2, 2), "red").save(ref)
            url = "https://example.invalid/result.png"
            response = json.dumps({"data": [{"url": url}]}).encode()
            request.side_effect = [HTTPError(background.ENDPOINT, 429, "busy", {}, None),
                                   io.BytesIO(response), TimeoutError(), io.BytesIO(ref.read_bytes())]
            path = background.clean(run, {"description": "green paper"}, (4, 4))
            calls = [call.args[0] for call in request.call_args_list]
            self.assertEqual(calls, [calls[0], calls[0], url, url])
            body = json.loads(calls[0].data)
            self.assertEqual(body["model"], "gpt-image-2")
            self.assertIn("green paper", body["prompt"])
            self.assertTrue(body["images"][0]["image_url"].startswith("data:image/png;base64,"))
            with Image.open(path) as result:
                self.assertEqual(result.size, (4, 4))
            self.assertEqual(sleep.call_count, 2)
