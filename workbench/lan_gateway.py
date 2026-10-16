"""Authenticated HTTPS access to the loopback workbench from one LAN subnet."""
import argparse
import asyncio
from collections import deque
import hashlib
import hmac
import html
import ipaddress
import json
import logging
from pathlib import Path
import re
import secrets
import ssl
import time
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web

COOKIE = '__Host-collage-session'
LOGIN_COOKIE = '__Host-collage-login'
SESSION_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 60 * 60
ITERATIONS = 600_000
STATIC = {'/', '/app.js', '/chat.js', '/editor.js', '/editor-crop.js', '/motion.js', '/style.css', '/reveal-architecture.png', '/reveal-data.png'}
API = re.compile(r'/api/(?:chat-config|tasks(?:/[0-9a-f]{16}(?:/[A-Za-z0-9_-]+)*)?|uploads(?:/[A-Za-z0-9_-]+)*)\Z')
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer',
       'transfer-encoding', 'upgrade', 'host', 'cookie', 'authorization', 'forwarded'}
STYLE = '''body{margin:0;background:#f3f5f0;color:#20382c;font:16px system-ui,sans-serif}
main{max-width:380px;margin:10vh auto;padding:30px;background:white;border:1px solid #dce4dc;border-radius:16px}
h1{font-size:25px;margin:0 0 12px}p{color:#657269;line-height:1.6}label{display:block;margin-top:20px}
input,button{box-sizing:border-box;width:100%;padding:13px;margin-top:8px;border:1px solid #ccd8cc;border-radius:8px;font:inherit}
button{background:#27543d;color:white;cursor:pointer;margin-top:24px}a{color:#27543d}.error{color:#a52a2a}
@media(max-width:480px){main{margin:8vh 16px;padding:24px}}'''


def password_record(password):
    salt = secrets.token_bytes(16)
    return {'salt': salt.hex(), 'iterations': ITERATIONS,
            'hash': hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS).hex()}


def verify_password(password, record):
    candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(record['salt']), record['iterations'])
    return hmac.compare_digest(candidate.hex(), record['hash'])


def page(content, status=200):
    return web.Response(text='<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width,initial-scale=1">'
                        '<title>拼贴工作台 · 登录</title><style>'+STYLE+'</style><main>'+content+'</main></html>',
                        content_type='text/html', status=status)


def login_page(error='', status=200, csrf=''):
    return page('<h1>拼贴工作台</h1><p>办公室专用入口，请使用分配给你的账号登录。</p>'
                + ('<p class="error">'+html.escape(error)+'</p>' if error else '')
                + '<form method="post" action="/_lan/login">'
                '<input type="hidden" name="csrf" value="'+html.escape(csrf)+'">'
                '<label>账号<input name="username" autocomplete="username" maxlength="64" required></label>'
                '<label>密码<input name="password" type="password" autocomplete="current-password" maxlength="256" required></label>'
                '<button type="submit">登录工作台</button></form>') if status == 200 else page(
                    '<h1>暂时无法登录</h1><p>'+html.escape(error)+'</p><a href="/_lan/login">返回登录</a>',status)


