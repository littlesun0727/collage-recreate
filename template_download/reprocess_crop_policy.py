"""Migrate saved crop decisions under the profile lock, retaining a SQLite backup."""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageOps
from store import Store
from run import lock
from edge_snap import postprocess_box
from crop_review import apply_decision
from dataset_preview import build
from paths import DATA_ROOT


def main():
    base=Path(__file__).resolve().parent
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    args=parser.parse_args()
    root=args.root.resolve()
    cfg=json.loads((base/'config.1000.json').read_text(encoding='utf-8'))
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    with (DATA_ROOT/'.worker.lock').open('a+b') as guard:
        lock(guard)
        store=Store(root)
        try:
            backup=root/f'catalog-before-crop-policy-{stamp}.sqlite'
            with sqlite3.connect(backup) as dest:store.db.backup(dest)
            # Earlier PoC localization was improved in a separate dataset. Only use
            # those saved results when IDs AND original source paths match.
            overrides={}
            reference=DATA_ROOT/'pipeline-stage2-test'
            if (reference/'catalog.sqlite').exists():
                with sqlite3.connect((reference/'catalog.sqlite').as_uri()+'?mode=ro',uri=True) as ref:
                    sources=dict(ref.execute('SELECT id,path FROM images'))
                for report in sorted((reference/'vlm-reports').glob('*-locate-*.json')):
                    for item in json.loads(report.read_text(encoding='utf-8'))['items']:
                        ident=int(item['id'])
                        row=store.db.execute('SELECT path FROM images WHERE id=?',(ident,)).fetchone()
                        if row and row['path'] and ident in sources and (root/row['path']).resolve()==(reference/sources[ident]).resolve():
                            overrides[ident]=item
            rows=store.db.execute("""SELECT p.*,i.path AS original_path FROM processed_assets p
                JOIN images i ON i.id=p.image_id WHERE p.action='crop' ORDER BY p.image_id""").fetchall()
            changes=[]
            for row in rows:
                ident=row['image_id']
                decision={'id':str(ident),'label':'single','action':'crop','bbox':json.loads(row['bbox']), 'reason':'external_ui'}
                if ident in overrides:
                    item=overrides[ident]
                    decision.update(action=item['status'],bbox=item['bbox'],reason=item['reason'])
                if decision['action']=='crop':
                    with Image.open(root/row['original_path']) as source:
                        snap=postprocess_box(ImageOps.exif_transpose(source).convert('RGB'),decision['bbox'])
                    decision.update(edge_snap=snap,crop_pixels=snap['pixels'])
                with store.db:apply_decision(store,decision,cfg)
                after=dict(store.db.execute('SELECT status,reason,width,height FROM processed_assets WHERE image_id=?',(ident,)).fetchone())
                changes.append({'id':ident,'before':{'status':row['status'],'reason':row['reason']},'after':after,'decision':decision})
            (root/f'crop-policy-migration-{stamp}.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
            store.export(root/'accepted.jsonl')
            store.export(root/'candidates.jsonl',candidates=True)
            from crop_report import report
            report(store)
            build(root)
            print(json.dumps({'reprocessed':len(changes),'backup':str(backup),'stats':store.stats()},ensure_ascii=False))
        finally:store.db.close()


if __name__=='__main__':main()
