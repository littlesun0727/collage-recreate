"""Start the existing workbench with native editable projects on this machine."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import traceback
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(ROOT))
from restore_editor import restore, read, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=ROOT/'workspace')
    parser.add_argument('--port', type=int, default=8795)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--enable-chat', action='store_true')
    parser.add_argument('--enable-create', action='store_true')
    parser.add_argument('--sdk-path')
    parser.add_argument('--codex-path')
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    state_path = workspace/'.editor-state.json'
    identity = hashlib.sha256(str(workspace).encode()).hexdigest()
    suffix = '/?task=b85eec1d0ef5933e'
    if state_path.exists():
        previous = read(state_path)
        url = 'http://127.0.0.1:'+str(int(previous['port']))
        try:
            with urlopen(url+'/_editor/health', timeout=2) as response:
                health = json.load(response)
            if health.get('workspace') == identity and previous.get('workspace') == identity:
                if args.stop:
                    with urlopen(Request(url+'/_editor/stop', data=b'', headers={
                            'Origin': url, 'X-Editor-Token': previous['token']}), timeout=3):
                        pass
                elif not args.no_browser:
                    webbrowser.open(url+suffix)
                print(url+suffix, flush=True)
                return
        except OSError:
            pass
    if args.stop:
        return
    manifest, _ = restore(workspace)
    if args.sdk_path:
        os.environ['COLLAGE_CODEX_SDK'] = str(Path(args.sdk_path).resolve())
    if args.codex_path:
        os.environ['COLLAGE_CODEX_CLI'] = str(Path(args.codex_path).resolve())
    try:
        from workbench.server.http import create_server
        import numpy, cv2, onnxruntime, jsonschema, av  # Fail before opening a broken app.
    except ImportError as exc:
        raise RuntimeError('Missing editor dependency. On Linux run: bash demo/start-edit.sh') from exc
    token = secrets.token_urlsafe(32)
    kwargs = dict(chat_path=workspace/'chat.sqlite' if args.enable_chat else None,
                  enable_editor=True, enable_motion=True, enable_create=args.enable_create,
                  media_root=workspace/'service', cutout_model=workspace/'models'/manifest['model']['name'])
    port = args.port
    if port == 8795:
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                port = 0
    server = create_server([workspace/'tasks'], port, **kwargs)
    base_handler = server.RequestHandlerClass

    class Handler(base_handler):
        def local(self):
            hosts = {f'localhost:{server.server_port}', f'127.0.0.1:{server.server_port}'}
            return self.headers.get('Host') in hosts and (not self.headers.get('Origin') or urlsplit(self.headers['Origin']).netloc in hosts)

        def do_GET(self):
            if urlsplit(self.path).path == '/_editor/health':
                if not self.local():
                    return self.send(b'Forbidden', 'text/plain', 403)
                return self.send(json.dumps({'workspace': identity, 'editable': True}).encode(), 'application/json')
            return super().do_GET()

        def do_POST(self):
            if self.path == '/_editor/stop':
                if not self.local() or not secrets.compare_digest(self.headers.get('X-Editor-Token', ''), token):
                    return self.send(b'Forbidden', 'text/plain', 403)
                self.send(b'{"stopping":true}', 'application/json')
                threading.Thread(target=server.shutdown, daemon=True).start()
                return
            return super().do_POST()

    server.RequestHandlerClass = Handler
    url = f'http://127.0.0.1:{server.server_port}'+suffix
    save(state_path, {'port': server.server_port, 'workspace': identity, 'token': token})
    (ROOT/'editor-url.txt').write_text(url+'\n', encoding='utf-8')
    print('Editable workbench: '+url, flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if state_path.exists() and read(state_path).get('token') == token:
            state_path.unlink()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        (ROOT/'editor-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        if sys.stderr:
            traceback.print_exc()
        raise SystemExit(1)
