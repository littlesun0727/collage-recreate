"""Loopback endpoints with opaque images and an optional protected revision queue."""
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .store import Store

WEB = Path(__file__).resolve().parents[1]/'web'


def create_server(roots, port=8790, chat_path=None, agent=None):
    store = Store(roots)
    from .chat import ChatQueue, Conflict
    queue=ChatQueue(store,chat_path,agent=agent) if chat_path else None
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, body, mime, code=200, extra=None):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                return self.send(b'Invalid host', 'text/plain', 403)
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc not in allowed:
                return self.send(b'Invalid origin', 'text/plain', 403)
            path = urlsplit(self.path).path
            try:
                if path in {'/', '/app.js', '/chat.js', '/style.css'}:
                    name = 'index.html' if path == '/' else path[1:]
                    mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'chat.js':'text/javascript; charset=utf-8','style.css': 'text/css; charset=utf-8'}[name]
                    return self.send((WEB/name).read_bytes(), mime)
                parts = path.strip('/').split('/')
                if path == '/api/chat-config':
                    data={'enabled':bool(queue),'token':token if queue else None}
                elif path == '/api/tasks':
                    data = store.listing()
                elif len(parts) == 3 and parts[:2] == ['api', 'tasks']:
                    data = store.document(parts[2])
                elif len(parts)==4 and parts[:2]==['api','tasks'] and parts[3]=='chat':
                    if parts[2] not in store.tasks():raise KeyError('Task')
                    data=queue.public(parts[2]) if queue else {'enabled':False,'requests':[]}
                elif len(parts) == 5 and parts[:2] == ['api', 'tasks'] and parts[3] == 'images':
                    body, mime = store.artifact(parts[2], parts[4])
                    extra = {'Content-Disposition': 'attachment; filename="collage.png"'} if 'download=1' in self.path else {}
                    return self.send(body, mime, extra=extra)
                else:
                    raise KeyError('Unknown route')
                self.send(json.dumps(data, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
            except (KeyError, FileNotFoundError):
                self.send(b'{"error":"not_found"}', 'application/json', 404)
            except (OSError, ValueError, TypeError):
                self.send(b'{"error":"artifact_unavailable"}', 'application/json', 503)

        def do_POST(self):
            allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            origin=urlsplit(self.headers.get('Origin',''))
            if self.headers.get('Host') not in allowed or origin.scheme!='http' or origin.netloc not in allowed or self.headers.get('X-Chat-Token')!=token:
                return self.send(b'{"error":"Invalid origin or token"}','application/json',403)
            if not queue:return self.send(b'{"error":"Chat is disabled"}','application/json',403)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=20000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Invalid JSON request')
                payload=json.loads(self.rfile.read(length).decode('utf8'))
                if not isinstance(payload,dict):raise ValueError('Invalid request')
                parts=urlsplit(self.path).path.strip('/').split('/')
                if len(parts)==4 and parts[:2]==['api','tasks'] and parts[3]=='chat':result=queue.submit(parts[2],payload)
                elif len(parts)==6 and parts[:2]==['api','tasks'] and parts[3]=='chat' and parts[5]=='retry':result=queue.retry(parts[2],parts[4])
                else:raise KeyError('Unknown route')
                data={'id':result['id'],'status':result['status']};code=202
            except Conflict as exc:data={'error':str(exc)};code=409
            except (ValueError,TypeError) as exc:data={'error':str(exc)};code=400
            except KeyError:data={'error':'not_found'};code=404
            self.send(json.dumps(data,ensure_ascii=False).encode('utf8'),'application/json; charset=utf-8',code)

    class Server(ThreadingHTTPServer):
        def server_close(self):
            if queue:queue.close()
            super().server_close()
    server=Server(('127.0.0.1',port),Handler);server.chat=queue
    return server
