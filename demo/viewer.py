"""Portable read-only snapshot viewer; Python standard library only."""
import argparse
import hashlib
import json
import os
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def create_server(root, port=0, token=None):
    root=Path(root).resolve()
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    identity=hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()
    routes=manifest['routes'];token=token or secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send_json(self,value,code=200):
            body=json.dumps(value,ensure_ascii=False).encode('utf-8')
            self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            if self.command!='HEAD':self.wfile.write(body)
        def allowed(self):
            hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            return self.headers.get('Host') in hosts and (not self.headers.get('Origin') or urlsplit(self.headers['Origin']).netloc in hosts)
        def do_HEAD(self):self.do_GET()
        def do_GET(self):
            if not self.allowed():return self.send_json({'error':'Invalid host'},403)
            path=urlsplit(self.path).path
            if path=='/_health':return self.send_json({'package':identity,'readonly':True})
            record=routes.get(path)
            if not record:return self.send_json({'error':'Not in this snapshot'},404)
            file=(root/record['file']).resolve()
            if not file.is_relative_to(root) or not file.is_file():return self.send_json({'error':'Missing packaged file'},404)
            size=file.stat().st_size;start=0;end=size-1;status=200
            range_header=self.headers.get('Range')
            if range_header:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
                if not match or not any(match.groups()):return self.bad_range(size)
                left,right=match.groups()
                if left:start=int(left);end=min(int(right),size-1) if right else size-1
                else:start=max(0,size-int(right))
                if start>=size or end<start:return self.bad_range(size)
                status=206
            self.send_response(status)
            self.send_header('Content-Type',record['mime']);self.send_header('Content-Length',str(end-start+1))
            self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-cache')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            if 'download=1' in self.path:self.send_header('Content-Disposition','attachment; filename="collage-live.mp4"')
            self.end_headers()
            if self.command=='HEAD':return
            try:
                with file.open('rb') as stream:
                    stream.seek(start);remaining=end-start+1
                    while remaining:
                        block=stream.read(min(262144,remaining))
                        if not block:break
                        self.wfile.write(block);remaining-=len(block)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        def bad_range(self,size):
            self.send_response(416);self.send_header('Content-Range',f'bytes */{size}')
            self.send_header('Content-Length','0');self.end_headers()
        def do_POST(self):
            if not self.allowed():return self.send_json({'error':'Invalid host'},403)
            if self.path=='/_stop' and secrets.compare_digest(self.headers.get('X-Viewer-Token',''),token):
                self.send_json({'stopping':True});threading.Thread(target=self.server.shutdown,daemon=True).start()
            else:self.send_json({'error':'这是离线浏览包，不支持编辑或生成。'},405)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.package_identity=identity;server.stop_token=token
    return server


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8795)
    parser.add_argument('--no-browser',action='store_true');parser.add_argument('--stop',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parent;state_path=root/'.viewer-state.json'
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    suffix='/?task='+manifest.get('default_task','b85eec1d0ef5933e')
    previous=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else None
    if previous:
        url=f"http://127.0.0.1:{int(previous['port'])}"
        try:
            with urlopen(url+'/_health',timeout=2) as response:health=json.load(response)
            if health['package']==previous['package']:
                if args.stop:
                    with urlopen(Request(url+'/_stop',data=b'',headers={'X-Viewer-Token':previous['token']}),timeout=3):pass
                elif not args.no_browser:webbrowser.open(url+suffix)
                return
        except OSError:pass
    if args.stop:return
    try:
        server=create_server(root,args.port)
    except OSError:
        if args.port!=8795:raise
        server=create_server(root,0)
    state={'port':server.server_port,'pid':os.getpid(),'package':server.package_identity,'token':server.stop_token}
    state_path.write_text(json.dumps(state),encoding='utf-8')
    start_url=f'http://127.0.0.1:{server.server_port}'+suffix
    (root/'viewer-url.txt').write_text(start_url+'\n',encoding='utf-8')
    try:
        if not args.no_browser:threading.Timer(.5,lambda:webbrowser.open(start_url)).start()
        server.serve_forever()
    finally:
        server.server_close()
        if state_path.exists() and json.loads(state_path.read_text(encoding='utf-8')).get('pid')==os.getpid():state_path.unlink()


if __name__=='__main__':
    try:main()
    except Exception:
        import traceback
        (Path(__file__).resolve().parent/'viewer-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise
