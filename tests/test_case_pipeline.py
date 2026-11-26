import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
import json

from PIL import Image

import pipeline as entry
from collage.storage import read, save


class CasePipelineTest(unittest.TestCase):
    def test_publish_order_and_source_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp)/"case"
            photos = case/"inputs/photos"
            photos.mkdir(parents=True)
            for name in ("IMG_7412.jpg", "IMG_8231.png"):
                Image.new("RGB", (24, 32), "white").save(photos/name)
            ident = "materials_007"
            save(case/"inputs/catalog.json", {
                "reference": "IMG_7412.jpg", "materials": {ident: "IMG_8231.png"}})
            output = case/"outputs"
            analysis = {"reference_size": [24, 32], "layer_order": ["bg", "p"], "objects": [
                {"id": "bg", "kind": "background", "bbox": [0, 0, 24, 32],
                 "background_type": "solid_color", "style": {"fill": "white"}},
                {"id": "p", "kind": "photo", "bbox": [0, 0, 24, 32]}]}

            def bind(run, layout, catalog):
                self.assertEqual(read(output/"analysis.json"), layout)
                self.assertFalse((output/"delivery.zip").exists())
                self.assertTrue((run/"prepared"/(ident + ".jpg")).is_file())
                self.assertFalse((run/"prepared"/(ident + ".png")).exists())
                self.assertEqual(catalog[0]["filename"], "IMG_8231.png")
                return [{"slot_id": "p", "asset_id": ident, "source_crop": [0, 0, 1, 1],
                         "crop_center": [.5, .5], "mirror_x": False}]

            with patch.object(entry.vlm, "analyze", return_value=analysis), patch.object(entry.vlm, "bind", bind):
                entry.run_case(case)
            self.assertEqual({p.name for p in output.iterdir()}, {"analysis.json", "scene.json", "delivery.zip"})
            with ZipFile(output/"delivery.zip") as archive:
                scene = json.loads(archive.read("scene.json"))
                self.assertEqual(read(output/"scene.json"), scene)
                self.assertEqual(scene["layers"][1]["source"]["asset_id"], ident)
            self.assertEqual(read(case/"artifacts/bindings.json")[0]["slot_id"], "p")
            external = Path(temp)/"round-02"/"case"
            output = external/"outputs"
            with patch.object(entry.vlm, "analyze", return_value=analysis), patch.object(entry.vlm, "bind", bind):
                for _ in range(2):
                    self.assertEqual(entry.run_case(case, output_dir=external), output.resolve())
            self.assertEqual(read(output/"scene.json"), scene)
            self.assertEqual(read(external/"artifacts/status.json"), {"status": "ready"})
            self.assertTrue(list((external/"artifacts/history").glob("*/outputs/delivery.zip")))
            self.assertFalse((case/"artifacts/history").exists())
            output = case/"outputs"
            (photos/"IMG_8231.png").rename(photos/"renamed.png")
            with self.assertRaisesRegex(ValueError, "materials_007"):
                entry.run_case(case)
            self.assertEqual(read(case/"artifacts/status.json")["stage"], "prepare")
            self.assertFalse(list(output.iterdir()))
            self.assertTrue(list((case/"artifacts/history").glob("*/outputs/delivery.zip")))
