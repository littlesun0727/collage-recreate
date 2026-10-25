"""Resumable collection, Live Photo pairing, and review every 100 candidates."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import time

from download import download
from live_download import download_live
from mediacrawler_adapter import import_export
from run import lock, review_page
from store import Store
from vlm_batch import CodexServer, PROMPT
from crop_review import apply_decision

BASE = Path(__file__).resolve().parent


def reconcile_crop(initial, refined):
    """Compatibility helper: the latest localization supersedes the old estimate."""
    return refined


def candidates(store):
    return [dict(r) for r in store.db.execute('''SELECT i.id,i.path FROM images i
        LEFT JOIN live_assets a ON a.image_id=i.id
        WHERE i.status='ready' AND i.review='pending'
        AND (i.media_kind='image' OR a.status='ready') ORDER BY i.id''')]


def review_cycle(store, server, model, count=100, concurrency=2, cfg=None):
    from PIL import Image, ImageOps
    from edge_snap import postprocess_box
    from vlm_stages import CLASSIFY_PROMPT, CROP_PROMPT
    if cfg is None:
        cfg = json.loads((BASE/'config.json').read_text(encoding='utf-8'))
    rows = candidates(store)[:count]
    if not rows:
        return 0
    for row in rows:
        row['path'] = str((store.root / row['path']).resolve())
    random.SystemRandom().shuffle(rows)
    row_by_id={str(r['id']):r for r in rows}
    directory=store.root/'vlm-reports'
    directory.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    prompt_sha=hashlib.sha256((CLASSIFY_PROMPT+CROP_PROMPT).encode()).hexdigest()
    classified=[]
    def save(result, stage, index):
        path=directory/f'{stamp}-{stage}-{index}.json'
        path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'{stage} batch {index}: {len(result["items"])} images',flush=True)
        return path
    def stage_calls(stage_rows, stage, batch_size, prompt, method):
        # Persist successful calls before a later call fails. A restart consumes only
        # missing work; model/prompt/source changes invalidate the corresponding cache.
        keys={};complete=[];missing=[]
        for row in stage_rows:
            digest=store.db.execute('SELECT sha256 FROM images WHERE id=?',(int(row['id']),)).fetchone()[0]
            digest=digest or hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()
            key=hashlib.sha256(json.dumps([stage,model,'low',prompt,digest],ensure_ascii=False).encode()).hexdigest()
            keys[str(row['id'])]=key
            cached=store.db.execute('SELECT payload,report FROM review_cache WHERE image_id=? AND stage=? AND cache_key=?',
                (int(row['id']),stage,key)).fetchone()
            if cached:complete.append((json.loads(cached['payload']),store.root/cached['report']))
            else:missing.append(row)
        failure=None
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            pending={pool.submit(method,missing[i:i+batch_size],model,'low'):i//batch_size for i in range(0,len(missing),batch_size)}
            for future in as_completed(pending):
                try:
                    result=future.result()
                    path=save(result,stage,pending[future])
                    with store.db:
                        for item in result['items']:
                            store.db.execute('''INSERT OR REPLACE INTO review_cache
                                (image_id,stage,cache_key,payload,report) VALUES(?,?,?,?,?)''',
                                (int(item['id']),stage,keys[item['id']],json.dumps(item),str(path.relative_to(store.root))))
                    complete.extend((item,path) for item in result['items'])
                except Exception as exc:
                    failure=failure or exc
        if failure:raise failure
        return complete
    classified=stage_calls(rows,'triage',20,CLASSIFY_PROMPT,server.triage)
    crop_rows=[row_by_id[item['id']] for item,_ in classified if item['label']=='single' and item['needs_crop']]
    located={item['id']:(item,path) for item,path in stage_calls(crop_rows,'locate',5,CROP_PROMPT,server.locate)}
    final=[]
    for classified_item,path in classified:
        ident=classified_item['id'];label=classified_item['label']
        if ident in located:
            item,path=located[ident]
            decision={'id':ident,'label':'single','action':item['status'],'bbox':item['bbox'],'reason':item['reason']}
            if item['status']=='crop':
                with Image.open(row_by_id[ident]['path']) as source:
                    im=ImageOps.exif_transpose(source).convert('RGB')
                    snap=postprocess_box(im,item['bbox'])
                decision['edge_snap']=snap
                decision['crop_pixels']=snap['pixels']
                # Unconfirmed edges retain VLM coordinates; this is not a review gate.
        else:
            action={'single':'keep','collection':'reject','other':'reject','unsure':'review'}[label]
            reason={'single':'clean','collection':'multiple_templates','other':'not_template','unsure':'uncertain_boundary'}[label]
            decision={'id':ident,'label':label,'action':action,'bbox':None,'reason':reason}
        with store.db:
            apply_decision(store,decision,cfg)
            store.db.execute('''INSERT OR REPLACE INTO vlm_results
                (image_id,label,model,effort,prompt_sha,report) VALUES(?,?,?,?,?,?)''',
                (int(ident),label,model,'low',prompt_sha,str(path.relative_to(store.root))))
        final.append(decision)
    save({'items':final,'model':model,'effort':'low','speed':'standard'},'decisions',0)
    return len(rows)


def state_get(store, key, default):
    row = store.db.execute('SELECT value FROM pipeline_state WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default


def state_set(store, key, value):
    with store.db:
        store.db.execute('INSERT OR REPLACE INTO pipeline_state VALUES(?,?)',(key,json.dumps(value)))


def process_pending(store, cfg, server, model, concurrency, cooldown):
    while True:
        pending = len(candidates(store))
        if pending >= 100:
            reviewed = review_cycle(store,server,model,100,concurrency,cfg)
            store.export(store.root/'accepted.jsonl')
            from dataset_preview import build
            build(store.root)
            print(f'VLM reviewed {reviewed}; {store.stats()}',flush=True)
            if cooldown > 0:
                time.sleep(cooldown)
            continue
        queued = store.db.execute("SELECT COUNT(*) FROM images WHERE status IN ('pending','error') AND attempts<?",
                                  (cfg['max_attempts'],)).fetchone()[0]
        if not queued:
            asyncio.run(download_live(store,cfg))
            if len(candidates(store)) >= 100:
                continue
            break
        before = store.db.total_changes
        asyncio.run(download(store,cfg,min(100-pending,queued)))
        asyncio.run(download_live(store,cfg))
        if store.db.total_changes == before:
            break


def main():
    from stage_cli import main as stage_main
    stage_main()


if __name__ == '__main__':
    main()
