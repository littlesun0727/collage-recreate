"""Apply VLM crop decisions at original resolution; preserve every source file."""
import hashlib
import io
import json
import math
import os

from PIL import Image, ImageOps

from quality import inspect_image, bands

VERSION = 'crop-v2'


def pixel_box(bbox, width, height):
    if len(bbox)!=4 or any(type(v) is not int or not 0<=v<=1000 for v in bbox):
        raise ValueError('invalid_bbox')
    left,top,right,bottom = bbox
    if left>=right or top>=bottom:
        raise ValueError('invalid_bbox')
    return (math.floor(left*width/1000), math.floor(top*height/1000),
            math.ceil(right*width/1000), math.ceil(bottom*height/1000))


def apply_decision(store, item, cfg):
    ident = int(item['id'])
    row = store.db.execute('SELECT * FROM images WHERE id=?',(ident,)).fetchone()
    action, bbox, reason = item['action'],item['bbox'],item['reason']
    status, decision = 'review','unsure'
    info, path, pixel_sha, dup_id = {}, None, None, None
    store.db.execute('DELETE FROM processed_bands WHERE image_id=?',(ident,))
    if action == 'reject':
        status,decision = 'rejected','reject'
    elif action in ('keep','crop'):
        source_path = store.root / row['path']
        with Image.open(source_path) as source:
            im = ImageOps.exif_transpose(source).convert('RGB')
        if action == 'crop':
            box = pixel_box(bbox,*im.size)
            if 'crop_pixels' in item:
                box=tuple(item['crop_pixels'])
                if len(box)!=4 or any(type(v) is not int for v in box) or not (0<=box[0]<box[2]<=im.width and 0<=box[1]<box[3]<=im.height):
                    raise ValueError('invalid_local_crop_pixels')
            if (box[2]-box[0])*(box[3]-box[1]) < im.width*im.height*.25:
                reason = 'crop_too_small_for_auto_accept'
            else:
                im = im.crop(box)
                status = 'ready'
        else:
            status = 'ready'
        if status == 'ready':
            encoded = io.BytesIO()
            im.save(encoded,format='PNG')
            normalized = encoded.getvalue()
            pixel_sha = hashlib.sha256(str(im.size).encode()+im.tobytes()).hexdigest()
            data = normalized if action=='crop' else source_path.read_bytes()
            info = inspect_image(data,cfg)
            path = row['path']
            if action == 'crop':
                path = f'processed/{VERSION}/{info["sha256"][:2]}/{info["sha256"]}.png'
                dest = store.root/path
                dest.parent.mkdir(parents=True,exist_ok=True)
                temp = dest.with_suffix('.part')
                temp.write_bytes(data)
                os.replace(temp,dest)
            resolution_warning = action=='crop' and info['reason']=='resolution'
            if resolution_warning:
                reason='post_crop_resolution'
            review_reason=item.get('needs_review_reason')
            if review_reason in ('local_boundary_unresolved','crop_predictions_disagree'):
                review_reason=None  # Retired program-only gates, including saved legacy decisions.
            if info['reason'] and not resolution_warning:
                status,reason = 'review','post_crop_'+info['reason']
            elif review_reason:
                status,reason = 'review',review_reason
            elif row['media_kind']=='live' and action=='crop':
                status,reason = 'review','live_video_geometry_unverified'
            else:
                same = store.db.execute('''SELECT p.image_id FROM processed_assets p JOIN images i ON i.id=p.image_id
                    WHERE p.pixel_sha256=? AND p.status='ready' AND p.image_id!=?
                    AND i.media_kind='image' ORDER BY p.image_id LIMIT 1''',(pixel_sha,ident)).fetchone()
                if row['media_kind']=='image' and same:
                    status,dup_id = 'duplicate',same[0]
                elif row['media_kind']=='image':
                    matches=set()
                    for b,v in bands(info['dhash']):
                        matches.update(r[0] for r in store.db.execute('SELECT image_id FROM processed_bands WHERE band=? AND value=?',(b,v)))
                    for candidate in sorted(matches):
                        other=store.db.execute('SELECT dhash FROM processed_assets WHERE image_id=?',(candidate,)).fetchone()
                        if (int(info['dhash'],16)^int(other[0],16)).bit_count()<=cfg['near_duplicate_distance']:
                            status,dup_id = 'near_duplicate',candidate
                            break
                if status=='ready':
                    decision='accept'
                    if row['media_kind']=='image':
                        store.db.executemany('INSERT OR IGNORE INTO processed_bands VALUES(?,?,?)',[(b,v,ident) for b,v in bands(info['dhash'])])
                elif status=='duplicate':
                    decision='reject'
    store.db.execute('''INSERT OR REPLACE INTO processed_assets
        (image_id,action,bbox,reason,path,sha256,pixel_sha256,dhash,width,height,status,duplicate_of,version)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (ident,action,json.dumps(bbox) if bbox else None,reason,path,info.get('sha256'),pixel_sha,
         info.get('dhash'),info.get('width'),info.get('height'),status,dup_id,VERSION))
    store.db.execute('UPDATE images SET review=? WHERE id=?',(decision,ident))
