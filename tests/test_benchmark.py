import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import benchmark
from collage.storage import save


class BenchmarkTest(unittest.TestCase):
    def test_original_rotation_transparency_and_preview_dimensions(self):
        import pipeline
        from collage.preview import photo
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder/"prepared").mkdir()
            original = folder/"portrait.png"
            im = Image.new("RGBA", (2048,512))
            im.paste((255,0,0,255), (0,0,1024,512))
            exif = Image.Exif()
            exif[274] = 6
            im.save(original, exif=exif)
            originals = {"materials_001": original}
            row, = pipeline.write_thumbnails(folder, "ref.png", originals)
            self.assertEqual(row["size"], [512,2048])
            with Image.open(folder/"prepared"/row["binding_preview"]) as thumb:
                self.assertEqual(thumb.size, (256,1024))
                self.assertEqual(thumb.getpixel((128,768)), (255,255,255))
            layer = {"id":"p","kind":"photo","mode":"bare","bbox":[0,0,512,2048],
                     "source":{"asset_id":"materials_001","source_crop":[0,0,1,1],
                               "mirror_x":False,"crop_center":[.5,.5]}}
            tile, (x,y) = photo(folder, layer, {"p":layer}, originals)
            self.assertEqual(tile.getpixel((256-x,512-y)), (255,0,0,255))
            self.assertEqual(tile.getpixel((256-x,1536-y))[3], 0)

    def test_offline_report_and_rotated_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            inputs = path/"cases/case-001/inputs"
            photos = inputs/"photos"
            photos.mkdir(parents=True)
            Image.new("RGB", (100,100), "white").save(photos/"r.png")
            Image.new("RGB", (100,100), "red").save(photos/"p.png")
            save(inputs/"catalog.json", {"reference":"r.png","materials":{"materials_001":"p.png"}})
            bg = dict(id="bg", kind="background", bbox=[0,0,100,100], background_type="solid_color", style={"fill":"white"})
            obj = dict(id="slot", kind="photo", mode="window", shape="heart", rotation=28, bbox=[15,15,85,85])
            binding = dict(slot_id="slot", asset_id="materials_001", source_crop=[0,0,1,1], crop_center=[.5,.5], mirror_x=False)
            out = path/"out"
            stale = out/"obsolete-case/artifacts"
            stale.mkdir(parents=True)
            (stale/"old.log").write_text("old")
            (out/"obsolete.html").touch()
            argv = ["benchmark.py","--case-dir",str(path/"cases"),"--output-dir",str(out)]
            with patch.object(sys,"argv",argv), patch.dict("os.environ"), patch("pipeline.vlm.analyze",return_value={
                    "objects":[bg,obj],"reference_size":[100,100],"layer_order":["bg","slot"]}), patch("pipeline.vlm.ask",return_value={"materials":[{"id":"materials_001","cutout_ok":True,"category":"other","focus_point":[.5,.5]}]}):
                self.assertEqual(benchmark.main(),0)
            self.assertFalse((out/"obsolete-case").exists())
            self.assertFalse((out/"obsolete.html").exists())
            batch_log = (out/"benchmark.log").read_text(encoding="utf-8")
            for entry in ("批测启动", "[case-001] [prepare]", "case-001 交付完成", "批测结束"):
                self.assertIn(entry, batch_log)
            self.assertTrue((photos/"p.png").exists())
            artifacts = out/"case-001/artifacts"
            with Image.open(artifacts/"final.png") as final:
                self.assertEqual(final.getpixel((50,50))[:3], (255,0,0))
                self.assertNotEqual(final.getpixel((15,15))[:3], (255,0,0))
            self.assertTrue((artifacts/"analysis-bbox.png").is_file())
            self.assertIn("case-001/artifacts/final.png",(out/"index.html").read_text(encoding="utf-8"))
            timings = json.loads((artifacts/"timings.json").read_bytes())
            self.assertGreaterEqual(timings["e2e_seconds"],timings["stages"]["analysis"]["seconds"])
