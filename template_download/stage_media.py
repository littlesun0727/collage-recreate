"""Stage boundaries: collect raw bytes over HTTP; screen complete local files offline."""
import asyncio
import hashlib
import os
from pathlib import Path
import shutil

import httpx

from browser import AccessStopped
from live_media import validate_mp4
from quality import inspect_image, bands, near_match


def sync_raw_assets(store):
    """Adopt existing files without changing legacy screening/review decisions."""
    with store.db:
        store.db.execute('''INSERT OR IGNORE INTO raw_assets(image_id,kind,status,path,sha256)
            SELECT id,'cover','ready',path,sha256 FROM images WHERE path IS NOT NULL''')
        store.db.execute('''INSERT OR IGNORE INTO raw_assets(image_id,kind,status,path,sha256,bytes)
            SELECT image_id,'live','ready',path,sha256,bytes FROM live_assets
            WHERE status='ready' AND path IS NOT NULL''')
        store.db.execute('''INSERT OR IGNORE INTO raw_assets(image_id,kind)
            SELECT i.id,'cover' FROM images i JOIN notes n ON n.id=i.note_id
            WHERE i.status IN ('pending','error','downloaded') AND n.status!='excluded_video' ''')
        store.db.execute('''INSERT OR IGNORE INTO raw_assets(image_id,kind)
            SELECT a.image_id,'live' FROM live_assets a JOIN images i ON i.id=a.image_id
            JOIN notes n ON n.id=i.note_id
            WHERE a.url!='' AND a.status IN ('pending','error') AND n.status!='excluded_video' ''')


def raw_complete_count(store):
    return store.db.execute('''SELECT COUNT(*) FROM raw_assets r JOIN images i ON i.id=r.image_id
        WHERE r.kind='cover' AND r.status='ready'
        AND (i.media_kind='image' OR EXISTS(SELECT 1 FROM raw_assets v
            WHERE v.image_id=i.id AND v.kind='live' AND v.status='ready'))''').fetchone()[0]


async def collect_media(store,cfg,limit=1000):
    """No Pillow/quality/VLM calls: publish a file only after the entire body is saved."""
    sync_raw_assets(store)
    processed=0
    async with httpx.AsyncClient(timeout=60,follow_redirects=True,
            headers={'Referer':'https://www.xiaohongshu.com/'}) as client:
        async def fetch(row):
            await asyncio.sleep(cfg['download_delay_seconds'])
            maximum=cfg.get('max_live_bytes',100*1024*1024) if row['kind']=='live' else cfg['max_image_bytes']
            try:
                async with client.stream('GET',row['url']) as response:
                    if response.status_code in (401,403,429,461,471):
                        return row,None,f'access:{response.status_code}'
                    response.raise_for_status()
                    body=bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body)>maximum:return row,None,'body_too_large'
                    return row,bytes(body),None
            except httpx.HTTPError as exc:
                return row,None,type(exc).__name__
        # Keep current image concurrency and serial Live transfer; optimize separately.
        for kind,concurrency in (('cover',cfg['download_concurrency']),('live',1)):
            while processed<limit:
                capacity=limit-processed
                if kind=='cover':
                    saved=store.db.execute("SELECT COUNT(*) FROM raw_assets WHERE kind='cover' AND status='ready'").fetchone()[0]
                    capacity=min(capacity,cfg['target']-saved)
                    if capacity<=0:break
                rows=store.db.execute('''SELECT r.*,CASE WHEN r.kind='cover' THEN i.url ELSE a.url END AS url
                    FROM raw_assets r JOIN images i ON i.id=r.image_id
                    JOIN notes n ON n.id=i.note_id LEFT JOIN live_assets a ON a.image_id=i.id
                    WHERE r.kind=? AND r.status IN ('pending','error') AND r.attempts<?
                    AND n.status!='excluded_video'
                    AND (r.kind='cover' OR EXISTS(SELECT 1 FROM raw_assets c
                        WHERE c.image_id=i.id AND c.kind='cover' AND c.status='ready'))
                    ORDER BY r.attempts,r.image_id LIMIT ?''',
                    (kind,cfg['max_attempts'],min(concurrency,capacity))).fetchall()
                if not rows:break
                stopped=False
                for row,data,error in await asyncio.gather(*(fetch(r) for r in rows)):
                    path=None;sha=None
                    if data is not None:
                        sha=hashlib.sha256(data).hexdigest()
                        suffix='mp4' if kind=='live' else 'bin'
                        path=f'raw/{kind}/{sha[:2]}/{sha}.{suffix}'
                        dest=store.root/path;dest.parent.mkdir(parents=True,exist_ok=True)
                        temporary=dest.with_suffix('.part');temporary.write_bytes(data);os.replace(temporary,dest)
                    state='ready' if error is None else ('blocked' if error.startswith('access:') else 'error')
                    with store.db:
                        store.db.execute('''UPDATE raw_assets SET status=?,path=?,sha256=?,bytes=?,error=?,
                            attempts=attempts+1,updated=CURRENT_TIMESTAMP WHERE image_id=? AND kind=?''',
                            (state,path,sha,len(data) if data is not None else None,error,row['image_id'],kind))
                        if kind=='cover' and state=='ready':
                            store.db.execute("UPDATE images SET status='downloaded',error=NULL WHERE id=? AND status IN ('pending','error')",(row['image_id'],))
                    stopped |= state=='blocked'
                    processed+=1
                if stopped:raise AccessStopped('Media request requires access verification; collection stopped.')
    return processed


