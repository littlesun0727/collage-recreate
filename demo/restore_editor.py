"""Restore editable native tasks and re-pin JSON dependencies at their new paths."""
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(temp, path)


def inside(root, relative):
    p = (root/relative).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError('Path outside migration directory')
    return p


def restore(workspace, only=None):
    workspace = Path(workspace).resolve()
    manifest = read(ROOT/'editable/manifest.json')
    workspace.mkdir(parents=True, exist_ok=True)
    model_info = manifest['model']
    model = workspace/'models'/model_info['name']
    if not model.is_file():
        model.parent.mkdir(parents=True, exist_ok=True)
        temporary = model.with_suffix('.tmp')
        with temporary.open('wb') as out:
            for part in model_info['parts']:
                p = inside(ROOT, part['file'])
                if digest(p) != part['sha256']:
                    raise ValueError('Model part checksum mismatch: '+p.name)
                with p.open('rb') as src:
                    shutil.copyfileobj(src, out)
        if digest(temporary) != model_info['sha256']:
            raise ValueError('Model checksum mismatch')
        os.replace(temporary, model)
    verified = set()
    restored = []
    for task in manifest['tasks']:
        if only and task['id'] not in only and task['name'] not in only:
            continue
        run = inside(workspace/'tasks', task['name'])
        marker = run/'portable-task.json'
        importing = run/'.portable-import.json'
        if marker.is_file():
            previous = read(marker)
            if previous['root'] != str(run):
                raise ValueError('This editable workspace was moved. Use relocate_editor.py first.')
            restored.append(task['id'])
            continue  # Never overwrite user edits on restart or git pull.
        expected_import = {'id': task['id'], 'template_sha256': task['sha256']}
        resumable = importing.is_file() and read(importing) == expected_import
        if run.exists() and any(run.iterdir()) and not resumable:
            raise ValueError('Incomplete restore; move this directory aside and retry: '+str(run))
        template_path = inside(ROOT, task['template'])
        if digest(template_path) != task['sha256']:
            raise ValueError('Task template checksum mismatch: '+task['name'])
        template = read(template_path)
        save(importing, expected_import)
        docs = {}

        def expand(value):
            if isinstance(value, dict):
                return {k: expand(v) for k, v in value.items()}
            if isinstance(value, list):
                return [expand(v) for v in value]
            if isinstance(value, str):
                if value == '@MODEL@':
                    return str(model)
                if value.startswith('@RUN@/'):
                    return str(inside(run, value[6:]))
            return value

        for relative, record in template['files'].items():
            p = inside(run, relative)
            p.parent.mkdir(parents=True, exist_ok=True)
            if 'blob' in record:
                blob = manifest['blobs'][record['blob']]
                source = inside(ROOT, blob['file'])
                if record['blob'] not in verified:
                    if source.stat().st_size != blob['bytes'] or digest(source) != blob['sha256']:
                        raise ValueError('Media checksum mismatch: '+source.name)
                    verified.add(record['blob'])
                shutil.copyfile(source, p)
            elif 'json' in record:
                docs[p] = expand(record['json'])
            else:
                p.write_text('\n'.join(json.dumps(expand(r), ensure_ascii=False) for r in record['jsonl'])+'\n', encoding='utf-8')
        # All imported materials are now inside this task's own directory.
        docs[run/'input.json']['materials'] = sorted({str(Path(a['file']).parent) for a in docs[run/'prepared/catalog.json']['assets']})
        repin(run, docs)
        save(marker, {'id': task['id'], 'root': str(run), 'model': str(model),
                      'template_sha256': task['sha256']})
        importing.unlink(missing_ok=True)
        restored.append(task['id'])
        print('Restored '+task['name'], flush=True)
    return manifest, restored


def repin(run, docs):
    """Topologically serialize JSONs; update hashes of JSONs, never image pixels."""
    done = {}
    visiting = set()
    scene_path = run/'scene.json'
    old_sources = dict(docs[scene_path]['sources'])
    new_sources = {}

    def write(path):
        if path in done:
            return done[path]
        if path in visiting:
            raise ValueError('Circular JSON dependency: '+str(path))
        visiting.add(path)

        def walk(value):
            if isinstance(value, list):
                return [walk(v) for v in value]
            if not isinstance(value, dict):
                return value
            obj = {k: walk(v) for k, v in value.items()}
            if obj.get('sources') == old_sources and new_sources:
                obj['sources'] = dict(new_sources)
            source = obj.get('file')
            if isinstance(source, str) and 'sha256' in obj and Path(source) in docs:
                obj['sha256'] = write(Path(source))
            if obj.get('raw_file') and Path(obj['raw_file']) in docs:
                obj['raw_sha256'] = write(Path(obj['raw_file']))
            return obj

        obj = walk(docs[path])
        if path.name == 'scene.json' and path == scene_path:
            obj['sources'] = dict(new_sources)
            if obj.get('reveal_config_sha256'):
                obj['reveal_config_sha256'] = write(run/'reveal-config.json')
        if path.name == 'result.json' and (path.parent/'scene.json') in docs:
            obj['scene_sha256'] = write(path.parent/'scene.json')
        if path.name == 'review.json' and (path.parent/'scene.json') in docs:
            obj['scene_sha256'] = write(path.parent/'scene.json')
        if path.name == 'manifest.json' and 'artifacts' in obj:
            for record in obj['artifacts'].values():
                target = inside(path.parent, record['path'])
                record['sha256'] = write(target) if target in docs else digest(target)
        save(path, obj)
        done[path] = digest(path)
        visiting.remove(path)
        return done[path]

    for key, name in [('analysis', 'analysis.json'), ('bindings', 'bindings.json'),
                      ('input', 'input.json'), ('catalog', 'prepared/catalog.json')]:
        new_sources[key] = write(run/name)
    # Use the complete source set consistently in gate records and current scene.
    for path in docs:
        write(path)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=ROOT/'workspace')
    parser.add_argument('--only', action='append')
    args = parser.parse_args()
    restore(args.workspace, args.only)
