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
    def test_batch_order_preserves_unobserved_occlusions(self):
        from collage.reveal_order import scene_order
        ids = ["bg", "a", "text", "b", "side"]
        layers = [{"id": oid, "bbox": [0, 0, 10, 10]} for oid in ids]
        layers[-1]["bbox"] = [10, 0, 20, 10]  # Touches, without overlap.
        self.assertEqual(scene_order(layers, [["side", "b", "a"]]), ids)
        layers[2]["bbox"] = [20, 0, 30, 10]
        self.assertEqual(scene_order(layers, [["b"], ["a"]]), ids)
        reordered = scene_order(layers, [["side", "b", "a"]])
        self.assertLess(reordered.index("b"), reordered.index("a"))
        self.assertLess(reordered.index("b"), reordered.index("side"))

    def test_popout_single_slot_above_frame_and_beyond_window(self):
        from collage import preview, binding
        wire = {"background":{"type":"solid_color","color":"#ffffff"},
                "objects":[{"id":"p","kind":"photo","description":"portrait","subject_type":"person",
                            "bbox":[10,40,90,80],"popout":[35,25,65,75]},
                           {"id":"rim","kind":"frame","description":"border","photo_id":["p"],
                            "bbox":[8,38,92,82]}],"layer_order":["p","rim"]}
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            with patch.object(vlm,"ask",return_value=wire):
                analysis = vlm.analyze(run,[100,100])
            obj, = [o for o in analysis["objects"] if o["kind"]=="photo"]
            self.assertEqual((obj["mode"],obj["popout_above"]),("popout","rim"))
            catalog = [dict(id="m",asset_id="m",size=[80,80])]
            slot, = binding.context_for(dict(reference_size=[100,100],catalog=catalog),analysis)["photo_slots"]
            self.assertTrue(slot["requires_segmentable_subject"])
            path = run/"m.png"
            src = Image.new("RGB",(80,80),"green")
            src.paste("red",(30,0,50,80))
            src.save(path)
            mask = Image.new("L",src.size)
            mask.paste(255,(30,0,50,80))
            with patch.object(pipeline.cutout,"alpha_mask",return_value=mask):
                rows = [dict(slot_id="p",asset_id="m",source_crop=[0,0,1,1],crop_center=[.5,.5],mirror_x=False)]
                masks = pipeline.build_masks(run,analysis,catalog,rows,{"m":path})
            rim = Image.new("RGBA",(100,100))
            rim.paste("blue",(8,38,92,43))
            rim.save(run/"rim.png")
            bg_id = analysis["layer_order"][0]
            Image.new("RGBA",(100,100),"white").save(run/"bg.png")
            pipeline.deliver(run,analysis,catalog,rows,{"rim":run/"rim.png",bg_id:run/"bg.png"},masks=masks)
            preview.final(run,run,{"m":path})
            with Image.open(run/"final.png") as final:
                self.assertEqual(final.getpixel((50,32))[:3], (255,0,0))
                self.assertEqual(final.getpixel((50,40))[:3], (255,0,0))
                self.assertEqual(final.getpixel((20,40))[:3], (0,0,255))

    def test_cutouts_share_packaged_mask_with_preview(self):
        from collage import preview
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            original = Image.new("RGBA", (40, 30))
            original.paste((100, 50, 10, 180), (6, 3, 23, 25))
            path = run/"original.png"
            original.save(path)
            objects = [dict(id=name, kind="photo", mode=mode, bbox=[0, 0, 40, 30])
                       for name, mode in (("base","window"), ("front","cutout"), ("other","cutout"))]
            objects[1]["linked_to"] = "base"
            analysis = dict(objects=objects, reference_size=[40,30], layer_order=[o["id"] for o in objects])
            catalog = [dict(id="m1", asset_id="local-picture")]
            bindings = [dict(slot_id=o["id"], asset_id="m1", source_crop=[0,0,1,1],
                             mirror_x=False, crop_center=[.5,.5]) for o in objects]
            originals = {"local-picture":path}
            masks = pipeline.build_masks(run, analysis, catalog, bindings, originals)
            pipeline.deliver(run, analysis, catalog, bindings, {}, masks=masks)
            sources = [o["source"] for o in read(run/"scene.json")["layers"]]
            self.assertNotIn("mask_file", sources[0])
            self.assertEqual(sources[1]["mask_file"], sources[2]["mask_file"])
            with ZipFile(run/"delivery.zip") as archive:
                self.assertEqual(archive.namelist().count("masks/m1.png"), 1)
                with Image.open(io.BytesIO(archive.read("masks/m1.png"))) as alpha:
                    self.assertEqual(alpha.tobytes(), original.getchannel("A").tobytes())
            with patch.object(preview, "subject", side_effect=AssertionError("unexpected inference")):
                preview.final(run, run, originals)
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
        self.wire["layer_order"].insert(0, "strip")
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
            self.assertEqual([box["id"] for box in boxes], ["strip", "p", "star"])
            indices = [2, 1]  # Overlapping star below p in the reply, strip omitted.
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
                    self.assertEqual([o["id"] for o in layers], ["bg","star","p"])
                    self.assertEqual(layers[2]["source"]["asset_id"], "手机/相册:12")
                    with Image.open(io.BytesIO(bundle.read(layers[0]["source"]["file"]))) as bg:
                        self.assertEqual(bg.getpixel((0,0)), (255,255,0,255))
                    members = {"scene.json", layers[0]["source"]["file"], layers[1]["source"]["file"]}
                    self.assertEqual(set(bundle.namelist()), members)
                    with Image.open(io.BytesIO(bundle.read(layers[1]["source"]["file"]))) as img:
                        self.assertEqual(img.getpixel((0,0)), (0,1,0,255))
                self.assertEqual(calls, ["analyze","bind"])
            finally:
                release.set()
                host.shutdown()
                host.server_close()
                worker.join()

    def test_reveal_photo_context_depends_on_order_shape_and_frame_links(self):
        def obj(oid, kind, bbox, **fields):
            return dict(id=oid, kind=kind, bbox=bbox, **fields)
        bg = obj("bg", "background", [0,0,100,100])
        photo = obj("p", "photo", [0,0,60,60], mode="window", shape="ellipse")
        ink = obj("ink", "overlay", [25,25,40,40])
        def ids(*layers):
            return [o["id"] for o in reveal.reveal_targets([bg, *layers])]
        self.assertEqual(ids(photo, ink), ["ink"])
        self.assertEqual(ids(ink, photo), ["ink", "p"])
        self.assertEqual(ids({**ink, "bbox":[0,0,4,4]}, photo), ["ink"])
        self.assertEqual(ids(photo, obj("f","frame",[70,70,90,90],photo_id=["p"])), ["p","f"])
        self.assertEqual(ids(photo, obj("t","text",[0,0,60,60],text="Hi")), [])
        self.assertEqual(ids(ink, {**photo,"linked_to":"base"}), ["ink"])

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

