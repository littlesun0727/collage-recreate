import json
from pathlib import Path

import pytest
import run_batch_sequence as sequence


@pytest.mark.parametrize('first_status', ['completed', 'failed'])
def test_sequence_waits_for_terminal_status_and_stops_failed_canary(tmp_path, monkeypatch, first_status):
    submitted, polled = [], []
    class Client:
        def __init__(self, base): pass
        def connect(self, **kwargs): pass
        def request(self, route, payload=None, **kwargs):
            if route == '/api/uploads': return {'id': 'upload'}
            if route == '/api/batches':
                submitted.append(payload['name'])
                return {'id': payload['id'], 'status': 'running', 'counts': {}}
            polled.append(route)
            return {'id': route.split('/')[-1], 'status': first_status, 'counts': {}}
    monkeypatch.setattr(sequence, 'Workbench', Client)
    monkeypatch.setattr(sequence.time, 'sleep', lambda _: None)
    monkeypatch.setattr(sequence, 'plan', lambda args: dict(name=Path(args.config).stem,
        references=[], materials=[], model_policy={}, width=1200, instructions='', bbox_refinement=False, screen=False))
    manifest = tmp_path/'sequence.json'
    manifest.write_text(json.dumps({'server':'http://127.0.0.1:1','stages':[
        {'name':'sol','config':'sol.json','stop_on_failure':True},
        {'name':'qwen','config':'qwen.json'}, {'name':'luna','config':'luna.json'}]}))
    if first_status == 'failed':
        with pytest.raises(RuntimeError): sequence.run(manifest)
        assert submitted == ['sol']
    else:
        sequence.run(manifest)
        assert submitted == ['sol', 'qwen', 'luna']
        assert len(polled) == 3
        sequence.run(manifest)
        assert submitted == ['sol', 'qwen', 'luna']
