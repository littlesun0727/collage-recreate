"""Loopback endpoints with opaque images and an optional protected revision queue."""
import json
import secrets
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .store import Store

WEB = Path(__file__).resolve().parents[1]/'web'


def create_server(roots, port=8790, chat_path=None, agent=None, enable_editor=False, enable_motion=False, media_root=None, cutout_model=None, enable_create=False, creator=None):
    store = Store(roots)
    from .chat import ChatQueue, Conflict
    queue=ChatQueue(store,chat_path,agent=agent) if chat_path else None
    from .editor import Editor
    editor=Editor(store) if enable_editor else None
    from .motion import Motion
    from .creation import Creation
    creator=(creator or Creation()) if enable_create else None
    if creator:creator.preflight()
    motion=Motion(store,media_root or Path(roots[0])/'.workbench/live',cutout_model,creator=creator) if enable_motion or enable_create else None
    token=secrets.token_urlsafe(32)
    mutation_guard=threading.RLock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, body, mime, code=200, extra=None):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def video(self, path):
            size=path.stat().st_size;start=0;end=size-1;code=200
            requested=self.headers.get('Range')
            if requested:
                match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
                if not match or not any(match.groups()):
                    return self.send(b'', 'video/mp4',416,{'Content-Range':f'bytes */{size}'})
                left,right=match.groups()
                if left:
                    start=int(left);end=min(int(right),size-1) if right else size-1
                else:
                    start=max(0,size-int(right))
                if start>=size or start>end:
                    return self.send(b'', 'video/mp4',416,{'Content-Range':f'bytes */{size}'})
                code=206
            self.send_response(code)
            self.send_header('Content-Type','video/quicktime' if path.suffix.lower()=='.mov' else 'video/mp4')
            self.send_header('Accept-Ranges','bytes');self.send_header('Content-Length',str(end-start+1))
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
            if code==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            if 'download=1' in self.path:self.send_header('Content-Disposition','attachment; filename="collage-live.mp4"')
            self.end_headers()
            try:
                with path.open('rb') as stream:
                    stream.seek(start);remaining=end-start+1
                    while remaining:
                        block=stream.read(min(256*1024,remaining))
                        if not block:break
                        self.wfile.write(block);remaining-=len(block)
            except (BrokenPipeError,ConnectionResetError):pass

        def do_GET(self):
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed:
                return self.send(b'Invalid host', 'text/plain', 403)
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc not in allowed:
                return self.send(b'Invalid origin', 'text/plain', 403)
            path = urlsplit(self.path).path
            try:
                if path in {'/', '/app.js', '/chat.js', '/editor.js', '/editor-crop.js', '/motion.js', '/style.css'}:
                    name = 'index.html' if path == '/' else path[1:]
                    mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8', 'motion.js':'text/javascript; charset=utf-8','editor.js':'text/javascript; charset=utf-8','editor-crop.js':'text/javascript; charset=utf-8','chat.js':'text/javascript; charset=utf-8','style.css': 'text/css; charset=utf-8'}[name]
                    return self.send((WEB/name).read_bytes(), mime)
                parts = path.strip('/').split('/')
                if path == '/api/chat-config':
                    data={'enabled':bool(queue),'editor_enabled':bool(editor),'motion_enabled':bool(motion),
                          'create_enabled':bool(creator),'token':token if queue or editor or motion else None}
                elif creator and len(parts)==4 and parts[:2]==['api','tasks'] and parts[3]=='creation':
                    if parts[2] not in store.tasks():raise KeyError('Task')
                    data=creator.public(motion,parts[2])
                elif motion and len(parts)==4 and parts[:3]==['api','uploads','jobs']:
                    data=motion.job(parts[3])
                elif motion and len(parts)==4 and parts[:2]==['api','tasks'] and parts[3] in ['media','motion']:
                    data=motion.document(parts[2])
                elif motion and len(parts)==6 and parts[:2]==['api','tasks'] and parts[3:5]==['motion','jobs']:
                    data=motion.job(parts[5],task=parts[2])
                elif motion and len(parts)==6 and parts[:2]==['api','tasks'] and parts[3]=='media' and parts[5]=='content':
                    return self.video(motion.content(parts[2],'media',parts[4]))
                elif motion and len(parts)==7 and parts[:2]==['api','tasks'] and parts[3:5]==['motion','versions'] and parts[6]=='content':
                    return self.video(motion.content(parts[2],'motion',parts[5]))
                elif path == '/api/tasks':
                    data = store.listing()
                elif len(parts) == 3 and parts[:2] == ['api', 'tasks']:
                    data = store.document(parts[2])
                elif len(parts)==4 and parts[:2]==['api','tasks'] and parts[3]=='chat':
                    if parts[2] not in store.tasks():raise KeyError('Task')
                    data=queue.public(parts[2]) if queue else {'enabled':False,'requests':[]}
                elif editor and len(parts)==4 and parts[:2]==['api','tasks'] and parts[3]=='editor':
                    data=editor.document(parts[2])
                elif editor and len(parts)==6 and parts[:2]==['api','tasks'] and parts[3:5]==['editor','jobs']:
                    data=editor.job(parts[2],parts[5])
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
            mutation_guard.acquire()
            try:
                parts=urlsplit(self.path).path.strip('/').split('/')
                if motion and len(parts)==4 and parts[:2]==['api','uploads'] and parts[3]=='files':
                    result=motion.upload(parts[2],self.headers,self.rfile)
                    return self.send(json.dumps(result,ensure_ascii=False).encode('utf8'),'application/json; charset=utf-8',201)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=200000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Invalid JSON request')
                payload=json.loads(self.rfile.read(length).decode('utf8'))
                if not isinstance(payload,dict):raise ValueError('Invalid request')
                parts=urlsplit(self.path).path.strip('/').split('/')
                if creator and len(parts)>=4 and parts[:2]==['api','tasks'] and parts[3] in {'chat','editor','motion'}:
                    current=creator.find(motion,parts[2])
                    if current and current['status'] in {'queued','running'}:
                        raise Conflict('首版正在制作，请完成后再修改或制作动态成片')
                if motion and parts==['api','uploads']:
                    result=motion.create_batch()
                elif motion and len(parts)==4 and parts[:2]==['api','uploads'] and parts[3]=='prepare':
                    result=motion.submit_import(parts[2],payload)
                elif creator and len(parts)==5 and parts[:2]==['api','tasks'] and parts[3:]==['creation','retry']:
                    if payload:raise ValueError('继续制作不接受额外参数')
                    if queue and any(j['status'] in {'queued','running'} for j in queue.rows(parts[2])):
                        raise Conflict('对话修改正在执行，请稍后继续')
                    if editor and any(task==parts[2] for task,identifier in editor.active):
                        raise Conflict('画布编辑正在执行，请稍后继续')
                    if any(j['status'] in {'queued','running'} for j in motion.document(parts[2])['jobs']):
                        raise Conflict('动态成片正在执行，请稍后继续')
                    result=creator.retry(motion,parts[2])
                elif motion and len(parts)==5 and parts[:2]==['api','tasks'] and parts[3:]==['motion','render']:
                    result=motion.submit(parts[2],payload)
                elif len(parts)==5 and parts[:2]==['api','tasks'] and parts[3]=='editor' and parts[4] in ['preview','save']:
                    if not editor:return self.send(b'{"error":"Editor is disabled"}','application/json',403)
                    result=editor.submit(parts[2],parts[4],payload)
                elif len(parts)==4 and parts[:2]==['api','tasks'] and parts[3]=='chat':
                    if not queue:return self.send(b'{"error":"Chat is disabled"}','application/json',403)
                    result=queue.submit(parts[2],payload)
                elif len(parts)==6 and parts[:2]==['api','tasks'] and parts[3]=='chat' and parts[5]=='retry':
                    if not queue:return self.send(b'{"error":"Chat is disabled"}','application/json',403)
                    result=queue.retry(parts[2],parts[4])
                else:raise KeyError('Unknown route')
                data={'id':result['id'],'status':result['status']};code=202
            except Conflict as exc:data={'error':str(exc)};code=409
            except (ValueError,TypeError) as exc:data={'error':str(exc)};code=400
            except KeyError:data={'error':'not_found'};code=404
            finally:mutation_guard.release()
            self.send(json.dumps(data,ensure_ascii=False).encode('utf8'),'application/json; charset=utf-8',code)

    class Server(ThreadingHTTPServer):
        def server_close(self):
            if motion:motion.close()
            if editor:editor.close()
            if queue:queue.close()
            super().server_close()
    server=Server(('127.0.0.1',port),Handler);server.chat=queue;server.motion=motion
    return server
