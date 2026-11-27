import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from collage.layout.analysis import normalize
from collage.vlm import analyze, parse_content, request_choice


class Tolerance(unittest.TestCase):
    def test_description(self):
        wire = {"background": {"type": "reference_clean", "id": "paper"},
                "objects": [{"kind": "photo", "id": "p", "description": "字"*401},
                            {"kind": "overlay", "id": "star"}]}
        value = normalize(wire)
        self.assertEqual(value["background"]["description"], "reference_clean paper")
        self.assertEqual(value["objects"][0]["description"], "字"*100)
        self.assertEqual(value["objects"][1]["description"], "overlay star")

    def test_punctuation(self):
        self.assertEqual(parse_content('{"objects":[{"id":"p"},"fade":{"bottom":0.1}},]}'),
                         {"objects":[{"id":"p","fade":{"bottom":0.1}}]})
        self.assertEqual(parse_content('{"a":"x" "b":"y"}'), {"a":"x","b":"y"})
        with self.assertRaises(ValueError):
            parse_content('{"objects":')

    @patch("collage.remote_retry.time.sleep")
    @patch("collage.vlm.urlopen")
    def test_gateway(self, post, sleep):
        def error(code):
            return HTTPError("https://example.invalid", code, "gateway", {}, None)
        post.side_effect = [error(502), error(503), io.BytesIO(b'{"choices":[{"ok":true}]}')]
        self.assertTrue(request_choice(object())["ok"])
        self.assertEqual([c.args for c in sleep.call_args_list], [(2,), (4,)])
        post.reset_mock()
        post.side_effect = [error(502) for _ in range(3)]
        with self.assertRaises(HTTPError):
            request_choice(object())
        self.assertEqual(post.call_count, 3)

    @patch("collage.vlm.request_choice")
    def test_model_coordinate_contract(self, transport):
        with TemporaryDirectory() as temp:
            run = Path(temp)
            (run/"prepared").mkdir()
            (run/"prepared/reference.png").write_bytes(b"fixture")
            for model, box, expected, unit in [
                ("gpt-6.2-sol", [20, 40, 600, 1090], [20, 40, 600, 1090], "pixel"),
                ("qwen3.8-flash", [0, 0, 500, 500], [0, 0, 450, 600], "normalized_1000"),
                ("qwen4-max", [100, 200, 800, 900], [90, 240, 720, 1080], "normalized_1000"),
            ]:
                wire = {"background": {"type":"transparent"}, "objects":[
                    {"kind":"photo","id":"p","bbox":box,"description":"portrait"}], "layer_order":["p"]}
                transport.return_value = {"finish_reason":"stop","message":{"content":json.dumps(wire)}}
                config = {"COLLAGE_VLM_MODEL":model, "COLLAGE_VLM_URL":"https://example.invalid",
                          "COLLAGE_VLM_KEY":"offline"}
                with patch.dict(os.environ, config):
                    analysis = analyze(run, [900,1200])
                self.assertEqual(analysis["objects"][1]["bbox"], expected)
                payload = json.loads(transport.call_args.args[0].data)
                if model == "gpt-6.2-sol":
                    self.assertEqual(payload["reasoning_effort"], "medium")
                parts = payload["messages"][0]["content"]
                self.assertIn(unit, parts[0]["text"])
                if unit == "pixel":
                    self.assertIn("宽 900 像素、高 1200 像素", parts[0]["text"])
                    self.assertEqual(parts[-1]["image_url"]["detail"], "original")
