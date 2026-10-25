import asyncio
import os

import httpx

from quality import inspect_image, bands, near_match
from browser import AccessStopped


async def download(store, cfg, limit):
    db = store.db
    processed = 0
    async with httpx.AsyncClient(timeout=40,follow_redirects=True,
            headers={'Referer':'https://www.xiaohongshu.com/'}) as client:
        async def fetch(row):
            await asyncio.sleep(cfg['download_delay_seconds'])
            try:
                async with client.stream('GET',row['url']) as response:
                    if response.status_code in (401,403,429):
                        return row, None, f'access:{response.status_code}'
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > cfg['max_image_bytes']:
                            return row,None,'image_too_large'
                    return row,bytes(body),None
            except httpx.HTTPError as error:
                # Do not print signed CDN URLs in errors.
                return row,None,type(error).__name__

        while processed < limit:
            ready = db.execute("SELECT COUNT(*) FROM images WHERE status='ready'").fetchone()[0]
            remaining = cfg['target'] - ready
            if remaining <= 0:
                break
            batch_size = min(cfg['download_concurrency'],limit-processed,remaining)
            rows = db.execute("SELECT * FROM images WHERE status IN ('pending','error') AND attempts<? ORDER BY attempts,id LIMIT ?",(cfg['max_attempts'],batch_size)).fetchall()
            if not rows:
                break
            results = await asyncio.gather(*(fetch(row) for row in rows))
            stop = False
            for row, data, error in results:
                ident = row['id']
                db.execute('UPDATE images SET attempts=attempts+1 WHERE id=?',(ident,))
                if error:
                    db.execute("UPDATE images SET status='error',error=? WHERE id=?",(error,ident))
                    stop |= error.startswith('access:')
                    continue
                try:
                    info = await asyncio.to_thread(inspect_image,data,cfg)
                except Exception as exc:
                    db.execute("UPDATE images SET status='rejected',error=? WHERE id=?",('invalid_image:'+type(exc).__name__,ident))
                    continue
                duplicate = db.execute("SELECT id,path FROM images WHERE sha256=? AND path IS NOT NULL LIMIT 1",(info['sha256'],)).fetchone()
                status = 'rejected' if info['reason'] else 'ready'
                dup_id = None
                path = None
                if duplicate:
                    status,dup_id,path = 'duplicate',duplicate['id'],duplicate['path']
                    if row['media_kind'] == 'live' and not info['reason']:
                        status = 'ready'  # Equal covers do not imply equal Live Photo motion.
                elif not info['reason']:
                    dup_id = near_match(db,info['dhash'],cfg['near_duplicate_distance'])
                    # Near matches are kept for review, never silently deleted.
                    if dup_id and row['media_kind'] != 'live':
                        status = 'near_duplicate'
                    path = f'images/{info["sha256"][:2]}/{info["sha256"]}{info["extension"]}'
                    destination = store.root / path
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    temporary = destination.with_suffix('.part')
                    temporary.write_bytes(data)
                    os.replace(temporary,destination)
                db.execute('''UPDATE images SET status=?,error=?,sha256=?,dhash=?,width=?,height=?,path=?,duplicate_of=? WHERE id=?''',
                    (status,info['reason'],info['sha256'],info['dhash'],info['width'],info['height'],path,dup_id,ident))
                if status == 'ready':
                    db.executemany('INSERT OR IGNORE INTO hash_bands VALUES(?,?,?)',[(b,v,ident) for b,v in bands(info['dhash'])])
            db.commit()
            processed += len(rows)
            print(f'本次处理 {processed}; {store.stats()}',flush=True)
            if stop:
                raise AccessStopped('图片服务器拒绝访问或限流，已停止。403 也可能是链接过期，可运行 refresh 后重新获取笔记。')
