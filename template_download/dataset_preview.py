"""Build a lightweight local dataset gallery without changing catalog decisions."""
import argparse
import html
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageOps


LABELS={'accept':'已保留','pending':'等待模型审核','model_review':'模型要求复核',
        'quality_review':'其他质量检查','live_review':'Live 视频待处理','duplicate':'完全重复',
        'near_duplicate':'近似重复','reject':'内容排除','rejected':'初筛不合格','error':'下载失败'}
REASONS={'uncertain_boundary':'模型无法确定完整边界','occluded_or_cutoff':'模型判断有遮挡或截断',
         'live_video_geometry_unverified':'封面已裁剪，视频画面尚未同步验证',
         'post_crop_resolution':'尺寸提示：裁后低于原下载尺寸门槛，仍保留',
         'post_crop_aspect_ratio':'裁后长宽比超出范围','crop_too_small_for_auto_accept':'裁剪区域过小',
         'multiple_templates':'包含多套独立模板','not_template':'非拼贴模板',
         'external_ui':'已裁去模板外界面','external_margin':'已裁去外部边距','clean':'无需裁剪'}


def display_group(row):
    if row['status']!='ready':return row['status']
    if row['processed_status'] in ('duplicate','near_duplicate'):return row['processed_status']
    if row['review']!='unsure':return row['review']
    if row['reason']=='live_video_geometry_unverified':return 'live_review'
    if row['action']=='review':return 'model_review'
    return 'quality_review'


def build(root):
    root=Path(root).resolve()
    db=sqlite3.connect((root/'catalog.sqlite').as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    stats={'images':dict(db.execute('SELECT status,COUNT(*) FROM images GROUP BY status')),
           'reviews':dict(db.execute("SELECT review,COUNT(*) FROM images WHERE status='ready' GROUP BY review")),
           'live':dict(db.execute('SELECT status,COUNT(*) FROM live_assets GROUP BY status'))}
    rows=db.execute('''SELECT i.id,i.path,i.width,i.height,i.review,i.status,i.media_kind,
        p.path AS processed_path,p.width AS processed_width,p.height AS processed_height,p.reason,p.action,p.status AS processed_status,
        a.path AS video_path FROM images i LEFT JOIN processed_assets p ON p.image_id=i.id
        LEFT JOIN live_assets a ON a.image_id=i.id WHERE i.path IS NOT NULL ORDER BY i.id''').fetchall()
    stages=''
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_assets'").fetchone():
        raw=db.execute("SELECT COUNT(*) FROM raw_assets WHERE kind='cover' AND status='ready'").fetchone()[0]
        waiting=stats['images'].get('downloaded',0)
        review=db.execute("""SELECT COUNT(*) FROM images i LEFT JOIN live_assets a ON a.image_id=i.id
            WHERE i.status='ready' AND i.review='pending' AND (i.media_kind='image' OR a.status='ready')""").fetchone()[0]
        attention=db.execute("SELECT value FROM pipeline_state WHERE key='collection_requires_attention'").fetchone()
        blocked=bool(attention and json.loads(attention[0]))
        stats['stages']={'raw_covers':raw,'waiting_screen':waiting,'waiting_vlm':review,'collection_requires_attention':blocked}
        stages=f'<p>采集已落盘：{raw} 张封面 · 等待初筛：{waiting} · 等待 VLM：{review} · 采集访问状态：'+('需要处理验证' if blocked else '未记录访问阻塞')+'</p>'
        stages+='<p>三个阶段独立运行；采集停止不影响本地初筛和审核。此页面是生成时的快照。</p>'
    db.close()
    thumbs=root/'preview-thumbs';thumbs.mkdir(exist_ok=True)
    cards=[];counts=Counter(display_group(r) for r in rows)
    cfg_path=Path(__file__).parent/'config.1000.json'
    cfg=json.loads(cfg_path.read_text(encoding='utf-8'))
    for row in rows:
        source=root/(row['processed_path'] or row['path'])
        filename=f'{row["id"]}-{source.stem[:16]}.jpg'
        with Image.open(source) as original:
            size=(row['processed_width'],row['processed_height']) if row['processed_path'] else (row['width'],row['height'])
            if not all(size):size=original.size
            if not (thumbs/filename).exists() or (thumbs/filename).stat().st_mtime<source.stat().st_mtime:
                im=ImageOps.exif_transpose(original).convert('RGB');size=im.size
                im.thumbnail((300,420));im.save(thumbs/filename,quality=80)
        href=Path(os.path.relpath(source,root)).as_posix()
        group=display_group(row)
        caption=f"#{row['id']} | {LABELS.get(group,group)} | {size[0]}×{size[1]} | {row['media_kind']}"
        warning=row['action']=='crop' and (min(size)<cfg['min_short_edge'] or size[0]*size[1]<cfg['min_pixels'])
        detail=REASONS.get(row['reason'],row['reason'] or '')
        if warning and row['reason']!='post_crop_resolution':detail+='；裁后尺寸偏小'
        video=''
        if row['video_path']:
            video='<a href="'+html.escape(Path(os.path.relpath(root/row['video_path'],root)).as_posix(),quote=True)+'">MP4</a>'
        original_href=Path(os.path.relpath(root/row['path'],root)).as_posix()
        cards.append(f'<article data-group="{html.escape(group)}" data-warning="{int(warning)}"><a href="{html.escape(href,quote=True)}"><img loading="lazy" src="preview-thumbs/{filename}?v={source.stat().st_mtime_ns}"></a><p>{html.escape(caption)}</p><p>{html.escape(detail)}</p><a href="{html.escape(original_href,quote=True)}">原图</a> {video}</article>')
    groups=sorted(counts)
    buttons='<button onclick="filter(\'all\')">全部</button>'+''.join(f'<button onclick="filter(\'{g}\')">{LABELS.get(g,g)} {counts[g]}</button>' for g in groups)+'<button onclick="filter(\'warning\')">尺寸提示</button>'
    buttons=stages+buttons
    updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    temp=root/'gallery.html.tmp'
    temp.write_text('''<!doctype html><meta charset="utf-8"><title>模板数据集预览</title>
    <style>body{background:#17212b;color:#edf3f8;font:15px system-ui;padding:20px}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}article{background:#263544;padding:10px}img{width:100%;height:340px;object-fit:contain;background:#101820}a{color:#8dcfff}button{padding:10px;margin:6px}pre{white-space:pre-wrap}</style>
    <h1>模板数据集预览</h1><p>等待模型审核：尚未送入模型，无需操作。模型要求复核：模型明确拿不准。尺寸提示不阻止保留。点击图片看实际尺寸。</p><p>生成时间：'''+updated+'。每轮审核完成后更新，浏览器需刷新。</p>'+buttons+'<main>'+''.join(cards)+'''</main><script>function filter(g){document.querySelectorAll('article').forEach(a=>a.hidden=g==='warning'?a.dataset.warning!=='1':g!=='all'&&a.dataset.group!==g)}</script>''',encoding='utf-8')
    os.replace(temp,root/'gallery.html')
    (root/'run-summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(stats,ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    build(parser.parse_args().root)


if __name__=='__main__':main()
