import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image, ImageChops, ImageDraw

from collage import pipeline, reveal
from collage.lettering import word_count
from collage.storage import read, save


class LetteringDelivery(unittest.TestCase):
    def test_skip_unavailable_glyph_and_keep_remaining_text(self):
        from collage.text_render import render_text
        missing = chr(0x10ffff)
        image, info = render_text("A" + missing + "♥", (100, 40), {})
        expected, _ = render_text("A♥", (100, 40), {})
        self.assertEqual(info["skipped_glyphs"], [missing])
        self.assertEqual(image.tobytes(), expected.tobytes())
        image, info = render_text(missing, (100, 40), {})
        self.assertFalse(info["text_rendered"])
        self.assertIsNone(image.getbbox())

    def test_counts(self):
        self.assertEqual(word_count("一二三四五"), 5)
        self.assertEqual(word_count("one two three four five"), 5)
        self.assertEqual(word_count("Children’s Day"), 2)
        self.assertEqual(word_count("Привет, как у тебя дела?"), 0)

    def test_visible_short_and_threshold_long_text(self):
        for content, blocked, action in [
            ("one two three", False, "embedded"),
            ("one two three", True, "rendered"),
            ("été à la mer ici", False, "embedded"),
            ("été à la mer ici", True, "rendered"),
            ("one two three four five", False, "rendered"),
        ]:
            with self.subTest(content=content, blocked=blocked), tempfile.TemporaryDirectory() as tmp:
                run = Path(tmp)
                (run/"prepared").mkdir()
                ref = Image.new("RGB", (200, 45), "#bead88")
                ImageDraw.Draw(ref).text((8, 8), content, fill="black")
                ref.save(run/"prepared/reference.png")
                objs = [{"kind":"background","id":"bg","bbox":[0,0,200,45],
                         "background_type":"solid_color","style":{"fill":"#ffffff"}},
                        {"kind":"overlay","id":"paper","bbox":[0,0,200,45]}]
                if blocked:
                    objs.append({"kind":"overlay","id":"cover","bbox":[0,0,200,45]})
                objs.append({"kind":"text","id":"t","bbox":[5,6,145,24],
                             "text":content,"style":{"fill":"#000000"}})
                order = [o["id"] for o in objs]
                analysis = {"objects":objs,"layer_order":order,"reference_size":[200,45]}
                def remote(task, reference, boxes, key):
                    self.assertEqual([b["id"] for b in boxes], order[1:-1])
                    indices = list(range(len(boxes)))  # paper below cover in returned PNGs
                    save(task/"query_response.json", {"output":{"boxes_mapping_index":indices}})
                    for i, box in enumerate((boxes[pos] for pos in indices), 1):
                        im = ref.convert("RGBA") if box["id"] == "paper" else Image.new("RGBA", ref.size, "white")
                        im.save(task/f"layers_aug_{i:02d}.png")
                with patch.object(reveal, "fetch_batch", side_effect=remote), patch.dict(os.environ, {"360_API_KEY":"offline"}):
                    files = reveal.extract(run, analysis)
                pipeline.deliver(run, analysis, [], [], files)
                with ZipFile(run/"delivery.zip") as archive:
                    scene = json.loads(archive.read("scene.json"))
                    self.assertEqual(read(run/"lettering.json")["t"]["action"], action)
                    if action == "embedded":
                        self.assertNotIn("t", files)
                        self.assertEqual([layer["id"] for layer in scene["layers"]], order[:-1])
                        self.assertEqual(len(archive.namelist()), len(order))
                    else:
                        layer = scene["layers"][-1]
                        self.assertEqual(layer["id"], "t")
                        with archive.open(layer["source"]["file"]) as f, Image.open(f) as text:
                            self.assertEqual(list(text.size), layer["source"]["size"])
                            self.assertIsNotNone(text.getchannel("A").getbbox())
                with Image.open(files["paper"]) as carrier:
                    changed = ImageChops.difference(ref, carrier.convert("RGB")).getbbox()
                    self.assertEqual(bool(changed), action == "rendered")
                    if blocked:
                        self.assertIn("paper", read(run/"lettering.json")["t"]["inpainted_carriers"])
                    self.assertEqual(carrier.getchannel("A").getextrema(), (255,255))

    def test_blurred_strokes_still_fail_when_detection_loses_the_line(self):
        from PIL import ImageFilter
        from collage.text_detection import residual_strokes
        original = Image.new("RGB", (160, 30), "white")
        ImageDraw.Draw(original).text((4, 8), "letters on the paper", fill="black")
        region = [[2, 5, 120, 25]]
        self.assertEqual(residual_strokes(original, original.filter(ImageFilter.GaussianBlur(.65)), region), region)
        self.assertEqual(residual_strokes(original, Image.new("RGB", original.size, "white"), region), [])

    def test_failed_ocr_restores_pixels_and_omits_text_from_delivery(self):
        from collage.lettering import finish_text_layers
        content = "one two three four five"
        size = (200, 45)
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run/"prepared").mkdir()
            paper = Image.new("RGBA", size, "white")
            ImageDraw.Draw(paper).text((8, 8), content, fill="black")
            paper.convert("RGB").save(run/"prepared/reference.png")
            files = {"bg": run/"paper.png"}
            paper.save(files["bg"])
            objects = [
                {"id":"bg", "kind":"background", "background_type":"reference_clean", "bbox":[0,0,*size]},
                {"id":"t", "kind":"text", "text":content, "bbox":[5,6,145,24]}]
            analysis = {"reference_size":list(size), "layer_order":["bg","t"], "objects":objects}
            def composite(layers, files, size, hidden):
                self.assertEqual(hidden, {"t"})
                with Image.open(files["bg"]) as clean:
                    self.assertNotEqual(clean.tobytes(), paper.tobytes())
                    return clean.copy()
            with patch("collage.text_detection.detect", return_value=[[5, 6, 140, 20]]) as detect:
                decisions = finish_text_layers(run, objects, files, self.fail, size, composite)
            self.assertEqual(detect.call_count, 2)
            self.assertEqual(decisions['t']['ocr']['status'], 'residual')
            self.assertEqual(decisions['t']['restored_carriers'], ['bg'])
            save(run/"lettering.json", decisions)
            pipeline.deliver(run, analysis, [], [], files)
            with ZipFile(run/"delivery.zip") as archive:
                scene = json.loads(archive.read("scene.json"))
                self.assertEqual([obj["id"] for obj in scene["layers"]], ["bg"])
                with archive.open(scene["layers"][0]["source"]["file"]) as f, Image.open(f) as im:
                    self.assertEqual(im.tobytes(), paper.tobytes())

    def test_clean_background_and_text_without_remote_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run/"prepared").mkdir()
            image = Image.new("RGB",(200,45),"white")
            ImageDraw.Draw(image).text((8,8),"one two three four five",fill="black")
            image.save(run/"prepared/reference.png")
            analysis = {"reference_size":[200,45],"layer_order":["bg","t"],"objects":[
                {"id":"bg","kind":"background","background_type":"reference_clean","bbox":[0,0,200,45]},
                {"id":"t","kind":"text","text":"one two three four five","bbox":[5,6,145,24]}]}
            with patch.object(reveal.background,"clean",return_value=run/"prepared/reference.png"), patch.object(reveal,"fetch_batch") as remote:
                reveal.extract(run,analysis)
                remote.assert_not_called()
            self.assertEqual(read(run/"lettering.json")["t"]["inpainted_carriers"], ["bg"])
