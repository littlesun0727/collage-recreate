import asyncio
import hashlib
import os

import httpx

from browser import AccessStopped
from live_media import validate_mp4


async def download_live(store, cfg):
    """Download bounded MP4 companions, including covers held for near-match review."""
    rows = store.db.execute('''SELECT a.* FROM live_assets a JOIN images i ON i.id=a.image_id
        WHERE a.status IN ('pending','error') AND a.attempts<?
        AND i.path IS NOT NULL AND i.status IN ('ready','near_duplicate','duplicate')
        AND i.review!='reject' ORDER BY a.image_id''', (cfg['max_attempts'],)).fetchall()
    async with httpx.AsyncClient(timeout=60, follow_redirects=True,
            headers={'Referer': 'https://www.xiaohongshu.com/'}) as client:
        for row in rows:
            await asyncio.sleep(cfg['download_delay_seconds'])
            store.db.execute('UPDATE live_assets SET attempts=attempts+1 WHERE image_id=?', (row['image_id'],))
            try:
                async with client.stream('GET', row['url']) as response:
                    if response.status_code in (401,403,429):
                        raise AccessStopped('Live media access denied: ' + str(response.status_code))
                    response.raise_for_status()
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > cfg.get('max_live_bytes', 100*1024*1024):
                            raise ValueError('live_too_large')
                validate_mp4(data)
                sha = hashlib.sha256(data).hexdigest()
                relative = f'videos/{sha[:2]}/{sha}.mp4'
                dest = store.root / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                temp = dest.with_suffix('.part')
                temp.write_bytes(data)
                os.replace(temp, dest)
                store.db.execute("UPDATE live_assets SET status='ready',error=NULL,path=?,sha256=?,bytes=? WHERE image_id=?",
                                 (relative,sha,len(data),row['image_id']))
                same = store.db.execute('''SELECT i.id FROM images i JOIN live_assets a ON a.image_id=i.id
                    WHERE a.sha256=? AND a.status='ready' AND i.status='ready' AND i.id!=?
                    AND i.sha256=(SELECT sha256 FROM images WHERE id=?) ORDER BY i.id LIMIT 1''',
                    (sha,row['image_id'],row['image_id'])).fetchone()
                if same:
                    store.db.execute("UPDATE images SET status='duplicate',duplicate_of=? WHERE id=?", (same[0],row['image_id']))
            except AccessStopped:
                store.db.execute("UPDATE live_assets SET status='error',error='access_denied' WHERE image_id=?", (row['image_id'],))
                store.db.commit()
                raise
            except (httpx.HTTPError, ValueError) as error:
                reason = str(error) if isinstance(error, ValueError) else type(error).__name__
                store.db.execute("UPDATE live_assets SET status='error',error=? WHERE image_id=?", (reason,row['image_id']))
            store.db.commit()
