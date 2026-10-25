"""Install a pinned official native CLI in this workflow only (no npm required)."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

BASE = Path(__file__).resolve().parent
VERSION = '0.164.0'


def main():
    metadata_url = f'https://registry.npmjs.org/@openai/codex/{VERSION}-win32-x64'
    with urllib.request.urlopen(metadata_url, timeout=30) as response:
        metadata = json.load(response)
    dist = metadata['dist']
    if not dist['tarball'].startswith('https://registry.npmjs.org/@openai/codex/'):
        raise ValueError('Unexpected package host')
    with urllib.request.urlopen(dist['tarball'], timeout=120) as response:
        data = response.read()
    digest = 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()
    if digest != dist['integrity']:
        raise ValueError('Package integrity mismatch')
    dest = BASE/'vendor'/'codex-cli'/VERSION
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        archive.extractall(dest, filter='data')
    executable, = dest.rglob('codex.exe')
    lock = {'version':VERSION,'executable':str(executable.relative_to(BASE)),
            'metadata_url':metadata_url,'tarball':dist['tarball'],'integrity':digest}
    (BASE/'codex-cli.lock.json').write_text(json.dumps(lock,indent=2),encoding='utf-8')
    print(json.dumps({'version':VERSION,'executable':str(executable),'integrity_verified':True}))


if __name__ == '__main__':
    main()
