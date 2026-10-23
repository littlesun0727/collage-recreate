"""Offline checks for optional calibration/screening and frozen task settings."""
from copy import deepcopy
from pathlib import Path
import re
import threading
import time
from unittest.mock import Mock
import uuid

import pytest
from PIL import Image

import batch_run
from collage.config import Config
from collage.workflow.pipeline import Workflow
from collage.storage import read, save
from workbench.server.creation import Creation
from workbench.server.http import create_server
from workbench.server.motion import read as read_job


@pytest.fixture
def prepared(tmp_path):
    run = tmp_path/'task'
    save(run/'input.json', {'reference_size': [200, 300]})
    save(run/'prepared/catalog.json', {'assets': []})
    Image.new('RGB', (200, 300), 'white').save(run/'prepared/reference.png')
    save(run/'analysis.json', {
        'schema_version': 'collage-analysis-v1', 'reference_size': [200, 300],
        'objects': [
            {'id': 'bg', 'kind': 'background', 'background_type': 'simple_design',
             'method': 'local', 'bbox': [0, 0, 200, 300], 'label': '底色',
             'description': '底色', 'style': {'fill': '#FFFFFF'}},
            {'id': 'deco', 'kind': 'overlay', 'method': 'local', 'bbox': [50, 60, 150, 160],
             'label': '装饰', 'description': '圆形装饰',
             'style': {'shape': 'ellipse', 'fill': '#FF0000'}}],
        'layer_order': ['bg', 'deco']})
    save(run/'text-units.json', [])
    return run


def offline_runner(run, **kwargs):
    client = Mock()
    client.usage.return_value = {'requests': 0}
    answer = {'movement_unit': 'original_bbox_percent', 'objects': [{
        'id': 'deco', 'status': 'adjust', 'reason': '补全右边界',
        'edges': {edge: {'action': 'expand' if edge == 'right' else 'keep',
                         'percent': 10 if edge == 'right' else 0}
                  for edge in ('left', 'top', 'right', 'bottom')}}]}
    def ask(stage, prompt, context, images, schema, validate):
        assert stage == 'boxes'
        from jsonschema import Draft202012Validator
        Draft202012Validator(schema).validate(answer)
        validate(answer)
        return deepcopy(answer)
    client.ask.side_effect = ask
    runner = Workflow(run, config=Config(), client=client, reveal=False, **kwargs)
    for name in ('command', 'analyze', 'photo_windows', 'bind', 'build', 'screen', 'register'):
        setattr(runner, name, Mock(return_value=None))
    return runner


@pytest.mark.parametrize('enabled', [None, False, True])
def test_execute_skips_or_applies_real_refinement(prepared, enabled):
    before = (prepared/'analysis.json').read_bytes()
    runner = offline_runner(prepared, bbox_refinement=enabled)
    runner.execute()
    runner.bind.assert_called_once()
    runner.build.assert_called_once()
    runner.photo_windows.assert_called_once()
    assert runner.client.ask.call_count == (1 if enabled else 0)
    assert read(prepared/'workflow-options.json') == {'bbox_refinement': bool(enabled), 'screen': False}
    assert read(prepared/'workflow-timing.json')['workflow_options'] == runner.workflow_options
    if enabled:
        assert read(prepared/'analysis.json')['objects'][1]['bbox'] == [50, 60, 160, 160]
        assert runner.state['steps']['boxes_apply']['status'] == 'complete'
    else:
        assert (prepared/'analysis.json').read_bytes() == before
        assert runner.state['steps']['boxes']['status'] == 'skipped'
        assert runner.state['steps']['boxes_apply']['status'] == 'skipped'
        assert not (prepared/'workflow/boxes-refinement').exists()


@pytest.mark.parametrize('enabled', [False, True])
def test_resume_uses_snapshot_without_repeating_calibration(prepared, enabled):
    runner = offline_runner(prepared, bbox_refinement=enabled)
    runner.bind.side_effect = RuntimeError('interrupted after boxes')
    with pytest.raises(RuntimeError):
        runner.execute()
    before = (prepared/'analysis.json').read_bytes()
    resumed = offline_runner(prepared)
    assert resumed.workflow_options['bbox_refinement'] is enabled
    resumed.execute()
    resumed.client.ask.assert_not_called()
    resumed.analyze.assert_not_called()
    assert (prepared/'analysis.json').read_bytes() == before
    with pytest.raises(ValueError, match='快照'):
        offline_runner(prepared, bbox_refinement=not enabled)


def test_disabled_does_not_apply_pending_legacy_correction(prepared):
    corrected = read(prepared/'analysis.json')
    corrected['objects'][1]['bbox'] = [0, 0, 200, 300]
    save(prepared/'workflow/state.json', {'photo_windows_version': 1, 'steps': {
        'boxes': {'status': 'complete', 'value': {'decision': 'replace',
                  'analysis': corrected, 'text_units': [], 'summary': 'old correction'}}}})
    before = (prepared/'analysis.json').read_bytes()
    runner = offline_runner(prepared)
    runner.execute()
    runner.client.ask.assert_not_called()
    assert (prepared/'analysis.json').read_bytes() == before
    assert runner.state['steps']['boxes_apply']['status'] == 'skipped'


