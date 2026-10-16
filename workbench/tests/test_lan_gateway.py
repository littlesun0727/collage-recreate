"""Exercise the LAN authentication boundary and streaming against an offline backend."""
import asyncio
import hashlib
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from workbench.lan_gateway import COOKIE, LOGIN_COOKIE, create_app, password_record

HOST='127.0.0.1:8443'
ORIGIN='https://'+HOST
PASSWORD='fixture-password-for-tests'


def test_peer_network_cannot_be_spoofed_with_forwarded_headers():
    from unittest.mock import Mock
    from aiohttp.test_utils import make_mocked_request
    async def check():
        config={'bind':'7.249.217.32','port':8443,'network':'7.249.217.0/24','users':{}}
        app=create_app(config)
        transport=Mock()
        transport.get_extra_info.side_effect=lambda name,default=None: ('203.0.113.8',12345) if name=='peername' else default
        request=make_mocked_request('GET','/_lan/login',headers={'Host':'7.249.217.32:8443','X-Forwarded-For':'7.249.217.10'},transport=transport)
        async def forbidden(request):raise AssertionError('Outside-office request reached handler')
        response=await app.middlewares[0](request,forbidden)
        assert response.status==403
    asyncio.run(check())


async def exercise(callback, extra_config=None):
    seen=[]
    async def upstream(request):
        body=await request.read()
        seen.append({'path':request.path,'headers':dict(request.headers),'body':body,'origins':request.headers.getall('Origin',[])})
        if request.path.endswith('/content'):
            return web.Response(body=b'frame-data',status=206,headers={'Content-Range':'bytes 0-9/100'})
        return web.json_response({'ok':True,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()})
    backend_app=web.Application(client_max_size=4*1024*1024)
    backend_app.router.add_route('*','/{tail:.*}',upstream)
    async with TestServer(backend_app) as backend:
        config={'bind':'127.0.0.1','port':8443,'network':'127.0.0.0/8','users':{'boss':password_record(PASSWORD)}}
        config.update(extra_config or {})
        async with TestClient(TestServer(create_app(config,str(backend.make_url('')).rstrip('/')))) as client:
            await callback(client,seen,backend)


async def login(client):
    response=await client.post('/_lan/login',headers={'Host':HOST,'Origin':ORIGIN},
                              data={'username':'boss','password':PASSWORD},allow_redirects=False)
    assert response.status==303
    cookie=response.cookies[COOKIE]
    assert cookie['secure'] and cookie['httponly'] and cookie['samesite']=='Strict'
    return {'Host':HOST,'Cookie':f'{COOKIE}={cookie.value}'}


def test_every_resource_requires_login_and_admin_routes_are_hidden():
    async def check(client,seen,backend):
        for route in ['/api/tasks','/api/chat-config','/api/tasks/0123456789abcdef/images/anything']:
            response=await client.get(route,headers={'Host':HOST},allow_redirects=False)
            assert response.status==401
            assert (await response.json())['error']=='login_required'
        for path in ['/','/reveal-architecture.png','/reveal-data.png']:
            response=await client.get(path,headers={'Host':HOST},allow_redirects=False)
            assert response.status==303 and response.headers['Location']=='/_lan/login'
        assert not seen
        headers=await login(client)
        response=await client.post('/_editor/stop',headers={**headers,'Origin':ORIGIN},data=b'{}')
        assert response.status==404 and not seen
        response=await client.get('/api/tasks',headers={**headers,'Host':'evil.example'})
        assert response.status==403 and not seen
    asyncio.run(exercise(check))


