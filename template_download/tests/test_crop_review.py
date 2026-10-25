import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from crop_review import apply_decision, pixel_box
from store import Store
from vlm_batch import validate_result
from pipeline import review_cycle, reconcile_crop


class CropTests(unittest.TestCase):
    def test_legacy_disagreement_gate_is_retired(self):
        initial={'action':'crop','bbox':[120,81,880,918]}
        refined={'action':'crop','bbox':[119,81,880,941]}
        self.assertEqual(reconcile_crop(initial,refined),refined)

    def test_program_uncertainty_and_crop_resolution_are_not_review_gates(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Store(folder)
            try:
                Image.new('RGB',(1000,1600),'blue').save(s.root/'source.png')
                s.ingest({'note_id':'d'*24,'image_list':['https://example.com/a']})
                s.db.execute("UPDATE images SET status='ready',path='source.png'")
                cfg={'min_short_edge':720,'min_pixels':800000,'max_aspect_ratio':3,'near_duplicate_distance':4}
                item={'id':'1','action':'crop','bbox':[200,200,800,800],'reason':'external_ui'}
                for legacy in ('local_boundary_unresolved','crop_predictions_disagree'):
                    apply_decision(s,dict(item,needs_review_reason=legacy),cfg)
                    row=s.db.execute('SELECT status,reason,width,height FROM processed_assets').fetchone()
                    self.assertEqual(tuple(row),('ready','post_crop_resolution',600,960))
                    self.assertEqual(s.db.execute('SELECT review FROM images').fetchone()[0],'accept')
            finally:
                s.db.close()

    def test_only_crop_candidates_get_small_high_resolution_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Store(folder)
            try:
                Image.new('RGB',(1000,1600),'blue').save(s.root/'source.png')
                s.ingest({'note_id':'f'*24,'image_list':['https://example.com/a']})
                s.db.execute("UPDATE images SET status='ready',path='source.png'")
                s.db.commit()
                calls=[]
                class Fake:
                    def triage(self,rows,model,effort):
                        calls.append(('triage',len(rows)))
                        return {'items':[{'id':str(r['id']),'label':'single','needs_crop':True} for r in rows]}
                    def locate(self,rows,model,effort):
                        calls.append(('locate',len(rows)))
                        return {'items':[{'id':str(r['id']),'status':'review','bbox':None,
                                         'reason':'uncertain_boundary'} for r in rows]}
                review_cycle(s,Fake(),'fixture')
                self.assertEqual(calls,[('triage',1),('locate',1)])
                self.assertEqual(s.db.execute('SELECT review FROM images').fetchone()[0],'unsure')
            finally:
                s.db.close()

    def test_invalid_model_boxes_rejected(self):
        for bbox in ([-1,0,1000,1000],[100,0,50,800],[0.1,0,900,900],[0,0,1001,900]):
            with self.assertRaises(ValueError):
                validate_result({'items':[{'id':'1','label':'single','action':'crop','bbox':bbox,'reason':'external_ui'}]},[{'id':'1'}])
        self.assertEqual(pixel_box([119,81,880,920],1080,1710),(128,138,951,1574))

    def test_crop_preserves_original_export_and_live_review(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Store(folder)
            try:
                original=s.root/'source.png'
                Image.new('RGB',(1000,1600),'blue').save(original)
                before=original.read_bytes()
                s.ingest({'note_id':'e'*24,'image_list':['https://example.com/a','https://example.com/b']})
                s.db.execute("UPDATE images SET status='ready',path='source.png'")
                cfg={'min_short_edge':500,'min_pixels':250000,'max_aspect_ratio':3,'near_duplicate_distance':4}
                decision={'id':'1','label':'single','action':'crop','bbox':[100,100,900,900],'reason':'external_ui'}
                apply_decision(s,decision,cfg)
                self.assertEqual(original.read_bytes(),before)
                row=s.db.execute('SELECT * FROM processed_assets WHERE image_id=1').fetchone()
                self.assertEqual((row['width'],row['height'],row['status']),(800,1280,'ready'))
                s.export(s.root/'accepted.jsonl')
                record=json.loads((s.root/'accepted.jsonl').read_text())
                self.assertIn('processed',record['path'])
                self.assertEqual(record['original']['path'],str(original.resolve()))
                apply_decision(s,dict(decision,id='2'),cfg)
                self.assertEqual(s.db.execute('SELECT status FROM processed_assets WHERE image_id=2').fetchone()[0],'duplicate')
                s.db.execute("UPDATE images SET media_kind='live' WHERE id=2")
                apply_decision(s,dict(decision,id='2'),cfg)
                self.assertEqual(s.db.execute('SELECT reason FROM processed_assets WHERE image_id=2').fetchone()[0],'live_video_geometry_unverified')
            finally:
                s.db.close()


if __name__=='__main__':
    unittest.main()