def screening_runner(run, **kwargs):
    runner = offline_runner(run, **kwargs)
    runner.screen = Workflow.screen.__get__(runner)
    runner.current_context = Mock(return_value={'objects': [{'id': 'frame_1', 'kind': 'frame'}]})
    runner.review_images = Mock(return_value=[])
    runner.mutate = Mock(return_value=None)
    save(run/'result.json', {'render_id': 'base'})
    save(run/'assets/gate/report.json', {'records': [{'id': 'frame_1', 'candidate': {'file': 'frame.png'}}]})
    def ask(stage, prompt, context, images, schema, validate):
        from jsonschema import Draft202012Validator
        assert stage == 'screen' and context['screenable_ids'] == ['frame_1']
        answer = {'decisions': [{'id': 'frame_1', 'action': 'keep', 'photo_content': 'absent',
                                 'reason': 'No reference photo remains.'}]}
        Draft202012Validator(schema).validate(answer)
        validate(answer)
        return answer
    runner.client.ask.side_effect = ask
    return runner


@pytest.mark.parametrize('enabled', [None, False, True])
def test_screen_switch_controls_model_and_apply_even_with_frame_candidates(prepared, monkeypatch, enabled):
    validation = Mock()
    monkeypatch.setattr('screening.screen', validation)
    runner = screening_runner(prepared, screen=enabled)
    runner.execute()
    runner.register.assert_called_once()
    assert read(prepared/'workflow-options.json')['screen'] is bool(enabled)
    assert runner.client.ask.call_count == runner.mutate.call_count == int(bool(enabled))
    assert validation.call_count == int(bool(enabled))
    if enabled:
        validation.assert_called_once_with(prepared, prepared/'workflow/screen-check.json', dry_run=True)
        assert runner.state['steps']['screen_apply']['status'] == 'complete'
    else:
        runner.current_context.assert_not_called()
        assert runner.state['steps']['screen']['status'] == 'skipped'
        assert runner.state['steps']['screen_apply']['status'] == 'skipped'


@pytest.mark.parametrize('enabled', [False, True])
def test_screen_resume_keeps_switch_without_repeating_request_or_apply(prepared, monkeypatch, enabled):
    monkeypatch.setattr('screening.screen', Mock())
    runner = screening_runner(prepared, screen=enabled)
    runner.register.side_effect = RuntimeError('interrupted before delivery')
    with pytest.raises(RuntimeError):
        runner.execute()
    resumed = screening_runner(prepared)
    # An explicit bbox setting must not reset the separately persisted screen flag.
    assert offline_runner(prepared, bbox_refinement=False).workflow_options['screen'] is enabled
    assert resumed.workflow_options['screen'] is enabled
    resumed.execute()
    resumed.client.ask.assert_not_called()
    resumed.mutate.assert_not_called()
    resumed.register.assert_called_once()
    with pytest.raises(ValueError, match='快照'):
        offline_runner(prepared, screen=not enabled)


def test_disabled_screen_does_not_apply_a_saved_pending_decision(prepared):
    save(prepared/'workflow/state.json', {'steps': {'screen': {'status': 'complete',
         'value': {'render_id': 'old', 'decisions': [{'id': 'frame_1', 'action': 'drop'}]}}}})
    runner = offline_runner(prepared)
    runner.mutate = Mock()
    runner.execute()
    runner.screen.assert_not_called()
    runner.mutate.assert_not_called()
    assert runner.state['steps']['screen_apply']['status'] == 'skipped'


@pytest.fixture
def batch_config(tmp_path):
    for name in ('ref.png', 'mat.png'):
        Image.new('RGB', (140, 180), 'blue').save(tmp_path/name)
    path = tmp_path/'batch.json'
    save(path, {'model': 'qwen3.8-flash', 'provider': 'yibu', 'reasoning': 'off',
                'references': ['ref.png'], 'materials': ['mat.png']})
    return path


@pytest.mark.parametrize('option', ['bbox_refinement', 'screen'])
@pytest.mark.parametrize('configured,flag,expected', [
    (None, None, False), (None, True, True),
    (True, None, True), (True, False, False), (False, True, True)])
def test_batch_cli_precedence(batch_config, option, configured, flag, expected):
    value = read(batch_config)
    if configured is not None:
        value[option] = configured
    save(batch_config, value)
    args = ['--config', str(batch_config)]
    if flag is not None:
        args.append(('--' if flag else '--no-') + option.replace('_', '-'))
    assert batch_run.plan(batch_run.parser().parse_args(args))[option] is expected


@pytest.mark.parametrize('option', ['bbox_refinement', 'screen'])
@pytest.mark.parametrize('bad', ['false', 0, 1, None])
def test_batch_rejects_non_boolean_switch(batch_config, option, bad):
    value = read(batch_config); value[option] = bad
    save(batch_config, value)
    with pytest.raises(ValueError, match=option):
        batch_run.plan(batch_run.parser().parse_args(['--config', str(batch_config)]))


@pytest.mark.parametrize('flag', ['--bbox-refinement', '--no-bbox-refinement', '--screen', '--no-screen'])
def test_resume_rejects_switch_override_before_network(flag, monkeypatch):
    monkeypatch.setattr(batch_run.Workbench, 'connect', Mock(side_effect=AssertionError('network')))
    assert batch_run.main(['--resume', 'unused.json', flag]) == 1
    batch_run.Workbench.connect.assert_not_called()


@pytest.mark.parametrize('enabled', [False, True])
def test_batch_submission_and_receipt_include_screen_option(batch_config, monkeypatch, enabled):
    client = Mock(base='http://127.0.0.1:8809')
    def request(route, payload=None, file=None, role=None):
        if route == '/api/uploads':
            return {'id': 'upload-test'}
        if file is not None:
            from collage.storage import file_sha
            return {'sha256': file_sha(file), 'bytes': file.stat().st_size}
        assert route == '/api/batches' and payload['screen'] is enabled
        return {'id': payload['id'], 'name': payload['name'], 'total': 1}
    client.request.side_effect = request
    monkeypatch.setattr(batch_run, 'Workbench', Mock(return_value=client))
    monkeypatch.setattr(batch_run, 'OUTPUT_ROOT', batch_config.parent/'output')
    assert batch_run.main(['--config', str(batch_config), '--screen' if enabled else '--no-screen']) == 0
    receipt = read(next((batch_config.parent/'output/batch-receipts').glob('*.json')))
    assert receipt['payload']['screen'] is receipt['inputs']['screen'] is enabled


class OfflineCreation(Creation):
    def preflight(self, config=None):
        pass

    def work(self, service, path, job):
        from workbench.server.motion import save as job_save
        job.update(status='completed', elapsed_seconds=0)
        job_save(path, job)
        with service.guard:
            service.active.discard(job['id'])


@pytest.fixture
def server(tmp_path):
    server = create_server([tmp_path/'tasks'], port=0, enable_create=True,
                           media_root=tmp_path/'service', creator=OfflineCreation())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = batch_run.Workbench(f'http://127.0.0.1:{server.server_port}')
    client.connect(start=False)
    yield server, client
    server.shutdown(); server.server_close(); thread.join(5)


@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('options', [{}, {'bbox_refinement': True}, {'screen': True},
                                    {'screen': False}, {'bbox_refinement': True, 'screen': True}])
def test_http_upload_through_creation_freezes_options(server, batch_config, batch, options):
    service, client = server
    upload_id = client.request('/api/uploads', {'multiple_references': batch})['id']
    for name, role in [('ref.png', 'reference'), ('mat.png', 'material')]:
        client.request(f'/api/uploads/{upload_id}/files', file=batch_config.parent/name, role=role)
    payload = {'model_policy': Config().model_policy(), **options}
    expected = {'bbox_refinement': False, 'screen': False, **options}
    if batch:
        payload.update(id='run-'+uuid.uuid4().hex, upload_id=upload_id, name='switch test')
        response = client.request('/api/batches', payload)
        assert response['workflow_options'] == expected
    else:
        response = client.request(f'/api/uploads/{upload_id}/prepare', payload)
        imported = client.request('/api/uploads/jobs/'+response['id'])
        assert imported['workflow_options'] == expected
    deadline = time.monotonic()+25
    while time.monotonic() < deadline:
        jobs = [read_job(path) for path in (service.motion.root/'jobs').glob('create-*.json')]
        if jobs and jobs[0].get('status') == 'completed':
            break
        time.sleep(.05)
    else:
        pytest.fail('Import/creation did not complete')
    run = Path(jobs[0]['run'])
    assert re.fullmatch(r'task-\d{8}-\d{3,}', run.name)
    assert jobs[0]['workflow_options'] == expected
    assert read(run/'workflow-options.json') == jobs[0]['workflow_options']
    assert Workflow(run, config=Config(), client=Mock()).workflow_options == jobs[0]['workflow_options']
    if batch:
        record = read(service.motion.root/'batches'/(payload['id']+'.json'))
        assert record['jobs'][0]['workflow_options'] == jobs[0]['workflow_options']
        assert read(run/'batch-run.json')['workflow_options'] == jobs[0]['workflow_options']
    # Retrying an acknowledged upload/batch must keep the reserved directory.
    counter = read(run.parent/'.task-names.json')
    repeated = client.request('/api/batches' if batch else f'/api/uploads/{upload_id}/prepare', payload)
    assert repeated['id'] == response['id']
    assert read(run.parent/'.task-names.json') == counter
    imports = [job for path in (service.motion.root/'jobs').glob('import-*.json')
               if (job := read_job(path)).get('kind') == 'import']
    assert len(imports) == 1 and imports[0]['run'] == str(run)
