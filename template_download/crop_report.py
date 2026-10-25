"""Local before/after review page; includes model boxes and processing decisions."""
import argparse
import base64
import html
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from crop_review import pixel_box
from store import Store


def thumbnail(path, bbox=None):
    with Image.open(path) as source:
        im=ImageOps.exif_transpose(source).convert('RGB')
    if bbox:
        ImageDraw.Draw(im).rectangle(pixel_box(bbox,*im.size),outline='red',width=max(3,im.width//200))
    im.thumbnail((460,650))
    out=io.BytesIO()
    im.save(out,format='JPEG',quality=85)
    return 'data:image/jpeg;base64,'+base64.b64encode(out.getvalue()).decode()


def report(store):
    cards=[]
    rows=store.db.execute('''SELECT p.*, i.path AS original_path,i.review FROM processed_assets p
        JOIN images i ON i.id=p.image_id ORDER BY CASE WHEN p.action='crop' THEN 0 ELSE 1 END,p.image_id''').fetchall()
    for row in rows:
        bbox=json.loads(row['bbox']) if row['bbox'] else None
        original=store.root/row['original_path']
        processed=store.root/row['path'] if row['path'] else None
        before=thumbnail(original,bbox)
        after=thumbnail(processed) if processed else before
        caption=f"#{row['image_id']} | {row['action']} | {row['status']} | {row['reason']} | bbox={bbox} | {row['width']}x{row['height']}"
        cards.append(f'<article><p>{html.escape(caption)}</p><div><img src="{before}"><img src="{after}"></div></article>')
    document='''<!doctype html><meta charset="utf-8"><title>裁剪前后复核</title>
    <style>body{font:16px system-ui;background:#17212b;color:#e8eef5;padding:20px}article{background:#263544;padding:15px;margin-bottom:20px}
    img{max-width:47%;max-height:650px;object-fit:contain;background:#101820;outline:1px solid #75889a}article div{display:flex;gap:20px;background:#101820;padding:12px}</style>
    <h1>裁剪前后复核</h1><p>左侧为原图及红色裁剪框，右侧为处理结果。原文件始终保留。</p>'''+''.join(cards)
    output=store.root/'crop-review.html'
    output.write_text(document,encoding='utf-8')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('data_dir',type=Path)
    args=parser.parse_args()
    store=Store(args.data_dir)
    try:
        print(report(store))
    finally:
        store.db.close()
