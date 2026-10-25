"""Parse per-image Live Photo streams; never treat a video note as Live Photo."""
from urllib.parse import urlsplit


def http_url(value):
    if not isinstance(value, str):
        return ''
    if value.startswith('//'):
        value = 'https:' + value
    return value if urlsplit(value).scheme in ('http', 'https') else ''


def live_asset(image):
    if not isinstance(image, dict):
        return False, ''
    flag = image.get('live_photo', image.get('livePhoto'))
    nested = flag if isinstance(flag, dict) else {}
    candidates = []
    for obj in (image, nested):
        media = obj.get('media')
        media = media if isinstance(media, dict) else {}
        stream = obj.get('stream') or media.get('stream') or {}
        if not isinstance(stream, dict):
            continue
        for codec, entries in stream.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                url = http_url(entry.get('master_url') or entry.get('masterUrl'))
                if url:
                    try:
                        height = int(entry.get('height') or 0)
                    except (ValueError, TypeError):
                        height = 0
                    candidates.append((codec == 'h264', height, url))
    url = max(candidates)[2] if candidates else ''
    if not url:
        url = http_url(image.get('live_url') or image.get('liveUrl') or
                       image.get('live_photo_video') or image.get('livePhotoVideo'))
    marked = flag is True or flag == 1 or flag == 'true' or isinstance(flag, dict)
    return marked or bool(url), url


def validate_mp4(data):
    """Check complete ISO-BMFF boxes, MP4 brand and movie/video track; no transcoding."""
    offset, boxes = 0, {}
    while offset < len(data):
        if len(data) - offset < 8:
            raise ValueError('truncated_mp4')
        size = int.from_bytes(data[offset:offset+4], 'big')
        kind = bytes(data[offset+4:offset+8])
        header = 8
        if size == 1:
            header = 16
            size = int.from_bytes(data[offset+8:offset+16], 'big')
        elif size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            raise ValueError('invalid_mp4_box')
        boxes[kind] = data[offset+header:offset+size]
        offset += size
    brands = boxes.get(b'ftyp', b'')
    if not any(x in brands for x in (b'isom', b'iso2', b'mp41', b'mp42', b'avc1', b'iso6')):
        raise ValueError('not_mp4')
    if not boxes.get(b'mdat') or b'vide' not in boxes.get(b'moov', b''):
        raise ValueError('missing_video_track')