def test_tunnel_origin_native_login_and_csrf_checks():
    public_host='workbench.example.test'
    public_origin='https://'+public_host
    async def check(client,seen,backend):
        response=await client.get('/_lan/login',headers={'Host':public_host})
        assert response.status==200
        assert response.headers['Referrer-Policy']=='same-origin'
        response=await client.get('/api/tasks',headers={
            'Host':public_host,'Cf-Access-Authenticated-User-Email':'boss@example.test',
            'Cf-Access-Jwt-Assertion':'unverified-token'})
        assert response.status==401 and not seen
        for wrong_origin in ('null', ORIGIN, 'https://evil.example'):
            response=await client.post('/_lan/login',headers={'Host':public_host,'Origin':wrong_origin},
                                      data={'username':'boss','password':PASSWORD})
            assert response.status==403
        response=await client.post('/_lan/login',headers={'Host':public_host,'Origin':public_origin},
                                  data={'username':'boss','password':PASSWORD},allow_redirects=False)
        assert response.status==303
        cookie=response.cookies[COOKIE]
        assert cookie['secure'] and cookie['httponly']
        response=await client.get('/api/tasks',headers={
            'Host':public_host,'Cookie':f'{COOKIE}={cookie.value}',
            'Cf-Access-Jwt-Assertion':'private-token','Cf-Connecting-IP':'203.0.113.5'})
        assert response.status==200
        assert not any(name.lower().startswith('cf-') for name in seen[-1]['headers'])
        response=await client.get('/_lan/login',headers={'Host':HOST,'X-Forwarded-Host':public_host})
        assert response.status==403
    asyncio.run(exercise(check,{'public_origin':public_origin}))


def test_tunnel_listener_rejects_non_loopback_or_ambiguous_origin():
    import pytest
    config={'bind':'127.0.0.1','port':8796,'network':'127.0.0.0/8',
            'public_origin':'https://workbench.example.test','users':{}}
    for change in ({'bind':'0.0.0.0'}, {'network':'0.0.0.0/0'},
                   {'public_origin':'http://workbench.example.test'},
                   {'public_origin':'https://workbench.example.test/'},
                   {'public_origin':'https://user:password@workbench.example.test'},
                   {'public_origin':'https://workbench.example.test?extra=1'}):
        with pytest.raises(ValueError):
            create_app({**config,**change})


def test_login_csrf_rate_limit_and_logout():
    async def check(client,seen,backend):
        response=await client.get('/_lan/login',headers={'Host':HOST})
        assert response.status==200
        assert response.headers['Referrer-Policy']=='same-origin'
        for extra in ({}, {'Origin':'null'}, {'Origin':'https://evil.example'},
                      {'Origin':ORIGIN,'Sec-Fetch-Site':'cross-site'}):
            response=await client.post('/_lan/login',headers={'Host':HOST,**extra},
                                      data={'username':'boss','password':PASSWORD})
            assert response.status==403 and COOKIE not in response.cookies
        headers=await login(client)
        response=await client.get('/_lan/account',headers=headers)
        assert response.status==200 and 'boss' in await response.text()
        assert response.headers['Referrer-Policy']=='same-origin'
        response=await client.post('/_lan/logout',headers={**headers,'Origin':ORIGIN},allow_redirects=False)
        assert response.status==303
        response=await client.get('/api/tasks',headers=headers)
        assert response.status==401
        for _ in range(5):
            response=await client.post('/_lan/login',headers={'Host':HOST,'Origin':ORIGIN},
                                      data={'username':'boss','password':'incorrect'})
            assert response.status==200 and not response.cookies.get(COOKIE)
        response=await client.post('/_lan/login',headers={'Host':HOST,'Origin':ORIGIN},
                                  data={'username':'boss','password':PASSWORD})
        assert response.status==429 and response.headers['Retry-After']=='300'
        assert not seen
    asyncio.run(exercise(check))


def test_proxy_enforces_origin_strips_credentials_and_preserves_large_upload_and_range():
    async def check(client,seen,backend):
        headers=await login(client)
        for extra in ({},{'Origin':'https://evil.example'},{'Origin':ORIGIN,'Sec-Fetch-Site':'cross-site'}):
            response=await client.post('/api/uploads',headers={**headers,**extra},json={})
            assert response.status==403
        assert not seen
        payload=b'x'*2*1024*1024
        response=await client.post('/api/uploads/batch-'+'a'*32+'/files',
            headers={**headers,'Origin':ORIGIN,'Content-Type':'application/octet-stream','X-Chat-Token':'upstream-token',
                     'X-File-Name':'video.mp4','X-Media-Role':'material','Authorization':'do-not-forward',
                     'X-Forwarded-For':'8.8.8.8'},data=payload)
        assert response.status==200
        result=await response.json()
        assert result['bytes']==len(payload) and result['sha256']==hashlib.sha256(payload).hexdigest()
        forwarded=seen[-1]['headers']
        assert 'Cookie' not in forwarded and 'Authorization' not in forwarded and 'X-Forwarded-For' not in forwarded
        assert forwarded['X-Chat-Token']=='upstream-token'
        assert forwarded['Origin']==str(backend.make_url('')).rstrip('/')
        response=await client.get('/api/tasks/0123456789abcdef/media/video/content',headers={**headers,'Range':'bytes=0-9'})
        assert response.status==206 and await response.read()==b'frame-data'
        assert response.headers['Content-Range']=='bytes 0-9/100'
        assert seen[-1]['headers']['Range']=='bytes=0-9'
    asyncio.run(exercise(check))


