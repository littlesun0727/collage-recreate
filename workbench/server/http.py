"""Loopback-only read-only endpoints with opaque registered image identifiers."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .store import Store

WEB = Path(__file__).resolve().parents[1]/'web'


def create_server(roots, port=8790):
    store = Store(roots)
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
                if path in {'/', '/app.js', '/style.css'}:
                    name = 'index.html' if path == '/' else path[1:]
                    mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'style.css': 'text/css; charset=utf-8'}[name]
                    return self.send((WEB/name).read_bytes(), mime)
                parts = path.strip('/').split('/')
                if path == '/api/tasks':
                    data = store.listing()
                elif len(parts) == 3 and parts[:2] == ['api', 'tasks']:
                    data = store.document(parts[2])
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

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)
