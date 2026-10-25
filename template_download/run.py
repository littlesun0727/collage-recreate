import argparse
import asyncio
import base64
import html
import io
import json
import os
from pathlib import Path

from store import Store

BASE = Path(__file__).resolve().parent


def review_page(store, output, limit):
    from PIL import Image, ImageOps
    cards = []
    for row in store.db.execute("""SELECT i.*,n.title FROM images i JOIN notes n ON n.id=i.note_id
            WHERE i.status IN ('ready','near_duplicate') AND i.review IN ('pending','unsure') ORDER BY i.id LIMIT ?""",(limit,)):
        with Image.open(store.root / row['path']) as original:
            im = ImageOps.exif_transpose(original).convert('RGB')
            im.thumbnail((320,320))
            buf = io.BytesIO()
            im.save(buf,format='JPEG',quality=80)
        encoded = base64.b64encode(buf.getvalue()).decode()
        cards.append(f'''<article><img src="data:image/jpeg;base64,{encoded}"><p>#{row['id']} {html.escape(row['title'])}</p>
        <p>{row['width']} × {row['height']} · {row['status']} · 相似项 {row['duplicate_of'] or '-'}</p>
        <a href="https://www.xiaohongshu.com/explore/{row['note_id']}" target="_blank">原笔记</a>
        <select data-id="{row['id']}"><option value="pending">待审</option><option value="accept">保留：拼贴模板</option><option value="reject">排除</option></select></article>''')
    document = '''<!doctype html><meta charset="utf-8"><title>拼贴素材审核</title>
    <style>body{font:16px system-ui;background:#f5f5f5;padding:24px}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}article{background:white;padding:16px}img{width:100%;height:320px;object-fit:contain}button,select{padding:10px}header{position:sticky;top:0;background:#fff;padding:12px}</style>
    <header>选择真正可用的拼贴模板；教程截图、普通照片、广告请排除。<button onclick="save()">下载审核结果 JSON</button></header><main>'''+''.join(cards)+'''</main>
    <script>function save(){const rows=[...document.querySelectorAll('select')].filter(x=>x.value!=='pending').map(x=>({id:Number(x.dataset.id),review:x.value}));const url=URL.createObjectURL(new Blob([JSON.stringify(rows)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}</script>'''
    Path(output).write_text(document,encoding='utf-8')
    print(f'审核页 {output}，共 {len(cards)} 张；下载 decisions.json 后运行 apply-review。')


def lock(file):
    if os.fstat(file.fileno()).st_size == 0:
        file.write(b'0')
        file.flush()
    file.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(file.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)


def main():
    parser = argparse.ArgumentParser(description='独立的小红书拼贴素材采集、筛选与审核流程')
    parser.add_argument('--config',type=Path,default=BASE/'config.json')
    from paths import DATA_ROOT
    parser.add_argument('--data-dir',type=Path,default=DATA_ROOT)
    parser.add_argument('command',choices=['login','discover','details','download','run','import','import-mediacrawler','stats','review','apply-review','export','refresh'])
    parser.add_argument('--limit',type=int,default=200,help='每次笔记/图片上限；累计目标在 config.json')
    parser.add_argument('--input',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--candidates',action='store_true',help='导出未审核的技术达标候选图')
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding='utf-8-sig'))
    if args.limit < 1 or not 1 <= cfg['download_concurrency'] <= 8:
        parser.error('limit 必须为正数，并发数须为 1–8')
    if not -1 <= cfg['near_duplicate_distance'] <= 4:
        parser.error('near_duplicate_distance 须为 -1（关闭）或 0–4')
    if cfg['target'] < 1 or cfg['max_attempts'] < 1 or cfg['page_delay_seconds'] < 1 or cfg['download_delay_seconds'] < 0:
        parser.error('目标/重试次数必须为正数，页面间隔 >=1 秒，下载间隔 >=0')
    args.data_dir.mkdir(parents=True,exist_ok=True)
    with (args.data_dir/'.worker.lock').open('a+b') as guard:
        try:
            lock(guard)
        except OSError:
            parser.error('该数据目录已有任务运行，请等待结束。')
        store = Store(args.data_dir.resolve())
        try:
            if args.command in ('login','discover','details','run'):
                from browser import browse
                modes = ['discover','details'] if args.command == 'run' else [args.command]
                for mode in modes:
                    asyncio.run(browse(store,cfg,mode,args.limit))
            if args.command in ('download','run'):
                from download import download
                asyncio.run(download(store,cfg,args.limit))
            elif args.command == 'import-mediacrawler':
                if not args.input:
                    parser.error('import-mediacrawler 需要 --input，支持 JSONL 或 JSON')
                from mediacrawler_adapter import import_export
                print(json.dumps(import_export(store,args.input),ensure_ascii=False))
            elif args.command == 'import':
                if not args.input:
                    parser.error('import 需要 --input，格式为笔记 JSONL')
                with args.input.open(encoding='utf-8-sig') as source:
                    for line in source:
                        if line.strip():
                            store.ingest(json.loads(line))
            elif args.command == 'refresh':
                # Refresh metadata only for expired/failed image URLs; completed files remain intact.
                store.db.execute("UPDATE notes SET status='pending',attempts=0 WHERE id IN (SELECT note_id FROM images WHERE status='error') OR status IN ('error','blocked')")
                store.db.execute("UPDATE images SET attempts=0 WHERE status='error'")
                store.db.commit()
            elif args.command == 'review':
                review_page(store,args.output or store.root/'review.html',min(args.limit,500))
            elif args.command == 'apply-review':
                if not args.input:
                    parser.error('apply-review 需要 --input decisions.json')
                decisions = json.loads(args.input.read_text(encoding='utf-8-sig'))
                with store.db:
                    for item in decisions:
                        if item['review'] not in ('accept','reject','pending'):
                            raise ValueError('无效审核决定')
                        store.db.execute("""UPDATE images SET review=?,status=CASE WHEN status='near_duplicate' AND ?='accept' THEN 'ready' ELSE status END
                            WHERE id=? AND status IN ('ready','near_duplicate')""",(item['review'],item['review'],int(item['id'])))
            elif args.command == 'export':
                output = args.output or store.root/'manifest.jsonl'
                store.export(output,args.candidates)
                print(f'已导出 {output}')
            print(json.dumps(store.stats(),ensure_ascii=False,indent=2))
        finally:
            store.db.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('已中止；再次运行同一命令可继续。')
        raise SystemExit(130)
    except Exception as error:
        from browser import AccessStopped
        if isinstance(error,AccessStopped):
            print(str(error))
            raise SystemExit(2)
        raise
