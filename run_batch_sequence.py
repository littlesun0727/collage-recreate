"""Resume a sequence of workbench batches, submitting only one stage at a time."""
import argparse
import json
import os
from pathlib import Path
import time
import uuid

from batch_run import Workbench, parser as batch_parser, plan


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def run(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    state_path = manifest_path.with_suffix('.state.json')
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'stages': []}
    client = Workbench(manifest['server'])
    client.connect(start=False)
    for index, stage in enumerate(manifest['stages']):
        if index < len(state['stages']) and state['stages'][index].get('status') == 'completed':
            continue
        if index == len(state['stages']):
            config = (manifest_path.parent/stage['config']).resolve()
            batch = plan(batch_parser().parse_args(['--config', str(config)]))
            upload = client.request('/api/uploads', {'multiple_references': True})
            for role, records in [('reference', batch['references']), ('material', batch['materials'])]:
                for item in records:
                    uploaded = client.request(f"/api/uploads/{upload['id']}/files", file=Path(item['path']), role=role)
                    if uploaded['sha256'] != item['sha256']:
                        raise ValueError('Input changed during upload')
            payload = {k: batch[k] for k in ('name', 'model_policy', 'width', 'instructions', 'bbox_refinement', 'screen')}
            payload.update(id='run-'+uuid.uuid4().hex, upload_id=upload['id'])
            state['stages'].append({'payload': payload, 'status': 'prepared'})
            save(state_path, state)
        current = state['stages'][index]
        # Persisted IDs make resuming after an uncertain response idempotent.
        client.connect(start=False)
        batch = client.request('/api/batches', current['payload'])
        while True:
            current.update(status=batch['status'], counts=batch['counts'])
            save(state_path, state)
            print(json.dumps({'stage': index+1, 'model': stage['name'], 'id': batch['id'],
                              'status': batch['status'], 'counts': batch['counts']}, ensure_ascii=False), flush=True)
            if batch['status'] in {'completed', 'failed'}:
                break
            time.sleep(10)
            batch = client.request('/api/batches/'+batch['id'])
        if batch['status'] == 'failed' and stage.get('stop_on_failure', False):
            raise RuntimeError('Validation sample failed; remaining batches were not submitted')
    state['finished'] = True
    save(state_path, state)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest')
    args = parser.parse_args()
    # Hold an OS lock for the full sequence so two launchers cannot pay twice.
    with Path(args.manifest).with_suffix('.lock').open('a+b') as lock:
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.manifest)