def screen_local(store,cfg,limit=1000):
    """Never issues HTTP requests. Partial Live pairs remain waiting for collection."""
    sync_raw_assets(store)
    rows=store.db.execute('''SELECT i.*,r.path AS raw_path,r.sha256 AS raw_sha,
        v.path AS video_path,v.sha256 AS video_sha FROM images i
        JOIN raw_assets r ON r.image_id=i.id AND r.kind='cover' AND r.status='ready'
        LEFT JOIN raw_assets v ON v.image_id=i.id AND v.kind='live' AND v.status='ready'
        WHERE (i.status='downloaded' OR (i.status='ready' AND i.media_kind='live' AND EXISTS(
            SELECT 1 FROM live_assets a WHERE a.image_id=i.id AND a.status!='ready')))
        AND (i.media_kind='image' OR v.path IS NOT NULL)
        ORDER BY i.id LIMIT ?''',(limit,)).fetchall()
    for row in rows:
        try:
            data=(store.root/row['raw_path']).read_bytes()
            if hashlib.sha256(data).hexdigest()!=row['raw_sha']:raise ValueError('raw_checksum_mismatch')
            info=inspect_image(data,cfg)
            if row['media_kind']=='live':
                motion=(store.root/row['video_path']).read_bytes()
                if hashlib.sha256(motion).hexdigest()!=row['video_sha']:raise ValueError('live_checksum_mismatch')
                validate_mp4(motion)
        except (OSError,ValueError,SyntaxError) as exc:
            with store.db:
                store.db.execute("UPDATE images SET status='rejected',error=? WHERE id=?",('invalid_local_media:'+type(exc).__name__,row['id']))
            continue
        status='rejected' if info['reason'] else 'ready';duplicate=None;path=None
        if not info['reason']:
            path=f'images/{info["sha256"][:2]}/{info["sha256"]}{info["extension"]}'
            destination=store.root/path;destination.parent.mkdir(parents=True,exist_ok=True)
            if not destination.exists():
                try:os.link(store.root/row['raw_path'],destination)
                except OSError:
                    temporary=destination.with_suffix('.part');shutil.copyfile(store.root/row['raw_path'],temporary)
                    os.replace(temporary,destination)
        with store.db:
            if row['media_kind']=='live':
                store.db.execute("UPDATE live_assets SET status='ready',path=?,sha256=?,bytes=?,error=NULL WHERE image_id=?",
                    (row['video_path'],row['video_sha'],len(motion),row['id']))
            if status=='ready':
                if row['media_kind']=='live':
                    same=store.db.execute('''SELECT i.id FROM images i JOIN live_assets a ON a.image_id=i.id
                        WHERE i.sha256=? AND a.sha256=? AND a.status='ready' AND i.status='ready'
                        AND i.id!=? ORDER BY i.id LIMIT 1''',(info['sha256'],row['video_sha'],row['id'])).fetchone()
                else:
                    same=store.db.execute('''SELECT id FROM images WHERE sha256=? AND path IS NOT NULL
                        AND id!=? ORDER BY id LIMIT 1''',(info['sha256'],row['id'])).fetchone()
                if same:status,duplicate='duplicate',same[0]
                elif row['media_kind']=='image':
                    duplicate=near_match(store.db,info['dhash'],cfg['near_duplicate_distance'])
                    if duplicate:status='near_duplicate'
            store.db.execute('''UPDATE images SET status=?,error=?,sha256=?,dhash=?,width=?,height=?,path=?,duplicate_of=?
                WHERE id=?''',(status,info['reason'],info['sha256'],info['dhash'],info['width'],info['height'],path,duplicate,row['id']))
            if status=='ready':
                store.db.executemany('INSERT OR IGNORE INTO hash_bands VALUES(?,?,?)',[(b,v,row['id']) for b,v in bands(info['dhash'])])
    return len(rows)
