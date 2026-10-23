"""Start the local service hidden, without changing PowerShell execution policy."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen

sys.dont_write_bytecode = True
from collage.paths import RUNTIME_ROOT


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8809)
    parser.add_argument('--data', type=Path, default=RUNTIME_ROOT)
    args = parser.parse_args()
    base = f'http://127.0.0.1:{args.port}'
    def ready():
        try:
            with urlopen(base+'/api/chat-config', timeout=2) as response:
                return json.load(response)
        except OSError:
            return None
    existing = ready()
    if existing:
        if existing.get('controller') != 'workflow-v9':
            raise ValueError('Port is occupied by another application')
        print('Workbench ready: '+base)
        return
    data = args.data.resolve(); (data/'tasks').mkdir(parents=True, exist_ok=True)
    runs = [str(data/'tasks')]
    registry = data/'task-roots.json'
    if registry.is_file():
        runs += [str(Path(p).resolve()) for p in json.loads(registry.read_text(encoding='utf-8'))
                 if Path(p).is_dir() and str(Path(p).resolve()) not in runs]
    command = [sys.executable, '-B', '-m', 'workbench', '--runs', *runs, '--port', str(args.port),
               '--enable-create', '--enable-chat', '--enable-editor', '--enable-motion',
               '--media-root', str(data/'service'), '--chat-state', str(data/'chat.sqlite')]
    model = Path(os.getenv('COLLAGE_CUTOUT_MODEL', 'D:/codes/visual-recreate-validation/models/birefnet-lite-fp32.onnx'))
    if model.is_file():
        command += ['--cutout-model', str(model)]
    with (data/'server-out.log').open('ab') as stdout, (data/'server-error.log').open('ab') as stderr:
        child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    (data/'server.pid').write_text(str(child.pid), encoding='ascii')
    for _ in range(40):
        if child.poll() is not None:
            raise RuntimeError('Service failed; inspect '+str(data/'server-error.log'))
        if ready():
            print('Workbench ready: '+base)
            return
        time.sleep(.5)
    raise RuntimeError('Startup pending; inspect service logs')


if __name__ == '__main__':
    main()