def create_app(config, upstream='http://127.0.0.1:8795'):
    # The upstream is fixed by the operator, never by a request or forwarded header.
    network = ipaddress.ip_network(config['network'])
    authority = f"{config['bind']}:{config['port']}"
    origin = 'https://'+authority
    if 'public_origin' in config:
        # A separate loopback listener receives authenticated tunnel traffic.
        # Never derive the accepted browser origin from forwarded headers.
        origin = config['public_origin']
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment
                or origin != 'https://' + parsed.netloc
                or not ipaddress.ip_address(config['bind']).is_loopback
                or not network.is_loopback):
            raise ValueError('public_origin requires an exact HTTPS origin and loopback-only binding/network')
        authority = parsed.netloc
    sessions = {}
    attempts = {}
    password_slots = asyncio.Semaphore(2)
    dummy = password_record(secrets.token_urlsafe(32))
    login_secret = secrets.token_bytes(32)

    def issue_login(error='', status=200):
        payload = str(int(time.time()))+'.'+secrets.token_urlsafe(32)
        csrf = payload+'.'+hmac.new(login_secret,payload.encode(),hashlib.sha256).hexdigest()
        response = login_page(error,status,csrf)
        response.set_cookie(LOGIN_COOKIE,csrf,secure=True,httponly=True,samesite='Strict',path='/',max_age=1800)
        return response

    def secure_login_error():
        response = login_page('登录页面已过期或来源无法验证，请返回登录页刷新后重试。',403)
        secure_headers(response)
        return response

    async def valid_login_form(request):
        # Some mobile browsers omit Origin on native form navigation. Accept only
        # our short-lived signed form token, bound to a host-only secure cookie.
        if (request.path != '/_lan/login' or request.content_type != 'application/x-www-form-urlencoded'
                or not request.content_length or request.content_length > 4096):
            request['login_csrf_status']='invalid-form-type-or-size'
            return False
        csrf = (await request.post()).get('csrf','')
        cookie = request.cookies.get(LOGIN_COOKIE,'')
        if (not isinstance(csrf,str) or not re.fullmatch(r'[0-9]{10,12}\.[A-Za-z0-9_-]{43}\.[a-f0-9]{64}',csrf)
                or not hmac.compare_digest(csrf.encode(),cookie.encode())):
            request['login_csrf_status']='missing-token' if not csrf else 'missing-cookie' if not cookie else 'token-cookie-mismatch'
            return False
        try:
            timestamp, nonce, signature = csrf.split('.')
            age = time.time()-int(timestamp)
        except ValueError:
            request['login_csrf_status']='invalid-token'
            return False
        expected = hmac.new(login_secret,(timestamp+'.'+nonce).encode(),hashlib.sha256).hexdigest()
        valid=0 <= age <= 1800 and hmac.compare_digest(signature,expected)
        request['login_csrf_status']='valid' if valid else 'expired-or-previous-gateway'
        return valid

    def audit_login(request,response):
        # Never log passwords, form values, cookie values, query strings or IPs.
        if request.path not in {'/','/_lan/login','/_lan/account','/_lan/logout'}:
            return
        origins=[]
        for value in request.headers.getall('Origin',[]):
            try:
                parsed=urlsplit(value)
                origins.append((parsed.scheme+'://'+(parsed.hostname or ''))[:160] if parsed.scheme else value[:10])
            except ValueError:
                origins.append('malformed')
        logging.getLogger('workbench.login').warning(json.dumps({
            'event':'login-diagnostic','method':request.method,'path':request.path,'status':response.status,
            'edge_scheme':request.headers.get('X-Forwarded-Proto','')[:12],
            'origin':origins,'fetch_site':request.headers.get('Sec-Fetch-Site','')[:24],
            'login_cookie':bool(request.cookies.get(LOGIN_COOKIE)),
            'session_cookie':bool(request.cookies.get(COOKIE)),
            'session_known':request.cookies.get(COOKIE,'') in sessions,
            'csrf':request.get('login_csrf_status','origin-checked'),
            'browser':request.headers.get('User-Agent','')[:240]},ensure_ascii=True))

    def session(request):
        token = request.cookies.get(COOKIE, '')
        entry = sessions.get(token)
        now = time.monotonic()
        if entry and now < entry['expires'] and now-entry['last'] < IDLE_SECONDS:
            entry['last'] = now
            return entry
        sessions.pop(token, None)
        return None

    @web.middleware
    async def protect(request, handler):
        try:
            try:
                peer = ipaddress.ip_address(request.remote or '')
                allowed = peer.is_loopback or peer in network
            except ValueError:
                allowed = False
            if not allowed:
                raise web.HTTPForbidden(text='Access is restricted to the office network.')
            if request.host != authority:
                raise web.HTTPForbidden(text='Invalid host')
            if request.method not in {'GET', 'HEAD', 'POST'}:
                raise web.HTTPMethodNotAllowed(request.method, ['GET', 'HEAD', 'POST'])
            # The tunnel uses HTTP on loopback even for HTTPS visitors. Cloudflare
            # overwrites X-Forwarded-Proto with the visitor's actual protocol.
            # Only use it to redirect to the configured authority, never to grant access.
            if 'public_origin' in config and request.headers.get('X-Forwarded-Proto')=='http':
                if request.method=='POST':
                    raise web.HTTPSeeOther(origin+'/_lan/login')
                raise web.HTTPFound(origin+request.rel_url.path_qs)
            if request.method == 'POST' and request.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise web.HTTPForbidden(text='Cross-site requests are not permitted')
            if request.method == 'POST' and request.headers.get('Origin') != origin:
                browser_origin = request.headers.get('Origin')
                if browser_origin not in {None,'null'} or not await valid_login_form(request):
                    if request.path=='/_lan/login':
                        response=secure_login_error()
                        audit_login(request,response)
                        return response
                    raise web.HTTPForbidden(text='Invalid origin')
            if request.path != '/_lan/login' and not session(request):
                if request.path.startswith('/api/'):
                    response = web.json_response({'error':'login_required'},status=401)
                else:
                    response = web.HTTPSeeOther('/_lan/login')
            else:
                response = await handler(request)
        except web.HTTPException as exc:
            response = exc
        if isinstance(response,web.HTTPException):
            normal = web.Response(status=response.status,body=response.body,headers=response.headers)
            normal.cookies.update(response.cookies)
            response = normal
        if not response.prepared:
            secure_headers(response)
        audit_login(request,response)
        return response

    async def login(request):
        if request.method in {'GET', 'HEAD'}:
            return web.HTTPSeeOther('/') if session(request) else issue_login()
        if request.content_type != 'application/x-www-form-urlencoded' or not request.content_length or request.content_length > 4096:
            raise web.HTTPBadRequest(text='Invalid login request')
        now = time.monotonic()
        for key in list(attempts):
            if not attempts[key] or now-attempts[key][-1] > 300:
                del attempts[key]
        history = attempts.setdefault(request.remote, deque())
        while history and now-history[0] > 300:
            history.popleft()
        if len(history) >= 5 or len(attempts) > 1024:
            response = issue_login('登录尝试过多，请五分钟后重试。',429)
            response.headers['Retry-After'] = '300'
            return response
        history.append(now)
        form = await request.post()
        username, password = form.get('username',''), form.get('password','')
        if not isinstance(username,str) or not isinstance(password,str) or len(username)>64 or len(password)>256:
            raise web.HTTPBadRequest(text='Invalid login request')
        record = config['users'].get(username)
        async with password_slots:
            valid = await asyncio.to_thread(verify_password,password,record or dummy)
        if not valid or record is None:
            return issue_login('账号或密码不正确。')
        attempts.pop(request.remote,None)
        sessions.pop(request.cookies.get(COOKIE,''),None)
        for key in list(sessions):
            item = sessions[key]
            if now >= item['expires'] or now-item['last'] >= IDLE_SECONDS:
                del sessions[key]
        if len(sessions) >= 256:
            raise web.HTTPServiceUnavailable(text='Too many active sessions')
        token = secrets.token_urlsafe(32)
        sessions[token] = {'username':username,'expires':now+SESSION_SECONDS,'last':now}
        response = web.HTTPSeeOther('/')
        response.set_cookie(COOKIE,token,secure=True,httponly=True,samesite='Strict',path='/',max_age=SESSION_SECONDS)
        return response

    async def account(request):
        entry = session(request)
        return page('<h1>当前账号：'+html.escape(entry['username'])+'</h1>'
                    '<p>此工作台的任务和素材由已授权的同事共享。</p><a href="/">返回工作台</a>'
                    '<form method="post" action="/_lan/logout"><button>退出登录</button></form>')

    async def logout(request):
        sessions.pop(request.cookies.get(COOKIE,''),None)
        response = web.HTTPSeeOther('/_lan/login')
        response.del_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='Strict')
        return response

    async def proxy(request):
        if request.path not in STATIC and not API.fullmatch(request.path):
            raise web.HTTPNotFound()
        if request.method == 'POST':
            if request.path in STATIC:
                raise web.HTTPMethodNotAllowed('POST',['GET','HEAD'])
            limit = 128*1024*1024 if re.fullmatch(r'/api/uploads/batch-[a-f0-9]{32}/files',request.path) else 200000
            if request.content_length is None:
                raise web.HTTPLengthRequired()
            if not 0 < request.content_length <= limit:
                raise web.HTTPRequestEntityTooLarge(max_size=limit,actual_size=request.content_length)
        excluded = HOP | {'origin','referer'} | {s.strip().lower() for s in request.headers.get('Connection','').split(',')}
        headers = {k:v for k,v in request.headers.items() if k.lower() not in excluded
                   and not k.lower().startswith(('x-forwarded-', 'cf-'))}
        headers['Host'] = upstream.split('://',1)[1]
        if request.headers.get('Origin') or request.method == 'POST':
            headers['Origin'] = upstream
        body = request.content.iter_chunked(256*1024) if request.method == 'POST' else None
        try:
            async with request.app[CLIENT].request(request.method,upstream+request.raw_path,headers=headers,
                    data=body,allow_redirects=False) as backend:
                response_headers = {k:v for k,v in backend.headers.items()
                    if k.lower() not in HOP | {'set-cookie','content-length'}}
                if request.path == '/' and request.method == 'GET' and backend.status==200:
                    document = await backend.text()
                    link = '<a href="/_lan/account" style="position:fixed;right:12px;bottom:12px;z-index:9999;background:white;padding:8px 12px;border:1px solid #ccd8cc;border-radius:8px;color:#27543d;font:13px system-ui">账号 / 退出</a>'
                    return web.Response(body=document.replace('</body>',link+'</body>').encode(),
                                        status=backend.status,headers=response_headers)
                if backend.content_length is not None:
                    response_headers['Content-Length'] = str(backend.content_length)
                response = web.StreamResponse(status=backend.status,headers=response_headers)
                secure_headers(response)
                await response.prepare(request)
                async for chunk in backend.content.iter_chunked(256*1024):
                    await response.write(chunk)
                await response.write_eof()
                return response
        except (OSError, asyncio.TimeoutError):
            raise web.HTTPBadGateway(text='工作台暂时无法连接，请稍后重试。')

    async def client_context(app):
        async with ClientSession(timeout=ClientTimeout(total=300,connect=10),
                connector=TCPConnector(limit=32),auto_decompress=False,trust_env=False) as client:
            app[CLIENT] = client
            yield

    app = web.Application(middlewares=[protect],client_max_size=128*1024*1024)
    app.cleanup_ctx.append(client_context)
    app.router.add_route('*','/_lan/login',login)
    app.router.add_get('/_lan/account',account)
    app.router.add_post('/_lan/logout',logout)
    app.router.add_route('*','/{tail:.*}',proxy)
    return app


CLIENT = web.AppKey('upstream_client',ClientSession)


def secure_headers(response):
    # Native POST forms need their same-origin Origin header for CSRF checks.
    # no-referrer makes browsers send Origin: null for these navigations.
    response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
        'Permissions-Policy':'camera=(), microphone=(), geolocation=()'})
    response.headers.setdefault('Content-Security-Policy',
        "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; media-src 'self' blob:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args()
    config=json.loads(args.config.read_text(encoding='utf-8'))
    app=create_app(config)
    tls=None
    if 'public_origin' not in config:
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version=ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(config['certificate'],config['private_key'])
    display_origin=config.get('public_origin', f"https://{config['bind']}:{config['port']}")
    print(f"Workbench gateway: {display_origin}",flush=True)
    web.run_app(app,host=config['bind'],port=config['port'],ssl_context=tls,access_log=None)


if __name__=='__main__':
    main()
