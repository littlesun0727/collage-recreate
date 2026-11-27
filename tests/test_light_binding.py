import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from collage import light_binding, vlm


class LightBindingTest(unittest.TestCase):
    def test_use_distinct_photos_before_repeating_for_cutouts(self):
        slot = {"kind":"photo","mode":"cutout","bbox":[0,0,100,100],"subject_type":"food"}
        catalog = [{"id":name,"size":[100,100],"classification":{
            "category":category,"cutout_ok":ok,"focus_point":[.5,.5]}}
            for name,category,ok in [("meal","food",True),("portrait","person",True),("plate","food",False)]]
        for count in (2,3,4):
            objects = [dict(slot,id=f"p{i}") for i in range(count)]
            rows = light_binding.match({"reference_size":[100,100],"objects":objects},catalog)
            self.assertEqual([r["asset_id"] for r in rows], ["meal","portrait","plate","meal"][:count])

    def test_pages_share_one_request_with_exact_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/"prepared").mkdir()
            Image.new("RGB",(80,20),"red").save(root/"prepared/p.jpg")
            assets = [{"id": f"materials_{i:03}", "binding_preview":"p.jpg"} for i in range(1,11)]
            rows = [{"id":a["id"],"category":"scenery","focus_point":[.66,.58],"cutout_ok":False} for a in assets]
            with patch.object(vlm,"ask",return_value={"materials":rows[::-1]}) as ask:
                classified = light_binding.classify(root,assets)
            ask.assert_called_once()
            stage, _, sheets, _ = ask.call_args.args
            self.assertEqual(stage,"classify")
            self.assertEqual(len(sheets),2)
            self.assertEqual(classified[0]["classification"]["id"], assets[0]["id"])
            with Image.open(sheets[0][1]) as page:
                self.assertEqual(page.size,(960,1050))

    def test_slot_categories_survive_analysis(self):
        wire = {"background":{"type":"photographic","description":"lake","id":"bg","subject_type":"scenery"},
                "objects":[{"kind":"photo","id":"p","description":"portrait","bbox":[20,20,80,80],"subject_type":"person"}],
                "layer_order":["p"]}
        with patch.object(vlm,"ask",return_value=wire):
            result = vlm.analyze(Path("."),[100,100])
        self.assertEqual([o["subject_type"] for o in result["objects"]],["scenery","person"])

    def test_popout_priority_and_focus(self):
        base = {"kind":"photo","mode":"window","bbox":[0,0,100,100],"subject_type":"person"}
        analysis = {"reference_size":[100,100],"objects":[dict(base,id="normal"),dict(base,id="base"),
                    dict(base,id="front",mode="cutout",linked_to="base")]}
        def asset(i, ok):
            return {"id":i,"size":[200,100],"classification":{"category":"person","cutout_ok":ok,"focus_point":[.7,.3]}}
        catalog = [asset("m1", True),asset("m2", False)]
        rows = light_binding.match(analysis,catalog)
        self.assertEqual([r["asset_id"] for r in rows],["m2","m1","m1"])
        self.assertEqual(rows[0]["crop_center"],[.9,.5])
        catalog[0]["classification"]["cutout_ok"] = False
        with self.assertRaisesRegex(ValueError, "没有可抠主体"):
            light_binding.match(analysis,catalog)
