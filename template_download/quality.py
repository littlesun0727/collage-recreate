import hashlib
import io

from PIL import Image, ImageOps


def inspect_image(data, cfg):
    with Image.open(io.BytesIO(data)) as check:
        check.verify()
    with Image.open(io.BytesIO(data)) as original:
        extension = {'JPEG':'.jpg','PNG':'.png','WEBP':'.webp','AVIF':'.avif',
                     'GIF':'.gif','BMP':'.bmp','TIFF':'.tiff','JPEG2000':'.jp2'}.get(original.format,'.img')
        im = ImageOps.exif_transpose(original).convert('RGB')
        width, height = im.size
        small = list(im.convert('L').resize((9,8)).tobytes())
        bits = 0
        for y in range(8):
            for x in range(8):
                bits = (bits << 1) | (small[y*9+x] > small[y*9+x+1])
        reason = None
        if min(width,height) < cfg['min_short_edge'] or width*height < cfg['min_pixels']:
            reason = 'resolution'
        elif max(width,height)/min(width,height) > cfg['max_aspect_ratio']:
            reason = 'aspect_ratio'
        return dict(sha256=hashlib.sha256(data).hexdigest(), dhash=f'{bits:016x}',
                    width=width,height=height,reason=reason,extension=extension)


def bands(hash_value):
    # Five disjoint bands guarantee a shared band for Hamming distance <= 4.
    number = int(hash_value,16)
    shift = 0
    for index, width in enumerate((13,13,13,13,12)):
        yield index, str((number >> shift) & ((1 << width)-1))
        shift += width


def near_match(db, value, distance):
    if distance < 0:
        return None
    candidates = set()
    for band, part in bands(value):
        candidates.update(row[0] for row in db.execute(
            'SELECT image_id FROM hash_bands WHERE band=? AND value=?',(band,part)))
    for ident in sorted(candidates):
        other = db.execute('SELECT dhash FROM images WHERE id=?',(ident,)).fetchone()[0]
        if (int(value,16) ^ int(other,16)).bit_count() <= distance:
            return ident
    return None
