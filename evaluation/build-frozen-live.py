"""Build first renders with live Reveal from this batch's immutable analysis/bindings."""
import argparse
import concurrent.futures
import shutil
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read, save, sha, timed, now, locked
from prepare import prepare
from scene import compile_scene
from render import render


def build_case(source, root, name):
    old = source/name/'run'
    case = root/name
    run = case/'run'
    statefile = case/'build-state.json'
    if statefile.exists():
        return read(statefile)
    state = {'case': name, 'status': 'running', 'started_at': now(), 'source_run': str(old)}
    save(statefile, state)
    print('BUILD START', name, flush=True)
    started = time.perf_counter()
    try:
        frozen = read(old/'analysis-freeze.json')
        meta = read(old/'input.json')
        prepare(meta['original_reference']['file'], meta['materials'], run,
                width=meta['output_width'], instructions='Live first render from frozen analysis; no reanalysis or corrections.',
                cutout_model=meta.get('cutout_model'))
        for kind in ['analysis', 'bindings']:
            src = old/f'{kind}-first.json'
            if sha(src) != frozen[kind+'_sha256']:
                raise ValueError('Frozen source hash mismatch: '+kind)
            shutil.copyfile(src, run/f'{kind}.json')
        if sha(run/'prepared/reference.png') != sha(old/'prepared/reference.png'):
            raise ValueError('Prepared reference differs from analysis input')
        config = {'enabled': True, 'remote': True, 'cache': None,
                  'key_file': 'D:/codes/.env', 'timeout': 360, 'padding': .1}
        start = time.perf_counter()
        with locked(run), timed(run, 'build'):
            compile_scene(run, config)
            result = render(run)
        duration = time.perf_counter()-start
        index = read(run/'assets/reveal/index.json')
        tasks = []
        for folder in sorted((run/'assets/reveal/api').glob('batch_*')):
            submit = read(folder/'submit_response.json') if (folder/'submit_response.json').exists() else {}
            tasks.append({'path': str(folder), 'task_id': submit.get('task_id')})
        evidence = {'case': name, 'built_at': now(), 'experiment': 'frozen-analysis-live-reveal',
            'old_run': str(old), 'remote_submissions': sum(bool(t['task_id']) for t in tasks), 'tasks': tasks,
            'build_seconds': round(duration, 3), 'prepare_and_build_seconds': round(time.perf_counter()-started, 3),
            'frozen_inputs': {f: sha(run/f) for f in ['analysis.json', 'bindings.json']},
            'actions': {action: sum(r['action'] == action for r in index['records']) for action in {r['action'] for r in index['records']}},
            'incomplete': result['incomplete_objects'], 'photos_verified': result['customer_photos_verified'],
            'visible_photos': result['photo_visible_fractions']}
        save(case/'build-evidence.json', evidence)
        state.update(status='completed', build_seconds=round(duration, 3), remote_submissions=evidence['remote_submissions'])
    except Exception as exc:
        state.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    state.update(finished_at=now(), elapsed_seconds=round(time.perf_counter()-started, 3))
    save(statefile, state)
    print('BUILD END', name, state['status'], state['elapsed_seconds'], flush=True)
    return state


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', required=True);p.add_argument('--root', required=True)
    p.add_argument('--workers', type=int, default=2);p.add_argument('--wait-minutes', type=float, default=45)
    args = p.parse_args();source = Path(args.source).resolve();root = Path(args.root).resolve()
    manifest = read(source/'manifest.json');names = [Path(n).stem for n in manifest['names']]
    manifest_path = root/'build-manifest.json'
    if manifest_path.exists():
        saved = read(manifest_path)
        if saved['analysis_root'] != str(source):raise ValueError('Different source batch')
    else:
        skill = Path(__file__).resolve().parents[1]
        files = dict.fromkeys(manifest['skill_files'])
        files['evaluation/build-frozen-live.py'] = None
        for f in files:
            files[f] = sha(skill/f)
            dest = root/'_build_skill_snapshot'/f
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(skill/f, dest)
        save(manifest_path, {'started_at': now(), 'analysis_root': str(source), 'names': names,
            'model': manifest['model'], 'effort': manifest['effort'], 'materials': manifest['materials'],
            'mode': 'frozen-analysis-live-reveal', 'padding': .1, 'workers': args.workers,
            'skill': str(skill), 'skill_files': files,
            'reanalysis': False, 'remote_reveal_authorized': True, 'generation_allowed': False})
    pending = set(names);states = [];futures = {};deadline = time.monotonic()+args.wait_minutes*60
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        while pending or futures:
            for future in list(futures):
                if future.done():
                    states.append(future.result());del futures[future]
            for name in names:
                if name not in pending or len(futures) >= args.workers:continue
                if (source/name/'run/analysis-freeze.json').exists():
                    pending.remove(name);futures[pool.submit(build_case, source, root, name)] = name
            if pending and time.monotonic() > deadline:
                states += [{'case': n, 'status': 'analysis_unavailable'} for n in sorted(pending)];pending.clear()
            if pending or futures:time.sleep(2)
    save(root/'build-batch.json', {'finished_at': now(), 'states': states})


if __name__ == '__main__':
    main()
