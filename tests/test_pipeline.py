import base64
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch
from zipfile import ZipFile
from PIL import Image

import server
from collage import pipeline, reveal, vlm
from collage.storage import read, save


def picture(color):
    image = io.BytesIO()
    Image.new("RGB", (100, 100), color).save(image, "PNG")
    return base64.b64encode(image.getvalue()).decode()


class AppContract(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.request = {"reference": picture("red"), "assets": [{"asset_id": "手机/相册:12", "image": picture("blue")}]}
        self.wire = {"background": {"type": "reference_clean", "id": "bg", "description": "paper"},
                     "objects": [{"kind": "photo", "id": "p", "description": "portrait", "bbox": [0, 0, 60, 60]},
                                 {"kind": "overlay", "id": "star", "description": "star", "bbox": [40, 40, 80, 80]}],
                     "layer_order": ["p", "star"]}

    def test_http_handoff_parallel_and_zip(self):
        self.wire["objects"].append({"id": "strip", "kind": "overlay", "bbox": [4,8,32,18],
                                     "description": "thin paper strip"})
        self.wire["layer_order"].append("strip")
        gate = threading.Barrier(3)
        release = threading.Event()
        calls = []
        def model(stage, *args):
            calls.append(stage)
            if stage == "analyze":
                return self.wire
            gate.wait(5)
            if not release.wait(5):
                raise AssertionError("first HTTP response blocked by binding")
            return {"photos": [{"slot_id": "p", "asset_id": "material_000", "source_crop": [0,0,1,1],
                                "crop_center": [.5,.5], "mirror_x": False, "reason": "portrait"}]}
        def clean(run, bg, size):
            gate.wait(5)
            path = run/"clean.png"
            Image.new("RGB", tuple(size), "yellow").save(path)
            return path
        def remote(task, reference, boxes, key):
            gate.wait(5)
            # Return service mappings in a different order to the input boxes.
            self.assertEqual([box["id"] for box in boxes], ["strip", "star", "p"])
            indices = [2, 1]  # Out of order, and the strip was omitted.
            save(task/"query_response.json", {"output": {"boxes_mapping_index": indices}})
            for i in range(len(indices)+1):
                Image.new("RGBA",(100,100),(0,i,0,255)).save(task/f"layers_aug_{i:02d}.png")
        with patch.object(server, "ROOT", self.root), patch.object(vlm,"ask", side_effect=model), \
             patch.object(pipeline.background,"clean",side_effect=clean), \
             patch.object(reveal,"fetch_batch", side_effect=remote), patch.dict(os.environ, {"360_API_KEY":"offline"}):
            host = ThreadingHTTPServer(("127.0.0.1",0), server.Handler)
            worker = threading.Thread(target=host.serve_forever)
            worker.start()
            base = "http://127.0.0.1:" + str(host.server_port)
            try:
                req = Request(base+"/jobs", data=json.dumps(self.request).encode(), headers={"Content-Type":"application/json"})
                with urlopen(req, timeout=8) as res:
                    self.assertEqual(res.status, 202)
                    response = json.load(res)
                    self.assertIn("strip", response["analysis"]["layer_order"])
                with urlopen(base+response["analysis_url"]) as res:
                    self.assertEqual(json.load(res), response["analysis"])
                release.set()
                import time
                for _ in range(100):
                    with urlopen(base+response["status_url"]) as res:
                        status = json.load(res)
                    if status["status"] != "processing":
                        break
                    time.sleep(.02)
                self.assertEqual(status, {"status": "ready"})
                with urlopen(base+response["bundle_url"]) as res, ZipFile(io.BytesIO(res.read())) as bundle:
                    layers = json.loads(bundle.read("scene.json"))["layers"]
                    self.assertEqual([o["id"] for o in layers], ["bg","p","star"])
                    self.assertEqual(layers[1]["source"]["asset_id"], "手机/相册:12")
                    with Image.open(io.BytesIO(bundle.read(layers[0]["source"]["file"]))) as bg:
                        self.assertEqual(bg.getpixel((0,0)), (255,255,0,255))
                    members = {"scene.json", layers[0]["source"]["file"], layers[2]["source"]["file"]}
                    self.assertEqual(set(bundle.namelist()), members)
                    with Image.open(io.BytesIO(bundle.read(layers[2]["source"]["file"]))) as img:
                        self.assertEqual(img.getpixel((0,0)), (0,2,0,255))
                self.assertEqual(calls, ["analyze","bind"])
            finally:
                release.set()
                host.shutdown()
                host.server_close()
                worker.join()

    def test_delivery_tiles_use_visible_pixels_and_skip_empty_layers(self):
        run = self.root
        im = Image.new("RGBA", (12, 10))
        im.save(run/"empty.png")
        im.putpixel((3, 2), (110, 30, 20, 1))
        im.putpixel((8, 7), (100, 50, 20, 255))
        im.save(run/"ink.png")
        objs = [{"id": oid, "kind": "overlay", "bbox": [4, 4, 8, 8]} for oid in ("empty", "ink")]
        analysis = {"reference_size": [12, 10], "objects": objs, "layer_order": ["empty", "ink"]}
        pipeline.deliver(run, analysis, [], [], {o["id"]: run/(o["id"]+".png") for o in objs})
        with ZipFile(run/"delivery.zip") as archive:
            scene = json.loads(archive.read("scene.json"))
            self.assertEqual(scene["schema_version"], "harmony-collage-scene-v2")
            self.assertEqual(set(archive.namelist()), {"scene.json", "layers/ink.png"})
            layer, = scene["layers"]
            self.assertEqual(layer["bbox"], [4, 4, 8, 8])
            source = layer["source"]
            self.assertEqual((source["placement"], source["offset"], source["size"]), ("tile", [3, 2], [6, 6]))
            restored = Image.new("RGBA", im.size)
            with Image.open(io.BytesIO(archive.read(source["file"]))) as tile:
                restored.paste(tile, tuple(source["offset"]))
            self.assertEqual(restored.tobytes(), im.tobytes())

    def test_invalid_analysis_calls_once(self):
        with patch.object(vlm, "ask", return_value={}) as model:
            with self.assertRaises(Exception):
                pipeline.first(self.root/"job", self.request)
            self.assertEqual(model.call_count, 1)
            self.assertFalse((self.root/"job/analysis.json").exists())

    def test_no_slots_and_no_reveal_targets(self):
        job = self.root/"plain"
        wire = {"background": {"type":"solid_color","color":"#123456"}, "objects":[], "layer_order":[]}
        with patch.object(vlm,"ask",return_value=wire) as model:
            pipeline.first(job, {**self.request, "assets":[]})
            pipeline.finish(job)
            self.assertEqual(model.call_count, 1)
            self.assertEqual(read(job/"status.json"), {"status":"ready"})

    def test_branch_failure_preserves_analysis(self):
        job = self.root/"fail"
        with patch.object(vlm,"ask",return_value=self.wire):
            pipeline.first(job, self.request)
        with patch.object(pipeline.background,"clean"), patch.object(vlm,"bind",return_value=[]), patch.object(reveal,"extract",side_effect=ValueError("missing layer")):
            pipeline.finish(job)
        self.assertTrue((job/"analysis.json").exists())
        self.assertFalse((job/"delivery.zip").exists())
        self.assertEqual(read(job/"status.json")["status"], "failed")
