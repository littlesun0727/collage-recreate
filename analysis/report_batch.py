"""Build an auditable batch index; show failures and separate manual review."""
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import statistics
from PIL import Image,ImageDraw,ImageFont


def load(path,default=None):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return default


def report(root):
    manifest=load(root/'manifest.json',[])
    rows=[];cards=[]
    for case in manifest:
        ident=case['id'];folder=root/'cases'/ident
        call=load(folder/'request/call.json',{})
        result=load(folder/'result.json',{})
        validation=load(folder/'review/validation.json',{})
        draft=load(folder/'request/raw-response.txt',{})
        if not isinstance(draft,dict):draft={}
        def array(key):
            value=draft.get(key)
            return value if isinstance(value,list) else []
        review=load(folder/'visual-review.json',{})
        row={'id':ident,'sample':Path(case['reference']).name,'status':result.get('status',call.get('status','pending')),
          'seconds':call.get('elapsed_seconds'),'http_dispatches':call.get('http_dispatches',0),'stop_reason':call.get('stop_reason'),
          'draft_valid':validation.get('valid',False),'slots':len(array('slots')),'texts':len(array('texts')),
          'overlays':len(array('overlays')),'background':draft.get('background'),
          'source_links':[{k:x.get(k) for k in ('id','source_slot_id')} for x in array('slots') if isinstance(x,dict) and x.get('source_slot_id')],
          'actions':dict(Counter(str(x.get('action')) for x in array('overlays') if isinstance(x,dict))),
          'questions':len(array('questions')),'errors':validation.get('errors',[]),'manual_review':review}
        rows.append(row)
        preview=folder/'review/preview-slots.png'
        fallback=folder/'review/reference.png'
        if not fallback.exists():fallback=next(folder.glob('reference.*'),Path(case['reference']))
        visible=preview if preview.exists() else fallback
        thumb=root/'thumbnails'/f'{ident}.jpg';thumb.parent.mkdir(exist_ok=True)
        if visible.exists():
            with Image.open(visible) as im:
                im=im.convert('RGB');im.thumbnail((540,760));im.save(thumb,quality=86)
        esc=html.escape
        errs='<pre>'+esc(json.dumps(row['errors'],ensure_ascii=False,indent=2))+'</pre>' if row['errors'] else ''
        findings='<ul>'+''.join('<li>'+esc(x)+'</li>' for x in review.get('findings',[]))+'</ul>'
        image=f'<img src="thumbnails/{ident}.jpg" alt="{esc(row["sample"])}">' if thumb.exists() else ''
        link=f'cases/{ident}/review/index.html'
        if not (folder/'review/index.html').exists():link=f'cases/{ident}/request/call.json'
        cards.append(f'<article><h2>{esc(ident)} · {esc(row["sample"])}</h2><p>{esc(row["status"])} · {row["seconds"]} 秒 · 图片 {row["slots"]} / 文字 {row["texts"]} / 装饰 {row["overlays"]}</p><a href="{link}">{image}<br>分类原框与校验结果</a><p>{esc(review.get("decision","尚未人工评审"))}</p>{findings}{errs}</article>')
    times=[r['seconds'] for r in rows if isinstance(r['seconds'],(int,float))]
    summary={'total':len(rows),'normal_stop':sum(r['stop_reason']=='stop' for r in rows),
      'structure_valid':sum(r['draft_valid'] for r in rows),'http_dispatches':sum(r['http_dispatches'] for r in rows),
      'median_seconds':statistics.median(times) if times else None,'max_seconds':max(times) if times else None,'rows':rows}
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v2 冻结批测</title><style>body{font:15px system-ui;max-width:1500px;margin:24px auto;padding:0 20px;background:#f5f6f8;color:#223}section{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:20px}article{background:white;padding:16px;border-radius:8px}img{width:100%;height:auto}h2{font-size:17px}pre{white-space:pre-wrap;overflow-wrap:anywhere}li{margin:8px 0}</style><h1>v2 Max/high 冻结批测</h1>'''
    page+=f'<p>共 {len(rows)} 张 · 正常结束 {summary["normal_stop"]} · 格式通过 {summary["structure_valid"]} · HTTP 请求 {summary["http_dispatches"]}</p><p>照片框来自模型原稿，没有吸边。格式合法不等于视觉通过；失败项仍保留。点击各图查看文字和装饰。</p><section>'+''.join(cards)+'</section></html>'
    (root/'index.html').write_text(page,encoding='utf-8')
    # Small contact sheets for manual coverage review, not model inputs.
    for start in range(0,len(rows),4):
        group=rows[start:start+4];sheet=Image.new('RGB',(1600,640),'#eeeeee');draw=ImageDraw.Draw(sheet)
        for i,row in enumerate(group):
            thumb=root/'thumbnails'/f"{row['id']}.jpg"
            if thumb.exists():
                im=Image.open(thumb);im.thumbnail((390,590));sheet.paste(im,(i*400+(400-im.width)//2,40))
            draw.text((i*400+8,10),row['id']+' '+str(row['slots'])+' slots / '+str(row['draft_valid']),fill='black')
        sheet.save(root/f'contact-{start//4+1:02d}.jpg',quality=88)
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);report(parser.parse_args().root)
