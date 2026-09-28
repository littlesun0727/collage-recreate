"""Offline regressions: no credentials, HTTP calls, or existing run mutations."""
import shutil
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'binding'), str(ROOT / 'build-v2')]
from binding_contract import digest, read_json
from matching_step import save
from text_contract import candidates, effective_draft, validate_records
from text_completion import complete_texts
from build_v2_core.inputs import load_inputs, object_fingerprint
from build_v2_core.unit_planner import unresolved, planning_data, validate_decisions
from build_v2_core.content_scope import allowed_text
from PIL import Image


def row(key, text='好时光', status='generated'):
    return {'key': key, 'text': text, 'status': status, 'reason': '新创作的装饰短句'}


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='collage-copy-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.draft = {
            'background': {'mode': 'fixed', 'kind': 'solid', 'background_brief': 'white'},
            'slots': [{'id': 'photo', 'label': 'photo', 'mode': 'photo', 'source_bbox_1000': [50,50,600,600]}],
            'texts': [
                {'id': 'known', 'label': 'title', 'default_text': 'Keep me!', 'source_bbox_1000': [10,10,500,50]},
                {'id': 'caption', 'label': 'decorative caption', 'default_text': None, 'source_bbox_1000': [100,650,600,700]}],
            'overlays': [{'id': 'label', 'label': 'label', 'action': 'reference_generate',
                          'generation_brief': 'handwritten paper label', 'requires_exact_content': True,
                          'text_content': None, 'source_bbox_1000': [100,750,600,850]}],
            'layer_order': [{'type': 'background'}, {'type': 'slot', 'id': 'photo'},
                            {'type': 'text', 'id': 'known'}, {'type': 'text', 'id': 'caption'},
                            {'type': 'overlay', 'id': 'label'}],
            'questions': ['Unclear decorative copy']}
        self.draft_path = self.root / 'draft.json'
        save(self.draft_path, self.draft)
        self.reference = self.root / 'reference.png'
        Image.new('RGB', (300,300), 'white').save(self.reference)
        assets = []
        for name, color in [('selected','blue'), ('unused','red')]:
            path = self.root / (name + '.png')
            Image.new('RGB', (100,100), color).save(path)
            assets.append({'asset_id': name, 'path': str(path), 'sha256': digest(path), 'size': [100,100]})
        self.document = {'schema_version': 'bindings-v1', 'status': 'completed',
            'input': {'draft_path': str(self.draft_path), 'draft_sha256': digest(self.draft_path),
                      'draft_contract_version': 'v2-2', 'reference_path': str(self.reference),
                      'reference_sha256': digest(self.reference), 'reference_size': [300,300]},
            'assets': assets, 'bindings': [{'slot_id': 'photo', 'asset_id': 'selected', 'method': 'user', 'reason': 'fixed'}]}
        self.binding = self.root / 'bindings.json'
        save(self.binding, self.document)

    def fake_request(self, records, status='completed'):
        def invoke(cmd, **kwargs):
            self.assertEqual(cmd[cmd.index('--task')+1], 'text')
            folder = Path(cmd[cmd.index('--output')+1])
            save(folder / 'call.json', {'status': status})
            save(folder / 'raw-response.txt', {'text_bindings': records})
            return SimpleNamespace(returncode=0 if status == 'completed' else 2)
        return invoke

    def load(self):
        return load_inputs(self.draft_path, self.reference, self.binding)

    def test_candidates_include_embedded_text_but_not_known_text(self):
        self.assertEqual(set(candidates(self.draft)), {'text:caption','overlay:label'})

    def test_generated_copy_reaches_planner_and_exact_content(self):
        records = [row('text:caption'), row('overlay:label', 'Happy days')]
        before = self.draft_path.read_bytes()
        with patch('text_completion.subprocess.run', side_effect=self.fake_request(records)), patch('text_completion.shutil.which', return_value='node'):
            result = complete_texts(self.binding, 'unused-config.json')
        self.assertEqual(result['generated'], 2)
        inputs = self.load()
        entry = inputs['entries']['text:caption']
        self.assertEqual(allowed_text(entry), ['好时光'])
        self.assertFalse(unresolved(entry))
        self.assertEqual(allowed_text(inputs['entries']['overlay:label']), ['Happy days'])
        self.assertFalse(unresolved(inputs['entries']['overlay:label']))
        self.assertEqual(inputs['entries']['text:known']['draft']['default_text'], 'Keep me!')
        planned = {o['key']: o for o in planning_data(inputs, {})['objects']}
        self.assertEqual(planned['text:caption']['text_binding']['status'], 'generated')
        self.assertFalse(planned['text:caption']['content_or_binding_unresolved'])
        self.assertEqual(self.draft_path.read_bytes(), before)
        self.assertEqual(read_json(self.binding)['bindings'], self.document['bindings'])

    def test_only_selected_photos_are_sent_without_catalog(self):
        from matching_step import make_sheet
        with patch('text_completion.make_sheet', wraps=make_sheet) as sheet, patch('text_completion.subprocess.run', side_effect=self.fake_request([row('text:caption'),row('overlay:label')])), patch('text_completion.shutil.which', return_value='node'):
            complete_texts(self.binding, 'unused-config.json')
        self.assertEqual([x[0] for x in sheet.call_args.args[0]], ['selected'])

    def test_user_copy_skips_remote(self):
        with patch('text_completion.subprocess.run') as request:
            complete_texts(self.binding, None, overrides={'text:caption':'用户文字', 'overlay:label':'指定文字'})
        request.assert_not_called()
        self.assertEqual(self.load()['entries']['text:caption']['text_binding']['status'], 'user')

    def test_unresolved_fact_remains_blocked(self):
        records = [row('text:caption', None, 'unresolved'), row('overlay:label')]
        self.document['text_bindings'] = records
        save(self.binding, self.document)
        inputs = self.load()
        self.assertTrue(unresolved(inputs['entries']['text:caption']))
        self.assertFalse(unresolved(inputs['entries']['overlay:label']))

    def test_old_bindings_remain_compatible(self):
        self.assertTrue(unresolved(self.load()['entries']['text:caption']))

    def test_invalid_records_cannot_override_known_text(self):
        bad = [row('text:known'), row('text:caption'), row('overlay:label')]
        with self.assertRaises(ValueError):
            effective_draft(self.draft, bad)

    def test_missing_duplicate_empty_and_unknown_keys_rejected(self):
        expected = candidates(self.draft)
        for rows in ([], [row('text:caption'),row('text:caption')],
                     [row('text:caption',' '),row('overlay:label')],
                     [row('text:caption'),row('text:unknown')],
                     [row('text:caption','bad','unresolved'),row('overlay:label')]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_records(rows, expected)

    def test_failed_request_does_not_write_replacements(self):
        before = self.binding.read_bytes()
        with patch('text_completion.subprocess.run', side_effect=self.fake_request([], 'failed')), patch('text_completion.shutil.which', return_value='node'), self.assertRaises(ValueError):
            complete_texts(self.binding, 'unused-config.json')
        self.assertEqual(self.binding.read_bytes(), before)

    def test_fingerprints_change_with_final_copy(self):
        before = self.load()
        self.document['text_bindings'] = [row('text:caption'),row('overlay:label')]
        save(self.binding, self.document)
        after = self.load()
        self.assertNotEqual(object_fingerprint(before['entries']['text:caption'], before),
                            object_fingerprint(after['entries']['text:caption'], after))
        self.assertEqual(object_fingerprint(before['entries']['text:known'], before),
                         object_fingerprint(after['entries']['text:known'], after))

    def test_planner_accepts_resolved_copy_but_still_rejects_missing_copy(self):
        plan = {'schema_version': 'build-unit-decision-v2', 'units': [
            {'member_keys': ['text:caption'], 'method': 'text', 'brief': '排版确定后的短句'}]}
        before = load_inputs(self.draft_path, self.reference, self.binding, objects=['text:caption'])
        with self.assertRaises(ValueError):
            validate_decisions(plan, before)
        self.document['text_bindings'] = [row('text:caption'),row('overlay:label')]
        save(self.binding, self.document)
        after = load_inputs(self.draft_path, self.reference, self.binding, objects=['text:caption'])
        self.assertEqual(validate_decisions(plan, after)['units'][0]['method'], 'text')

    def test_model_cannot_claim_user_provided_copy(self):
        with patch('text_completion.subprocess.run', side_effect=self.fake_request([row('text:caption', status='user'),row('overlay:label')])), patch('text_completion.shutil.which', return_value='node'), self.assertRaises(ValueError):
            complete_texts(self.binding, 'unused-config.json')
        self.assertNotIn('text_bindings', read_json(self.binding))

    def test_changed_selected_photo_rejected(self):
        Image.new('RGB', (100,100), 'green').save(self.document['assets'][0]['path'])
        with patch('text_completion.subprocess.run') as request, self.assertRaises(ValueError):
            complete_texts(self.binding, 'unused-config.json')
        request.assert_not_called()

    def test_changed_source_rejected(self):
        self.draft['texts'][0]['default_text'] = 'changed'
        save(self.draft_path, self.draft)
        with self.assertRaises(ValueError):
            complete_texts(self.binding, None)

    def test_no_unknown_text_makes_no_request(self):
        self.draft['texts'][1]['default_text'] = 'known'
        self.draft['overlays'][0]['text_content'] = 'known'
        save(self.draft_path, self.draft)
        self.document['input']['draft_sha256'] = digest(self.draft_path)
        save(self.binding, self.document)
        with patch('text_completion.subprocess.run') as request:
            result = complete_texts(self.binding, None)
        request.assert_not_called()
        self.assertEqual(result['generated'], 0)

    def test_full_entrypoint_with_all_photos_explicitly_bound(self):
        import bind_materials
        materials = self.root / 'materials'
        materials.mkdir()
        photo = materials / 'selected.png'
        shutil.copyfile(self.document['assets'][0]['path'], photo)
        override_path = self.root / 'overrides.json'
        save(override_path, {'photo': str(photo)})
        args = SimpleNamespace(output=self.root / 'binding-run', materials=[materials], jobs=None,
            draft=self.draft_path, reference=self.reference, draft_version='auto', overrides=override_path,
            text_overrides=None, instructions=None, catalog=None, config=Path('unused-config.json'),
            openclaw_root=None, timeout_seconds=600, dry_run=False)
        with patch('text_completion.subprocess.run', side_effect=self.fake_request([row('text:caption'),row('overlay:label')])) as request, patch('text_completion.shutil.which', return_value='node'):
            result = bind_materials.run(args)
        self.assertEqual(result['status'], 'completed', result)
        self.assertIsNone(result['catalog'])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(result['jobs'][0]['text_completion']['generated'], 2)
        inputs = load_inputs(self.draft_path, self.reference, result['jobs'][0]['bindings'])
        self.assertFalse(unresolved(inputs['entries']['text:caption']))


if __name__ == '__main__':
    unittest.main()
