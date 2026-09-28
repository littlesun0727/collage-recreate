"""Download a returned artifact, never a model request or credential-bearing GET."""
import os
import urllib.request
from urllib.parse import urlsplit
from .common import BuildError, read_json, save, sha

def download_artifact(folder):
    url=read_json(folder/'artifact.json')['url']
    parts=urlsplit(url)
    if parts.scheme!='https' or parts.username or parts.password or parts.port or not parts.hostname or not parts.hostname.endswith('.aliyuncs.com'):
        raise BuildError('artifact_url','Unexpected artifact host')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):
            raise BuildError('artifact_redirect','Artifact redirects are not followed')
    proxy=os.environ.get('YIBU_ARTIFACT_PROXY','http://127.0.0.1:7890')
    if proxy!='http://127.0.0.1:7890':
        raise BuildError('artifact_proxy','Only the configured local artifact proxy is permitted')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({'https':proxy}),NoRedirect())
    with opener.open(urllib.request.Request(url),timeout=90) as response:
        data=response.read(30*1024*1024+1)
    if len(data)>30*1024*1024:
        raise BuildError('artifact_size','Artifact exceeds 30 MiB')
    target=folder/'original-image.bin'
    with target.open('xb') as stream:stream.write(data)
    from PIL import Image
    with Image.open(target) as image:image.load()
    call=read_json(folder/'call.json')
    call.update(status='completed',artifact_download_proxy=proxy,image_sha256=sha(target),image_bytes=len(data))
    save(folder/'call.json',call)
    return call