def test_mobile_login_with_signed_form_and_missing_or_null_origin():
    import re
    import time
    from unittest.mock import patch
    async def check(client,seen,backend):
        for origin in (None,'null'):
            response=await client.get('/_lan/login',headers={'Host':HOST})
            token=re.search(r'name="csrf" value="([^"]+)"',await response.text())[1]
            cookie=response.cookies[LOGIN_COOKIE]
            assert cookie['secure'] and cookie['httponly'] and cookie['samesite']=='Strict'
            headers={'Host':HOST,'Cookie':f'{LOGIN_COOKIE}={cookie.value}'}
            if origin:headers['Origin']=origin
            form={'username':'boss','password':PASSWORD,'csrf':token}
            tampered=token[:-1]+('0' if token[-1]!='0' else '1')
            for bad_headers,bad_form in [
                ({**headers,'Cookie':''},form),
                (headers,{**form,'csrf':token[:-1]+('0' if token[-1]!='0' else '1')}),
                ({**headers,'Origin':'https://evil.example'},form),
                ({**headers,'Sec-Fetch-Site':'cross-site'},form),
                (headers,{**form,'csrf':'无效令牌'}),
                ({**headers,'Cookie':f'{LOGIN_COOKIE}={tampered}'},{**form,'csrf':tampered}),
            ]:
                response=await client.post('/_lan/login',headers=bad_headers,data=bad_form,allow_redirects=False)
                assert response.status==403 and COOKIE not in response.cookies
            with patch('workbench.lan_gateway.time.time',return_value=time.time()+1900):
                response=await client.post('/_lan/login',headers=headers,data=form,allow_redirects=False)
                assert response.status==403
            response=await client.post('/_lan/login',headers=headers,data=form,allow_redirects=False)
            assert response.status==303 and COOKIE in response.cookies
            # This compatibility path must never admit an Origin-less API mutation.
            response=await client.post('/api/uploads',headers={**headers,'Cookie':f'{COOKIE}={response.cookies[COOKIE].value}; {LOGIN_COOKIE}={cookie.value}'},data=form)
            assert response.status==403
        assert not seen
    asyncio.run(exercise(check))


def test_proxy_replaces_lowercase_origin_without_duplicate_headers():
    async def check(client,seen,backend):
        headers=await login(client)
        response=await client.post('/api/uploads',headers={**headers,'origin':ORIGIN,'referer':ORIGIN+'/'},json={})
        assert response.status==200
        assert seen[-1]['origins']==[str(backend.make_url('')).rstrip('/')]
        assert not any(k.lower()=='referer' for k in seen[-1]['headers'])
    asyncio.run(exercise(check))


def test_public_http_redirects_before_showing_login_or_processing_passwords():
    public='https://workbench.example.test'
    async def check(client,seen,backend):
        headers={'Host':'workbench.example.test','X-Forwarded-Proto':'http'}
        for path in ['/','/_lan/login?check=phone','/_lan/login?next=https://evil.example/path']:
            response=await client.get(path,headers=headers,allow_redirects=False)
            assert response.status==302 and response.headers['Location'].startswith(public+'/')
            assert not response.cookies and 'name="password"' not in await response.text()
        for _ in range(7):
            response=await client.post('/_lan/login',headers={**headers,'Origin':'http://workbench.example.test'},
                data={'username':'boss','password':PASSWORD},allow_redirects=False)
            assert response.status==303 and response.headers['Location']==public+'/_lan/login'
            assert not response.cookies
        response=await client.post('/_lan/login',headers={**headers,'X-Forwarded-Proto':'https','Origin':public},
            data={'username':'boss','password':PASSWORD},allow_redirects=False)
        assert response.status==303 and COOKIE in response.cookies
        assert not seen
    asyncio.run(exercise(check,{'public_origin':public}))
