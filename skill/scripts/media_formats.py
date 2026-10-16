"""Normalize phone media into the existing PNG/video workflow; retain originals."""
from pathlib import Path, PurePosixPath
import mmap
import os
import shutil
import uuid
import zipfile

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.heic', '.heif', '.hif', '.avif', '.tif', '.tiff', '.gif'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.webm'}
PACKAGE_EXTENSIONS = {'.livp'}
UPLOAD_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | PACKAGE_EXTENSIONS
CONVERT_IMAGES = {'.heic', '.heif', '.hif', '.avif', '.tif', '.tiff', '.gif'}
MAX_BYTES = 128 * 1024 * 1024
MAX_PIXELS = 80_000_000


def open_image(path):
    from PIL import Image
    if Path(path).suffix.lower() in {'.heic', '.heif', '.hif'}:
        try:
            from pillow_heif import register_heif_opener
        except ImportError as exc:
            raise ValueError('服务器缺少 HEIC 解码组件，请安装 requirements-motion.txt') from exc
        register_heif_opener(thumbnails=False)
    image = Image.open(path)
    if image.width * image.height > MAX_PIXELS:
        image.close()
        raise ValueError('图片超过 8000 万像素，请缩小后上传')
    return image


def still(path, destination):
    from PIL import ImageOps
    with open_image(path) as image:
        ImageOps.exif_transpose(image).convert('RGB').save(destination, format='PNG')


def appended_video(path, destination):
    """Recognize an appended MP4 by its container signature AND a video stream."""
    import av
    if not path.stat().st_size:
        return False
    with path.open('rb') as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        offset = 0
        for _ in range(16):
            found = data.find(b'ftyp', offset)
            if found < 0:
                break
            offset = found + 4
            start = found - 4
            if start < 16 or data[found+4:found+8] not in {b'isom', b'iso2', b'mp41', b'mp42', b'qt  ', b'avc1'}:
                continue
            size = int.from_bytes(data[start:found], 'big')
            if not 16 <= size <= 4096 or start + size > len(data):
                continue
            destination.write_bytes(data[start:])
            try:
                with av.open(str(destination)) as container:
                    if container.streams.video and next(container.decode(video=0), None) is not None:
                        return True
            except (av.error.FFmpegError, ValueError):
                pass
            destination.unlink(missing_ok=True)
    return False


def unpack_live(path, destination):
    """Extract one video, never archive paths; reject oversized/ambiguous packages."""
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 16 or sum(e.file_size for e in entries) > MAX_BYTES:
            raise ValueError('LIVP 解压内容过大')
        for entry in entries:
            name = PurePosixPath(entry.filename.replace('\\', '/'))
            if name.is_absolute() or '..' in name.parts or ':' in entry.filename or entry.flag_bits & 1:
                raise ValueError('LIVP 包含不安全或加密的文件')
        videos = [e for e in entries if not e.is_dir() and Path(e.filename).suffix.lower() in VIDEO_EXTENSIONS]
        if len(videos) != 1:
            raise ValueError('LIVP 必须包含一个 MOV/MP4 视频；请从相册导出视频后上传')
        destination = destination.with_suffix(Path(videos[0].filename).suffix.lower())
        with archive.open(videos[0]) as source, destination.open('xb') as output:
            remaining = MAX_BYTES
            while block := source.read(min(1024 * 1024, remaining + 1)):
                remaining -= len(block)
                if remaining < 0:
                    raise ValueError('LIVP 视频过大')
                output.write(block)
        return destination


def animated_image(path, destination):
    from live_media import encode_frames
    with open_image(path) as image:
        if not getattr(image, 'is_animated', False):
            return False
        if image.width * image.height > 20_000_000:
            raise ValueError('动图超过 2000 万像素，请缩小后上传')
        points, elapsed = [], 0
        for index in range(image.n_frames):
            if index >= 7200:
                raise ValueError('动图超过 7200 帧')
            image.seek(index)
            points.append(elapsed)
            elapsed += max(10, int(image.info.get('duration', 100) or 100)) * 1000
            if elapsed > 60_000_000:
                raise ValueError('动图时长不能超过 60 秒')
        size = image.size
    def frames():
        with open_image(path) as image:
            for index in range(len(points)):
                image.seek(index)
                yield image.convert('RGB')
    encode_frames(destination, size, points, elapsed, frames())
    return True


def normalize_inputs(reference, materials, run, publish):
    """Only create a normalized source directory if a material actually changes."""
    from common import save
    reference = Path(reference).resolve()
    roots = [Path(p).resolve() for p in materials]
    cache = Path(run).parent / '.live-media' / (Path(run).name + '-import-' + uuid.uuid4().hex[:12])
    cache.mkdir(parents=True)
    converted = cache / 'materials'
    converted.mkdir()
    reports, sources, changed = [], [], False
    if reference.suffix.lower() in CONVERT_IMAGES:
        publish('normalize', current=reference.name)
        try:
            still(reference, cache / 'reference.png')
        except Exception as exc:
            raise ValueError(f'{reference.name}：无法读取参考图：{exc}') from exc
        reference = cache / 'reference.png'
    paths = sorted({p.resolve() for root in roots for p in root.rglob('*') if p.is_file() and p.suffix.lower() in UPLOAD_EXTENSIONS})
    for index, path in enumerate(paths):
        suffix = path.suffix.lower()
        publish('normalize', current=path.name, done=index, total=len(paths))
        dest = converted / (str(index) + '-' + path.stem + '.mp4')
        kind, note = 'photo', ''
        try:
            if suffix in PACKAGE_EXTENSIONS:
                dest = unpack_live(path, dest)
                kind = 'video'
            elif suffix in {'.jpg', '.jpeg', '.heic', '.heif', '.hif'} and appended_video(path, dest):
                kind = 'video'
            elif suffix in {'.gif', '.webp', '.png', '.avif'} and animated_image(path, dest):
                kind = 'video'
            elif suffix in CONVERT_IMAGES:
                dest = dest.with_suffix('.png')
                still(path, dest)
                if suffix in {'.heic', '.heif', '.hif'}:
                    note = '已按照片导入，未识别到内嵌视频；需要动态效果时，请同时上传相册导出的视频。'
            else:
                sources.append(path)
                continue
        except Exception as exc:
            raise ValueError(f'{path.name}：素材转换失败：{exc}') from exc
        sources.append(dest)
        reports.append({'source': str(path), 'file': str(dest), 'kind': kind, 'note': note})
        changed = True
    if changed:
        for index, path in enumerate(sources):
            if path.parent != converted:
                dest = converted / (f'original-{index}-' + path.name)
                try:
                    os.link(path, dest)
                except OSError:
                    shutil.copyfile(path, dest)
                reports.append({'source': str(path), 'file': str(dest), 'kind': 'video' if path.suffix.lower() in VIDEO_EXTENSIONS else 'photo', 'note': ''})
        roots = [converted]
    save(cache / 'imports.json', {'files': reports})
    return reference, roots, reports
