import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent.parent / 'build-v2'))
sys.path.insert(0, str(Path(__file__).parent))
import cutout
import workflow as renders_workflow
from compositor import pin, write_json, digest
from build_v2_core.inputs import load_inputs
from build_v2_core.unit_planner import validate_decisions
from build_v2_core.common import BuildError
from build_v2_core.workflow import export as build_export


class CutoutTests(unittest.TestCase):
    def test_outline_units_and_validation(self):
        self.assertEqual(cutout.outline_pixels('2%', (200, 500)), 4)
        self.assertEqual(cutout.outline_pixels('12px', (200, 500)), 12)
        self.assertEqual(cutout.outline_pixels(12, (200, 500)), 12)
        self.assertEqual(cutout.outline_pixels('0', (200, 500)), 0)
        for value in ('-2', 'NaN', '2em', True, '1e100'):
            with self.assertRaises(ValueError):
                cutout.outline_pixels(value, (200, 500))

    def test_outline_preserves_pixels_and_geometry(self):
        image = Image.new('RGBA', (100, 160))
        ImageDraw.Draw(image).rectangle((20, 30, 49, 109), fill=(80, 120, 160, 255))
        asset, meta = cutout.make_asset(image, '4px')
        ox, oy = meta['source_offset']
        restored = asset.crop((20-ox, 30-oy, 50-ox, 110-oy))
        self.assertEqual(restored.size, (30, 80))
        np.testing.assert_array_equal(np.asarray(restored), np.asarray(image.crop((20,30,50,110))))
        self.assertEqual(asset.getpixel((19-ox, 30-oy)), (255,255,255,255))
        self.assertEqual(cutout.outline_rgba(image, 2).size, image.size)
        np.testing.assert_array_equal(np.asarray(cutout.outline_rgba(image, 0)), np.asarray(image))

    def test_mask_cache_is_reused_for_outline_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, model = root/'photo.png', root/'birefnet.onnx'
            Image.new('RGB', (100, 150), '#345678').save(source)
            model.write_bytes(b'test model')
            alpha = Image.new('L', (100,150))
            ImageDraw.Draw(alpha).rectangle((20,10,79,139), fill=255)
            with patch.object(cutout, 'infer_alpha', return_value=(alpha, {'backend':'birefnet-onnx'})) as infer:
                first = cutout.extract(source, model, root/'first', '2%', cache=root/'cache')
                second = cutout.extract(source, model, root/'second', '12px', cache=root/'cache')
                self.assertEqual(infer.call_count, 1)
            self.assertFalse(first['segmentation_reused'])
            self.assertTrue(second['segmentation_reused'])
            self.assertEqual(first['foreground']['sha256'], second['foreground']['sha256'])
            self.assertNotEqual(first['asset']['sha256'], second['asset']['sha256'])
            with patch.object(cutout, 'infer_alpha', side_effect=AssertionError('must not infer')):
                third = cutout.extract(None, None, root/'third', '0', foreground_path=first['foreground']['file'])
            self.assertEqual(third['foreground']['sha256'], first['foreground']['sha256'])

    def test_build_passes_cutout_through_and_rejects_ownership(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference = root/'reference.png'
            Image.new('RGB', (100, 150), 'white').save(reference)
            draft = {'background': {'mode':'fixed','kind':'solid','background_brief':'white'},
                     'slots':[{'id':'person','label':'person','mode':'cutout','display_brief':'white outline',
                               'source_bbox_1000':[100,100,900,999]}],
                     'texts':[], 'overlays':[], 'questions':[],
                     'layer_order':[{'type':'background'},{'type':'slot','id':'person'}]}
            path = root/'draft.json'
            path.write_text(json.dumps(draft), encoding='utf-8')
            inputs = load_inputs(path, reference)
            self.assertNotIn('slot:person', inputs['entries'])
            self.assertNotIn('slot:person', inputs['selected'])
            self.assertEqual([e['key'] for e in inputs['passthrough']], ['slot:person'])
            bad_plan = {'schema_version':'build-unit-decision-v2','units':[
                {'unit_id':'person','member_keys':['slot:person'],'brief':'person','method':'cutout'}]}
            with self.assertRaises(BuildError):
                validate_decisions(bad_plan, inputs)

    def test_empty_production_scope_with_only_slots_can_continue(self):
        with tempfile.TemporaryDirectory() as temp:
            state = {'tasks':{}, 'selected':[], 'all_entries':{}, 'passthrough':[],
                     'revision':'test', 'planning':{}, 'environment':{}, 'events':[],
                     'plan':{'units':[]}}
            self.assertTrue(build_export(Path(temp), state)['all_build_resources_accepted'])
            state['all_entries'] = {'overlay:unselected':{}}
            self.assertFalse(build_export(Path(temp), state)['all_build_resources_accepted'])

    def test_new_and_legacy_builds_render_one_proportional_cutout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build = root/'build'
            build.mkdir()
            photo, model = root/'photo.png', root/'birefnet.onnx'
            Image.new('RGB', (100,150), '#345678').save(photo)
            model.write_bytes(b'test model')
            source = {'asset_id':'a', 'path':str(photo), 'sha256':digest(photo), 'size':[100,150]}
            slots = [{'id':'bg','label':'background','mode':'photo','source_bbox_1000':[0,0,999,999]},
                     {'id':'person','label':'person','mode':'cutout','display_brief':'white outline',
                      'source_bbox_1000':[200,0,800,999]}]
            draft = {'background':{'mode':'slot','kind':'photo','slot_id':'bg'}, 'slots':slots,
                     'overlays':[], 'texts':[], 'questions':[],
                     'layer_order':[{'type':'slot','id':'bg'},{'type':'slot','id':'person'}]}
            draft_path, bindings_path = root/'draft.json', root/'bindings.json'
            write_json(draft_path, draft)
            write_json(bindings_path, {'assets':[source], 'bindings':[{'slot_id':s['id'],'asset_id':'a'} for s in slots]})
            paths = {'draft':draft_path,'reference':photo,'bindings':bindings_path}
            info = {'canvas_size':[100,150]}
            for key,path in paths.items():
                info[key+'_path'],info[key+'_sha256'] = str(path),digest(path)
            state = {'revision':'test','input':info,'draft':draft,'tasks':{}}
            runtime = [{'id':s['id'],'key':'slot:'+s['id'],'draft':s,'source_asset':source,
                        'target_box':[0,0,100,150] if s['id']=='bg' else [20,0,80,150]} for s in slots]
            write_json(build/'state.json', state)
            alpha = Image.new('L',(100,150))
            ImageDraw.Draw(alpha).rectangle((30,10,69,139), fill=255)

            def local_cutout(original, output, weights, python=None, cache=None, outline_width='2%', outline_color='#FFFFFF'):
                return cutout.extract(original['file'], weights, output, outline_width, outline_color, root/'cache')

            for legacy in (False, True):
                catalog = {'schema_version':'build-unit-assets-v1','revision':'test','assets':[],
                           'passthrough':runtime if not legacy else runtime[:1]}
                if legacy:
                    catalog['assets'] = [{'key':'old-person','member_keys':['slot:person'],
                                          'method':'cutout','file':'unused-legacy.png'}]
                write_json(build/'assets.json', catalog)
                output = root/('legacy' if legacy else 'new')
                with patch.object(renders_workflow, 'customer_cutout', side_effect=local_cutout), \
                     patch.object(cutout, 'infer_alpha', return_value=(alpha,{'backend':'birefnet-onnx'})):
                    renders_workflow.prepare(build, output, [], model)
                result = renders_workflow.render(output/'layout.json')
                layout = json.loads((output/'layout.json').read_text())
                people = [l for l in layout['layers'] if l['kind']=='cutout']
                self.assertEqual(len(people), 1)
                self.assertEqual(people[0]['fit'], 'contain')
                self.assertTrue(result['coverage_complete'])
                self.assertTrue(result['source_assets_accepted'])
                before = digest(output/'layout.json')
                patch_path = root/'stretch.json'
                write_json(patch_path, {'reason':'invalid stretch','changes':[{'key':'slot:person','fit':'stretch'}]})
                with self.assertRaisesRegex(ValueError, 'preserve aspect ratio'):
                    renders_workflow.adjust(output/'layout.json', patch_path)
                self.assertEqual(digest(output/'layout.json'), before)


if __name__ == '__main__':
    unittest.main()
