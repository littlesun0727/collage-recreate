"""Real phone-format decoding, motion retention, and upload/prepare integration."""
import io
import json
from pathlib import Path
import threading
import time
import urllib.request
import zipfile

import pytest
from PIL import Image
from test_workbench import run
from common import read, sha
from live_media import prepare_live, inspect_video
from media_formats import unpack_live
from test_motion import clip
from workbench.server.http import create_server


@pytest.mark.parametrize('extension,format', [('heic','HEIF'),('heif','HEIF'),('hif','HEIF'),('avif','AVIF'),('tiff','TIFF'),('gif','GIF')])
def test_phone_photos_normalize_to_renderable_png(tmp_path, extension, format):
    from pillow_heif import register_heif_opener
    register_heif_opener()
    source = tmp_path/'materials'; source.mkdir()
    image = Image.new('RGB', (80, 120), '#e02840')
    reference = tmp_path/('ref.'+extension)
    image.save(reference, format=format)
    customer = source/('phone.'+extension)
    image.save(customer, format=format)
    before = sha(customer)
    target = tmp_path/'task'
    result = prepare_live(reference, [source], target, 200)
    assert result['asset_count'] == 1 and result['reference_size'] == [80,120]
    asset = read(target/'prepared/catalog.json')['assets'][0]
    assert Path(asset['file']).suffix == '.png' and asset['size'] == [80,120]
    with Image.open(asset['file']) as decoded:
        pixel = decoded.getpixel((30,30))
        assert abs(pixel[0]-224) < 10 and abs(pixel[1]-40) < 10
    assert sha(customer) == before
    report = read(target/'media/imports.json')['files'][0]
    assert report['kind'] == 'photo'
    assert bool(report['note']) == (format == 'HEIF')


def test_heic_orientation(tmp_path):
    from pillow_heif import register_heif_opener
    from media_formats import still
    register_heif_opener()
    image = Image.new('RGB', (80,120), 'red')
    exif = Image.Exif(); exif[274] = 6
    image.save(tmp_path/'rotated.heic', exif=exif.tobytes())
    still(tmp_path/'rotated.heic', tmp_path/'output.png')
    with Image.open(tmp_path/'output.png') as decoded:
        assert decoded.size == (120,80)


@pytest.mark.parametrize('kind', ['gif', 'webp', 'livp', 'appended-jpeg', 'appended-heic'])
def test_motion_survives_normalization(tmp_path, kind):
    from pillow_heif import register_heif_opener
    register_heif_opener()
    source = tmp_path/'materials'; source.mkdir()
    reference = tmp_path/'ref.png'
    Image.new('RGB', (128,160), 'white').save(reference)
    if kind in {'gif','webp'}:
        frames = [Image.new('RGB',(128,160),color) for color in ['red','blue']]
        frames[0].save(source/('animation.'+kind),save_all=True,append_images=frames[1:],duration=[80,120],loop=0)
    else:
        video = tmp_path/'original.mp4'; clip(video)
        if kind == 'livp':
            with zipfile.ZipFile(source/'phone.livp','w') as archive:
                archive.write(video,'IMG_0001.mov')
                archive.write(reference,'IMG_0001.jpg')
        else:
            photo = source/('phone.'+('jpg' if kind.endswith('jpeg') else 'heic'))
            Image.new('RGB', (128,160), 'green').save(photo)
            with photo.open('ab') as output: output.write(video.read_bytes())
    target = tmp_path/'task'
    result = prepare_live(reference,[source],target,200)
    assert result['live_count'] == 1 and result['asset_count'] == 1
    video = read(target/'media/manifest.json')['videos'][0]
    assert video['duration_us'] == (200000 if kind in {'gif','webp'} else 203000)
    assert video['frame_count'] == (2 if kind in {'gif','webp'} else 4)
    assert read(target/'media/imports.json')['files'][0]['kind'] == 'video'


def test_reject_unsafe_or_ambiguous_live_packages(tmp_path):
    for names in [['../escape.mov'],['one.mov','two.mp4'],['photo.heic']]:
        archive = tmp_path/'bad.livp'
        with zipfile.ZipFile(archive,'w') as output:
            for name in names:output.writestr(name,b'bad')
        with pytest.raises(ValueError):unpack_live(archive,tmp_path/'video.mp4')
    assert not (tmp_path.parent/'escape.mov').exists()


@pytest.mark.parametrize('invalid', [False, True])
def test_heic_http_upload_and_prepare(run, tmp_path, invalid):
    from pillow_heif import register_heif_opener
    register_heif_opener()
    stream = io.BytesIO()
    Image.new('RGB',(80,120),'red').save(stream,format='HEIF')
    server = create_server([run.parent],0,enable_motion=True,media_root=tmp_path/'service')
    thread = threading.Thread(target=server.serve_forever);thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def get(path):
        with opener.open(base+path) as response:return json.load(response)
    def post(path,body,headers=None):
        request = urllib.request.Request(base+path,data=body,headers={'Origin':base,'X-Chat-Token':token,'Content-Type':'application/json',**(headers or {})})
        with opener.open(request) as response:return json.load(response)
    try:
        token = get('/api/chat-config')['token']
        batch = post('/api/uploads',b'{}')['id']
        post(f'/api/uploads/{batch}/files',stream.getvalue(),{'X-File-Name':'reference.HEIC','X-Media-Role':'reference'})
        post(f'/api/uploads/{batch}/files',b'broken' if invalid else stream.getvalue(),{'X-File-Name':'phone.heic'})
        job = post(f'/api/uploads/{batch}/prepare',b'{"width":200}')
        for _ in range(150):
            result = get('/api/uploads/jobs/'+job['id'])
            if result['status'] in {'completed','failed'}:break
            time.sleep(.1)
        assert result['status'] == ('failed' if invalid else 'completed'), result
        if invalid:
            assert '素材转换失败' in result['error']
            assert 'phone.heic' in result['error']
        else:
            assert result['notes'] and 'phone.heic' in result['notes'][0]
            assert not get('/api/tasks/'+result['task_id']+'/motion')['videos']
    finally:
        server.shutdown();server.server_close();thread.join()
