"""Offline regression tests: actual transport contract, rejection, resume and rendering.

Run: python -B -m unittest discover -s tests -v
No provider calls or remote extraction are made.
"""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from PIL import Image
from jsonschema import ValidationError

from collage.workflow.stages import bind as photo_binding
from collage import prompts
from collage.vlm.client import Client, ModelError
from collage.config import Config
from collage.vlm.coordinates import ReferenceCoordinates
from collage.workflow.pipeline import Workflow
from collage.storage import digest, file_sha, read, save
from photo_windows import defaults
from window_contract import empty_stroke


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root/'run'
        (self.run/'prepared').mkdir(parents=True)
        ref = self.run/'prepared/reference.png'
        Image.new('RGB', (400, 200), '#bbbbbb').save(ref)
        assets = []
        for i, color in enumerate(('#dd2222', '#2222dd')):
            path = self.root/f'asset_{i}.png'
            Image.new('RGB', (240, 180), color).save(path)
            assets.append({'id': f'asset_{i}', 'size': [240, 180], 'file': str(path),
                           'sha256': file_sha(path)})
        self.catalog = {'assets': assets, 'contact_sheets': [str(ref)]}
        save(self.run/'prepared/catalog.json', self.catalog)
        save(self.run/'input.json', {'reference_size': [400, 200], 'instructions': 'photo preference'})
        self.analysis = {'objects': [
            {'id': 'photo_a', 'kind': 'photo', 'description': 'front-facing subject',
             'bbox': [20, 20, 180, 180], 'window_bbox': [25, 25, 175, 175], 'mode': 'cover'},
            {'id': 'photo_b', 'kind': 'photo', 'description': 'landscape',
             'bbox': [200, 20, 380, 180], 'mode': 'cover'},
            {'id': 'note_yellow', 'kind': 'overlay', 'description': 'PRIVATE_FIXED_NOTE',
             'text_status': 'unreadable', 'text': 'PRIVATE_OCR'}]}
        self.base = {'reference_size': [400, 200], 'catalog': assets, 'instructions': 'photo preference',
                     'text_units': ['PRIVATE_TEXT_UNIT'], 'analysis': self.analysis}
        save(self.run/'structure.json', {**self.analysis, 'coordinate_mode': 'pixel'})
        self.windows = defaults(self.analysis)
        save(self.run/'photo-windows.json', self.windows)
        self.context = photo_binding.context_for(self.base, self.analysis, self.windows)
        self.answer = {'photos': [
            {'slot_id': p['slot_id'], 'asset_id': f'asset_{i}', 'source_crop': [0, 0, 1, 1],
             'crop_center': [.5, .5], 'mirror_x': False, 'reason': 'Visible subject; preserve full crop.'}
            for i, p in enumerate(self.context['photo_slots'])]}
        self.config = Config(coordinate_mode='pixel', response_format='json_object', http_retry_delay=0)
        self.calls = []

    def transport(self, payload):
        self.calls.append(deepcopy(payload))
        return {'id': 'offline-test', 'model': self.config.model,
                'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.answer)}}]}

    def ask(self, client=None, **options):
        client = client or Client(self.root/'vlm', self.config, transport=self.transport)
        value = client.ask('bind', prompts.render('bind'), self.context,
            [('reference', self.run/'prepared/reference.png')], photo_binding.schema_for(self.context, self.windows),
            lambda a: photo_binding.validate(a, self.context, self.analysis, self.run, self.windows),
            single_request=True, reference_input_only=True, **options)
        return value, client

    def test_context_is_allowlisted_and_originals_unchanged(self):
        before = deepcopy(self.base)
        windows = deepcopy(self.windows)
        for spec in windows['photos']:
            spec.update(reason='PRIVATE_WINDOW_REASON')
        before_windows = deepcopy(windows)
        context = photo_binding.context_for(self.base, self.analysis, windows)
        wire = json.dumps(context)
        for secret in ('PRIVATE_', 'note_yellow', 'text_units', 'text_status', '"file"', '"sha256"', '"analysis"'):
            self.assertNotIn(secret, wire)
        self.assertEqual(context['photo_slots'][0]['aspect_ratio'], 1)
        self.assertNotIn('photo_windows', context)
        self.assertNotIn('mode', context['photo_slots'][0])
        context['photo_slots'][0]['bbox'][0] = 0
        self.assertFalse({'mask', 'edge_gradient', 'subject', 'linked_foregrounds'} & set(context['photo_slots'][0]))
        self.assertEqual(self.base, before)
        self.assertEqual(windows, before_windows)

    def test_pixel_and_normalized_input_keep_photo_output_contract(self):
        for mode in ('pixel', 'normalized_1000'):
            with self.subTest(mode=mode):
                adapter = ReferenceCoordinates(self.context, mode, input_only=True)
                expected = [50, 100, 450, 900] if mode == 'normalized_1000' else [20, 20, 180, 180]
                self.assertEqual(adapter.context['photo_slots'][0]['bbox'], expected)
                self.assertNotIn('window_bbox', adapter.context['photo_slots'][0])
                if mode == 'pixel':
                    self.assertNotIn('width', adapter.context['photo_slots'][0])
                    self.assertEqual(adapter.context['reference_size'], [400, 200])
                else:
                    self.assertNotIn('width', adapter.context['photo_slots'][0])
                    self.assertNotIn('height', adapter.context['photo_slots'][0])
                    self.assertNotIn('reference_size', adapter.context)
                    self.assertEqual(adapter.context['photo_slots'][0]['aspect_ratio'], 1)
                schema = adapter.schema(photo_binding.schema_for(self.context))
                self.assertEqual(set(schema['properties']), {'photos'})
                self.assertEqual(adapter.decode(self.answer), self.answer)
                self.assertNotIn('回复的 coordinate_space', adapter.prompt(prompts.render('bind')))
        self.assertEqual(self.context['photo_slots'][0]['bbox'], [20, 20, 180, 180])

    def test_existing_coordinate_output_adapter_still_roundtrips(self):
        context = {'reference_size': [400, 200], 'analysis': {'reference_size': [400, 200],
                   'objects': [self.analysis['objects'][0]]}}
        adapter = ReferenceCoordinates(context, 'normalized_1000')
        raw = {'coordinate_space': 'normalized_1000', 'analysis': deepcopy(adapter.context['analysis'])}
        self.assertEqual(adapter.decode(raw)['analysis'], context['analysis'])
        self.assertNotIn('input_only', adapter.metadata)

    def test_valid_answer_makes_one_request_and_cache_resume_makes_none(self):
        original = deepcopy(self.answer)
        answer, client = self.ask()
        self.assertEqual(answer, original)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(client.usage()['requests'], 1)
        self.ask(client)
        self.assertEqual(len(self.calls), 1)
        final = photo_binding.validate(answer, self.context, self.analysis, self.run)
        self.assertEqual(final['texts'], [])
        self.assertEqual(final['schema_version'], 'collage-bindings-v1')
        self.assertEqual(self.answer, original)
        instructions = self.calls[0]['messages'][0]['content']
        self.assertIn('"source_crop"', instructions)
        self.assertIn('"additionalProperties": false', instructions)
        self.assertEqual(self.calls[0]['response_format']['type'], 'json_object')

    def test_strict_schema_transport_also_uses_photo_only_output(self):
        config = replace(self.config, response_format='json_schema', coordinate_mode='normalized_1000')
        self.ask(Client(self.root/'strict', config, transport=self.transport))
        schema = self.calls[0]['response_format']['json_schema']['schema']
        self.assertEqual(set(schema['properties']), {'photos'})
        self.assertEqual(len(self.calls), 1)

    def test_text_output_is_rejected_preserved_and_never_retried(self):
        self.answer['texts'] = [{'object_id': 'note_yellow', 'text': 'invented'}]
        original = deepcopy(self.answer)
        with self.assertRaises(ModelError):
            self.ask()
        self.assertEqual(len(self.calls), 1)
        folder = next((self.root/'vlm/bind').iterdir())
        self.assertEqual(json.loads(read(folder/'response-0.json')['content']), original)
        self.assertTrue((folder/'validation-0.json').exists())
        self.assertFalse((folder/'response-1.json').exists())
        self.assertFalse((folder/'accepted.json').exists())
        self.assertEqual(self.answer, original)

    def test_duplicate_missing_unknown_and_invalid_crops_rejected(self):
        mutations = {
            'duplicate': lambda a: a['photos'][1].update(slot_id='photo_a'),
            'missing': lambda a: a['photos'].pop(),
            'unknown_slot': lambda a: a['photos'][0].update(slot_id='note_yellow'),
            'unknown_asset': lambda a: a['photos'][0].update(asset_id='asset_unknown'),
            'inverted_crop': lambda a: a['photos'][0].update(source_crop=[.8, 0, .2, 1]),
            'out_of_range': lambda a: a['photos'][0].update(crop_center=[1.1, .5]),
            'extra_field': lambda a: a['photos'][0].update(bbox=[0, 0, 1, 1]),
            'missing_field': lambda a: a['photos'][0].pop('mirror_x'),
            'blank_reason': lambda a: a['photos'][0].update(reason=' '),
            'extra_texts_even_empty': lambda a: a.update(texts=[]),
        }
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                value = deepcopy(self.answer); mutate(value); before = deepcopy(value)
                with self.assertRaises((ValidationError, ValueError)):
                    photo_binding.validate(value, self.context, self.analysis, self.run)
                self.assertEqual(value, before)

    def test_source_integrity_is_still_enforced(self):
        Path(self.catalog['assets'][0]['file']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source changed'):
            photo_binding.validate(self.answer, self.context, self.analysis, self.run)

    def test_independent_cutout_rejects_guessed_crop(self):
        self.windows['photos'][0]['subject']['preserve_background'] = False
        self.context = photo_binding.context_for(self.base, self.analysis, self.windows)
        self.answer['photos'][0]['source_crop'] = [.1, .1, .9, .9]
        with self.assertRaises(ValidationError):
            photo_binding.validate(self.answer, self.context, self.analysis, self.run, self.windows)
        self.answer['photos'][0]['source_crop'] = [0, 0, 1, 1]
        photo_binding.validate(self.answer, self.context, self.analysis, self.run)

    def test_linked_cutout_must_share_all_four_base_parameters(self):
        self.windows['photos'][1]['subject']['preserve_background'] = False
        self.windows['photos'][1]['linked_to'] = 'photo_a'
        self.context = photo_binding.context_for(self.base, self.analysis, self.windows)
        base, front = self.context['photo_slots']
        self.assertTrue(base['requires_segmentable_subject'])
        self.assertNotIn('subject', base)
        self.assertEqual(front['linked_to'], 'photo_a')
        self.assertTrue(front['requires_segmentable_subject'])
        self.answer['photos'][0].update(source_crop=[.1, 0, .9, 1], crop_center=[.2, .7], mirror_x=True)
        self.answer['photos'][1] = {**deepcopy(self.answer['photos'][0]), 'slot_id': 'photo_b'}
        photo_binding.validate(self.answer, self.context, self.analysis, self.run)
        for field, other in [('asset_id', 'asset_1'), ('source_crop', [0, 0, 1, 1]),
                             ('crop_center', [.5, .5]), ('mirror_x', False)]:
            with self.subTest(field=field):
                bad = deepcopy(self.answer); bad['photos'][1][field] = other
                with self.assertRaisesRegex(ValueError, field):
                    photo_binding.validate(bad, self.context, self.analysis, self.run)

    def test_preserved_subject_stroke_allows_original_image_crop(self):
        self.windows['photos'][0]['subject']['stroke'].update(enabled=True, width=.03)
        self.context = photo_binding.context_for(self.base, self.analysis, self.windows)
        self.assertTrue(self.context['photo_slots'][0]['requires_segmentable_subject'])
        self.answer['photos'][0]['source_crop'] = [.1, .1, .9, .9]
        photo_binding.validate(self.answer, self.context, self.analysis, self.run)

    def test_subject_requirements_reach_transport(self):
        for preserve, stroke in ((True, False), (True, True), (False, False), (False, True)):
            with self.subTest(preserve=preserve, stroke=stroke):
                windows = deepcopy(self.windows)
                spec = windows['photos'][0]
                spec['subject']['preserve_background'] = preserve
                spec['subject']['stroke'].update(enabled=stroke, width=.03 if stroke else 0)
                # A window outline alone must never require a subject mask.
                spec['mask'] = {'enabled': True, 'stroke': {**empty_stroke(), 'enabled': True, 'width': .02}}
                spec['mask']['shape'] = {'type': 'polygon', 'points': [[0, 0], [1, 0], [.5, 1]]}
                spec['edge_gradient'].update(enabled=True, direction='outside', top=.1)
                analysis = deepcopy(self.analysis)
                analysis['objects'][0]['mode'] = 'cutout' if preserve else 'cover'
                self.windows = windows
                self.context = photo_binding.context_for(self.base, analysis, windows)
                config = replace(self.config, coordinate_mode='normalized_1000')
                client = Client(self.root/f'matrix-{preserve}-{stroke}', config,
                                transport=self.transport)
                self.ask(client)
                payload = self.calls[-1]
                wire = json.loads(payload['messages'][1]['content'][0]['text'].split('\n', 1)[1])
                slot = wire['photo_slots'][0]
                self.assertNotIn('photo_windows', wire)
                self.assertNotIn('mode', slot)
                self.assertEqual(slot['requires_segmentable_subject'], not preserve or stroke)
                self.assertFalse({'mask', 'subject', 'edge_gradient', 'linked_foregrounds'} & set(slot))
                self.assertEqual(slot['bbox'], [50, 100, 450, 900])
                from jsonschema import Draft202012Validator
                schema = photo_binding.schema_for(self.context, windows)
                changed = deepcopy(self.answer)
                changed['photos'][0]['source_crop'] = [.1, .1, .9, .9]
                self.assertEqual(Draft202012Validator(schema).is_valid(changed), preserve)

    def test_foreground_requirement_reaches_base_even_when_foreground_comes_first(self):
        analysis = deepcopy(self.analysis)
        analysis['objects'][:2] = analysis['objects'][1::-1]
        windows = deepcopy(self.windows)
        windows['photos'][1]['subject']['preserve_background'] = False
        windows['photos'][1]['linked_to'] = 'photo_a'
        windows['photos'].reverse()
        context = photo_binding.context_for(self.base, analysis, windows)
        front, base = context['photo_slots']
        self.assertEqual(front['linked_to'], 'photo_a')
        self.assertTrue(base['requires_segmentable_subject'])
        self.assertNotIn('subject', base)
        self.assertNotIn('linked_foregrounds', base)

    def test_missing_windows_stop_binding_before_any_model_call(self):
        save(self.run/'analysis.json', self.analysis)
        (self.run/'photo-windows.json').unlink()
        client = Client(self.root/'missing', self.config, transport=Mock())
        runner = Workflow(self.run, config=self.config, client=client)
        with self.assertRaisesRegex(ValueError, 'completed photo window plan'):
            runner.bind()
        self.assertEqual(client.usage()['requests'], 0)
        self.assertFalse((self.run/'bindings.json').exists())
        self.assertEqual(read(runner.control/'binding-check.json')['status'], 'failed')

    def test_structure_only_run_converts_coordinates_and_keeps_window_details_local(self):
        structure = deepcopy(self.analysis)
        structure['coordinate_mode'] = 'normalized_1000'
        structure['objects'][0]['bbox'] = [50, 100, 450, 900]
        structure['objects'][1]['bbox'] = [500, 100, 950, 900]
        save(self.run/'structure.json', structure)
        before = file_sha(self.run/'structure.json')
        client = Client(self.root/'structure-only', self.config, transport=self.transport)
        runner = Workflow(self.run, config=self.config, client=client)
        runner.progress = Mock()
        runner.command = Mock(side_effect=AssertionError('No internal analysis is needed'))
        runner.bind()
        context = read(runner.control/'binding-input.json')
        self.assertEqual(context['photo_slots'][0]['bbox'], [20, 20, 180, 180])
        self.assertFalse({'subject', 'mask', 'edge_gradient'} & set(context['photo_slots'][0]))
        self.assertEqual(read(self.run/'bindings.json')['photos'], self.answer['photos'])
        self.assertFalse((self.run/'analysis.json').exists())
        self.assertEqual(file_sha(self.run/'structure.json'), before)

    def test_cutout_constraint_is_enforced_after_strict_schema_transport(self):
        from jsonschema import Draft202012Validator
        self.windows['photos'][0]['subject']['preserve_background'] = False
        self.context = photo_binding.context_for(self.base, self.analysis, self.windows)
        config = replace(self.config, provider='yibu', model='gpt-5.4',
                         response_format='json_schema', reasoning_enabled=None)
        self.ask(Client(self.root/'strict-cutout-valid', config, transport=self.transport))
        self.answer['photos'][0]['source_crop'] = [.1, 0, .9, 1]
        # The provider subset omits array constants. The canonical contract in
        # the prompt and local validator must still reject a partial cutout crop.
        with self.assertRaises(ModelError):
            self.ask(Client(self.root/'strict-cutout-invalid', config, transport=self.transport))
        wire_schema = self.calls[-1]['response_format']['json_schema']['schema']
        Draft202012Validator(wire_schema).validate(self.answer)
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(list((self.root/'strict-cutout-invalid').rglob('accepted.json')))

    def test_incomplete_windows_and_invalid_links_are_rejected(self):
        for fault in ('missing', 'duplicate', 'self', 'unknown', 'cycle'):
            with self.subTest(fault=fault):
                windows = deepcopy(self.windows)
                photos = windows['photos']
                if fault == 'missing': photos.pop()
                elif fault == 'duplicate': photos.append(deepcopy(photos[0]))
                elif fault == 'self': photos[0]['linked_to'] = 'photo_a'
                elif fault == 'unknown': photos[1]['linked_to'] = 'note_yellow'
                else:
                    photos[0]['linked_to'] = 'photo_b'
                    photos[1]['linked_to'] = 'photo_a'
                with self.assertRaises(ValueError):
                    photo_binding.context_for(self.base, self.analysis, windows)

    def test_http_429_and_503_do_not_retry_single_call(self):
        for status in (429, 503):
            with self.subTest(status=status):
                error = HTTPError('https://example.invalid', status, 'failed', {}, BytesIO(b'{}'))
                self.addCleanup(error.close)
                transport = Mock(side_effect=error)
                sleep = Mock()
                client = Client(self.root/str(status), self.config, transport=transport, sleep=sleep)
                with self.assertRaises(ModelError): self.ask(client)
                self.assertEqual(transport.call_count, 1)
                sleep.assert_not_called()
                self.assertEqual(client.usage()['requests'], 1)

    def test_other_stages_retain_one_correction(self):
        replies = iter(['invalid', '{"ok":true}'])
        def transport(payload):
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': next(replies)}}]}
        client = Client(self.root/'other', self.config, transport=transport)
        answer = client.ask('other', 'JSON only', {}, [], {'type': 'object'})
        self.assertEqual(answer, {'ok': True})
        self.assertEqual(client.usage()['requests'], 2)

    def test_no_photos_skips_model_and_material_images(self):
        save(self.run/'structure.json', {'objects': [self.analysis['objects'][2]]})
        runner = Workflow(self.run, config=self.config, client=Client(self.root/'empty', self.config, transport=Mock()))
        runner.command = Mock(); runner.material_images = Mock(side_effect=AssertionError('No images needed'))
        runner.bind()
        self.assertEqual(read(self.run/'bindings.json')['photos'], [])
        self.assertEqual(read(self.run/'bindings.json')['texts'], [])
        self.assertEqual(runner.client.usage()['requests'], 0)

    def test_runner_failure_stops_before_build_and_records_failed_step(self):
        save(self.run/'analysis.json', self.analysis)
        self.answer['texts'] = []
        client = Client(self.root/'control/vlm', self.config, transport=self.transport)
        runner = Workflow(self.run, control=self.root/'control', config=self.config, client=client, reveal=False)
        runner.command = Mock(); runner.analyze = Mock(return_value=None); runner.photo_windows = Mock(return_value=None)
        runner.boxes = Mock(return_value={'decision': 'keep'}); runner.build = Mock()
        with self.assertRaises(ModelError): runner.execute()
        runner.build.assert_not_called()
        self.assertFalse((self.run/'bindings.json').exists())
        self.assertFalse((self.run/'material-inventory.json').exists())
        self.assertNotIn('materials', runner.state['steps'])
        self.assertEqual(runner.state['steps']['bind']['status'], 'failed')
        self.assertEqual(read(runner.control/'binding-check.json')['status'], 'failed')
        self.assertEqual(len(self.calls), 1)

    def saved_runner(self, built=False):
        save(self.run/'analysis.json', self.analysis)
        save(self.run/'bindings.json', {'schema_version': 'collage-bindings-v1',
             'photos': deepcopy(self.answer['photos']), 'texts': [{'object_id': 'note_yellow', 'text': 'legacy'}]})
        steps = {name: {'status': 'complete', 'value': None}
                 for name in ('analyze', 'photo_windows', 'boxes_apply', 'bind')}
        steps['boxes'] = {'status': 'complete', 'value': {'decision': 'keep'}}
        if built:
            steps['build'] = {'status': 'complete', 'value': None}
        # A saved task retains its identity and completed steps.
        identity = digest({'input': file_sha(self.run/'input.json'),
                           'catalog': file_sha(self.run/'prepared/catalog.json'),
                           'model': self.config.model, 'reveal': False})
        save(self.run/'workflow/state.json', {'controller': 'workflow-v9', 'identity': identity,
             'status': 'failed', 'binding_contract': photo_binding.VERSION, 'steps': steps})
        client = Client(self.run/'workflow/vlm', self.config, transport=self.transport)
        runner = Workflow(self.run, config=self.config, client=client, reveal=False)
        runner.command = Mock()
        for name in ('analyze', 'photo_windows', 'boxes', 'apply_boxes', 'build', 'screen', 'register'):
            setattr(runner, name, Mock(return_value=None))
        return runner



    def test_saved_built_task_retains_bindings_and_finishes_without_model_calls(self):
        runner = self.saved_runner(built=True)
        before = file_sha(self.run/'bindings.json')
        runner.bind = Mock(side_effect=AssertionError('Built task must retain binding'))
        runner.execute()
        runner.bind.assert_not_called(); runner.build.assert_not_called()
        runner.register.assert_called_once()
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(file_sha(self.run/'bindings.json'), before)
        self.assertEqual(runner.state['binding_contract'], photo_binding.VERSION)
        self.assertNotIn('materials', runner.state['steps'])

    def test_saved_finished_task_is_unchanged_and_still_checks_integrity(self):
        runner = self.saved_runner(built=True)
        (self.run/'final.png').write_bytes((self.run/'prepared/reference.png').read_bytes())
        save(self.run/'scene.json', {'legacy': True})
        previous = {'render_id': 'legacy-render', 'final_sha256': file_sha(self.run/'final.png'),
                    'scene_sha256': file_sha(self.run/'scene.json')}
        save(self.run/'result.json', previous)
        runner.state.update(status='finished', last_render_id='legacy-render')
        before = file_sha(self.run/'bindings.json')
        self.assertEqual(runner.execute(), previous)
        runner.register.assert_not_called()
        self.assertEqual(file_sha(self.run/'bindings.json'), before)
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(runner.state['binding_contract'], photo_binding.VERSION)
        save(self.run/'scene.json', {'changed': True})
        with self.assertRaises(ValueError): runner.execute()

    def test_unknown_binding_contract_rejected_before_mutation(self):
        state = {'binding_contract': 'unknown-future-contract', 'steps': {}}
        save(self.run/'workflow/state.json', state)
        runner = Workflow(self.run, config=self.config)
        with self.assertRaisesRegex(ValueError, '绑定协议'): runner.execute()
        self.assertEqual(read(self.run/'workflow/state.json'), state)
        self.assertFalse((self.run/'workflow-models.json').exists())

    def test_saved_input_or_model_changes_still_rejected(self):
        runner = self.saved_runner()
        save(self.run/'input.json', {'reference_size': [400, 200], 'instructions': 'changed'})
        with self.assertRaisesRegex(ValueError, '任务输入'): runner.execute()
        self.assertEqual(len(self.calls), 0)
        self.assertEqual(runner.state['binding_contract'], photo_binding.VERSION)
        runner = self.saved_runner()
        runner.state['model_policy'] = replace(self.config, model='different-model').model_policy()
        with self.assertRaisesRegex(ValueError, '模型配置'): runner.execute()
        self.assertEqual(len(self.calls), 0)

    def test_full_local_render_and_resume_with_one_bind_request(self):
        from prepare import prepare
        from collage.workflow.support.fallbacks import simple_bundle
        from photo_windows import defaults
        materials = self.root/'materials'; materials.mkdir()
        Image.new('RGB', (300, 250), '#db6531').save(materials/'one.png')
        Image.new('RGB', (200, 280), '#2566bc').save(materials/'two.png')
        run = self.root/'render-run'
        prepare(self.run/'prepared/reference.png', [materials], run, width=400)
        bundle = simple_bundle(run)
        save(run/'structure.json', {**bundle['analysis'], 'coordinate_mode': 'pixel'})
        save(run/'analysis.json', bundle['analysis']); save(run/'text-units.json', [])
        save(run/'photo-windows.json', defaults(bundle['analysis']))
        before = file_sha(run/'analysis.json')
        self.answer = {'photos': [{**p, 'source_crop': [0, 0, 1, 1], 'crop_center': [.5, .5],
                                   'mirror_x': False, 'reason': 'Offline selected customer photo.'}
                                  for p in bundle['bindings']['photos']]}
        box_calls = []
        def transport(payload):
            context = json.loads(payload['messages'][1]['content'][0]['text'].split('\n', 1)[1])
            if 'targets' not in context:
                return self.transport(payload)
            box_calls.append(deepcopy(payload))
            # Exercise the current v6 box stage before binding, including its
            # independent edge-movement update. No provider is contacted.
            answer = {'movement_unit': 'original_bbox_percent', 'objects': [
                {'id': target['id'], 'status': 'keep',
                 'edges': {edge: {'action': 'keep', 'percent': 0}
                           for edge in ('left', 'top', 'right', 'bottom')}, 'reason': 'Keep visible bounds.'}
                for target in context['targets']]}
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(answer)}}]}
        client = Client(self.root/'render-control/vlm', self.config, transport=transport)
        runner = Workflow(run, control=self.root/'render-control', config=self.config, client=client,
                        reveal=False, bbox_refinement=True)
        runner.analyze = Mock(return_value=None); runner.photo_windows = Mock(return_value=None)
        result = runner.execute()
        self.assertTrue((run/'final.png').is_file())
        self.assertEqual(file_sha(run/'analysis.json'), before)
        self.assertEqual(read(run/'bindings.json')['texts'], [])
        self.assertEqual(read(run/'bindings.json')['photos'], self.answer['photos'])
        self.assertNotIn('materials', runner.state['steps'])
        self.assertFalse((run/'material-inventory.json').exists())
        self.assertEqual(runner.state['status'], 'finished')
        self.assertEqual(runner.state['controller'], 'workflow-v9')
        self.assertEqual(runner.state['binding_contract'], photo_binding.VERSION)
        self.assertEqual(len(box_calls), 1)
        self.assertEqual(read(runner.control/'boxes-decision.json')['decision'], 'keep')
        self.assertFalse(runner.state.get('warnings'))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(runner.execute()['render_id'], result['render_id'])
        self.assertEqual(len(self.calls), 1)


if __name__ == '__main__':
    unittest.main()
