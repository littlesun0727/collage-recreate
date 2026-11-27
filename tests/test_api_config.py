import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collage import api_config, vlm


class ApiConfigTest(unittest.TestCase):
    def test_separate_analysis_and_binding_requests(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, clear=True):
            root = Path(tmp)
            config = root/"api.json"
            config.write_text(json.dumps({s: {"url":f"https://{s}.invalid/chat", "model":s,
                "api_key":s+"-key", "reasoning_effort":e} for s,e in
                [("binding","low"),("analysis","high")]}),encoding="utf-8")
            with patch.object(api_config,"ROOT",root):
                api_config.configure(config)
            with patch.object(vlm,"request_choice",return_value={"message":{"content":"{}"}}) as send:
                for stage, section, effort in [("analyze","analysis","high"),("classify","binding","low"),("bind","binding","low")]:
                    vlm.ask(stage,{"coordinate_space":"normalized_1000"},[],{})
                    req, = send.call_args.args
                    self.assertEqual(req.full_url, f"https://{section}.invalid/chat")
                    self.assertEqual(req.get_header("Authorization"),"Bearer "+section+"-key")
                    payload = json.loads(req.data)
                    self.assertEqual((payload["model"],payload["reasoning_effort"]),(section,effort))

    def test_custom_endpoint_models_and_key_precedence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, clear=True):
            root = Path(tmp)
            config = root/"custom.json"
            (root/".env").write_text("YIBU_API_KEY=fallback\n360_API_KEY=existing\n", encoding="utf-8")
            config.write_text(json.dumps({
                "vlm": {"url":"https://yibuapi.com/v1/chat/completions", "api_key":"test",
                        "analysis_model":"model-a", "binding_model":"model-b"},
                "reference_clean":{"url":"https://example.invalid/edit","model":"edit","api_key":"clean"},
                "reveal":{"base_url":"https://example.invalid/v1","model":"layers","api_key":""}
            }), encoding="utf-8")
            with patch.object(api_config, "ROOT", root):
                api_config.configure(config)
            with patch.object(vlm, "request_choice", return_value={"message":{"content":"{}"}}) as send:
                for stage, model in [("analyze","model-a"), ("bind","model-b")]:
                    vlm.ask(stage, {"coordinate_space":"normalized_1000"}, [], {})
                    request, = send.call_args.args
                    self.assertEqual(request.full_url, "https://yibuapi.com/v1/chat/completions")
                    self.assertEqual(json.loads(request.data)["model"], model)
                    self.assertEqual(request.get_header("Authorization"), "Bearer test")
            self.assertEqual(os.environ["360_API_KEY"], "existing")
            self.assertEqual(os.environ["COLLAGE_REVEAL_MODEL"], "layers")
            self.assertEqual(os.environ["COLLAGE_CLEAN_MODEL"], "edit")
