"""Rebase an existing edited workspace after copying it to another directory/OS.

Stop the workbench and copy the entire workspace, then run this on the copy.
Original JSONs are retained in a backup archive before rewriting any file.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import zipfile
from restore_editor import read, save, repin


def relocate(workspace):
    workspace = Path(workspace).resolve()
    tasks = list((workspace/'tasks').glob('*/portable-task.json'))
    if not tasks:
        raise ValueError('No imported editable tasks found')
    changed = [p for p in tasks if read(p)['root'] != str(p.parent)]
    if not changed:
        print('Workspace already uses this location')
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    backup = workspace/('before-relocate-'+stamp+'.zip')
    with zipfile.ZipFile(backup, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for marker in changed:
            for p in marker.parent.rglob('*.json'):
                archive.write(p, p.relative_to(workspace))
            for p in marker.parent.rglob('*.jsonl'):
                archive.write(p, p.relative_to(workspace))
    for marker in changed:
        run = marker.parent
        info = read(marker)
        before = info['root'].replace('\\', '/').rstrip('/')
        old_model = info['model'].replace('\\', '/')
        model = workspace/'models'/old_model.rsplit('/', 1)[-1]

        def rebase(v):
            if isinstance(v, dict):
                return {k: rebase(x) for k, x in v.items()}
            if isinstance(v, list):
                return [rebase(x) for x in v]
            if isinstance(v, str):
                normalized = v.replace('\\', '/')
                if normalized == old_model:
                    return str(model)
                if normalized == before:
                    return str(run)
                if normalized.startswith(before+'/'):
                    return str(run/normalized[len(before)+1:])
            return v

        docs = {p: rebase(read(p)) for p in run.rglob('*.json') if p != marker}
        repin(run, docs)
        for p in run.rglob('*.jsonl'):
            rows = []
            for line in p.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    rows.append(json.dumps(rebase(json.loads(line)), ensure_ascii=False))
            p.write_text('\n'.join(rows)+'\n', encoding='utf-8')
        info.update(root=str(run), model=str(model))
        save(marker, info)
        print('Relocated '+run.name)
    print('JSON backup: '+str(backup))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    relocate(parser.parse_args().workspace)
