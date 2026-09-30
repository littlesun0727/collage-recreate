"""Static offline comparison and evidence report; visual verdicts only from SDK reviews."""
import argparse
import html
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--followup');p.add_argument('--baseline-root');args=p.parse_args();root=Path(args.root)
def rel(p):
    import os
    return quote(os.path.relpath(p,root).replace('\\','/'),safe='/')
def link(p,label):return f'<a href="{rel(p)}" target="_blank">{html.escape(label)}</a>'
rows=[];cards=[];actions=Counter();verdicts=Counter();times=[]
priority=['20260908-192922','拼贴2','拼贴7','海边人像拼图']
cases=sorted(root.glob('*/build-evidence.json'),key=lambda p:(priority.index(p.parent.name) if p.parent.name in priority else 100,p.parent.name))
for ep in cases:
    case=ep.parent;run=case/'run';e=read(ep);r=read(run/'result.json');index=read(run/'assets/reveal/index.json')
    ev=read(case/'evaluation.json') if (case/'evaluation.json').exists() else {}
    state=read(case/'review-controller/state.json') if (case/'review-controller/state.json').exists() else {}
    verdict=r['status'];verdicts[verdict]+=1;actions.update(e['actions']);times.append(e['build_seconds'])
    old=Path(e['old_run']);inp=read(run/'input.json');frozen=all(sha(old/f)==sha(run/f)==h for f,h in e['frozen_inputs'].items())
    hash_ok=sha(run/'final.png')==r['final_sha256'] and sha(run/'previews/first.png')==r['final_sha256']
    row={**e,'status':verdict,'review_model':state.get('model'),'review_effort':state.get('effort'),'review_state':state.get('status'),
         'review_seconds':state.get('elapsed_seconds'),'thread_id':state.get('thread_id'),'frozen_inputs_unchanged':frozen,'first_final_hash_match':hash_ok,
         'coverage_complete':r['coverage_complete'],'visual_issues':ev.get('issues',[])};rows.append(row)
    title=html.escape(case.name);label={'verified':'首版通过','needs_changes':'需要修改','preview':'待模型复核'}.get(verdict,verdict)
    local=root/'_local_controls'/case.name/'run/previews/first.png'
    images=[('参考图',run/'prepared/reference.png'),('当前本地对照（同输入）',local) if local.exists() else ('原本地首版',old/'previews/first.png'),('本次提取回填首版',run/'previews/first.png')]
    if args.baseline_root:images[1]=('修正前提取首版',Path(args.baseline_root)/case.name/'run/previews/first.png')
    figures=''.join(f'<figure><figcaption>{t}</figcaption><a href="{rel(p)}" target="_blank"><img src="{rel(p)}" loading="lazy"></a></figure>' for t,p in images)
    fallback=[x for x in index['records'] if x['action']=='local_fallback']
    issues=ev.get('issues',[]);strengths=ev.get('strengths',[])
    def bullets(values):return '<ul>'+''.join('<li>'+html.escape(str(v))+'</li>' for v in values)+'</ul>' if values else '<p>暂无记录。</p>'
    details=bullets([x['id']+': '+x.get('reason','') for x in fallback])
    links=' · '.join([link(run/'final.png','新图原尺寸'),link(old/'previews/first.png','原历史首版'),link(run/'previews/comparison.png','完整对照'),link(run/'analysis.json','analysis'),link(run/'assets/reveal/index.json','回填记录'),link(run/'result.json','result')]+([link(run/'review.json','模型review')] if (run/'review.json').exists() else []))
    sheets=' · '.join(link(Path(p),f'素材检查表{i+1}') for i,p in enumerate(r.get('extraction_sheets',[])))
    prototype=Path('D:/codes/collage_outputs/v5-reveal-backfill-preview-20260929-r2')/case.name/'preview.png'
    cards.append(f'''<article data-status="{verdict}"><h2>{title} <span class="badge {verdict}">{label}</span></h2>
<p>离线build <b>{e['build_seconds']:.2f}s</b> · 复用 {e['actions'].get('reuse_layer',0)} · 清窗 {e['actions'].get('clear_photo_window',0)} · 回退 {len(fallback)} · 客户照片来源核验 {'通过' if r['customer_photos_verified'] else '失败'}</p>
<div class="images">{figures}</div><p>{links}</p><p>{sheets}</p>
<details open><summary>gpt-5.6-sol / medium 视觉意见</summary><p>{html.escape(r.get('visual_review',{}).get('summary','复核尚未完成。') if r.get('visual_review') else '复核尚未完成。')}</p>{bullets(issues)}<details><summary>模型记录的优点</summary>{bullets(strengths)}</details></details>
<details><summary>回退与文字归属</summary>{details}<pre>{html.escape(json.dumps(index['unconfirmed_embedded_text'],ensure_ascii=False,indent=2))}</pre><p>未明确归属的疑似叠字保留并提示，不凭位置自动删掉。</p></details></article>''')
service=[]
for p in root.glob('*/run/assets/reveal/index.json'):
    service += [x['generation_seconds'] for x in read(p)['service_tasks'] if isinstance(x.get('generation_seconds'),(int,float))]
summary={'cases':len(rows),'statuses':dict(verdicts),'actions':dict(actions),'offline_build_median_seconds':round(statistics.median(times),3) if times else None,
         'offline_build_range_seconds':[min(times),max(times)] if times else [],'remote_submissions':0,
         'historical_extraction_service_tasks':len(service),'historical_service_median_seconds':round(statistics.median(service),3) if service else None,
         'evaluation_scope':'Same frozen historical analysis and customer bindings; current production build with downloaded Reveal cache. No new analysis or remote extraction. Visual review uses one independent Codex SDK run per case.',
         'mechanical_ok':all(x['frozen_inputs_unchanged'] and x['first_final_hash_match'] and x['coverage_complete'] and x['photos_verified'] for x in rows),'cases_detail':rows}
save(root/'summary.json',summary)
followup='<p>路线误回退及组合生成后的文字归属已修正。'+link(Path(args.followup)/'index.html','查看单独保存的修正回归')+'；不计入上面16张首次数据。</p>' if args.followup else ''
comparison_note='中列为修正前提取首版，右列为修正后。此单例回归不并入16张首次数据。' if args.baseline_root else '前四张另外重跑了同版本本地对照；其余为原历史首版，列名明确区分。'
body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v5 提取回填首版</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;background:#f3f4f6;color:#222;margin:0}}main{{max-width:1550px;margin:auto;padding:28px}}h1{{margin-bottom:8px}}h2{{font-size:21px}}article,.intro{{background:white;padding:22px;margin:22px 0;border-radius:12px}}.images{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;align-items:start}}figure{{margin:0}}figcaption{{font-weight:600;padding:8px 0}}img{{width:100%;height:auto;display:block;background:#ddd}}a{{color:#175fba}}.badge{{font-size:14px;padding:4px 9px;border-radius:6px;background:#eee}}.verified{{background:#ddf6e4}}.needs_changes{{background:#fff0d5}}.intro p{{margin:10px 0}}button,select{{padding:8px 12px;margin:0 8px 8px 0}}details{{margin-top:12px}}summary{{cursor:pointer;font-weight:600}}pre{{white-space:pre-wrap}}@media(max-width:750px){{main{{padding:10px}}article{{padding:12px}}.images{{grid-template-columns:1fr}}}}</style>
<main><h1>v5：提取 → 清理 → 客户照片回填</h1><div class="intro"><p>这次将你认可的回填原型接入正式build；分析与照片绑定保持原样，隔离观察制作路径的变化。</p>
<p><b>{len(rows)} 张已成图</b> · 离线build中位数 <b>{summary['offline_build_median_seconds']}s</b> · 清理窗口 <b>{actions.get('clear_photo_window',0)}</b> · 新增付费提取 <b>0 次</b></p>
<p>视觉状态：{html.escape(str(dict(verdicts)))}。文件和来源核验不等于视觉通过。</p>
<p>耗时只统计缓存条件下编译/制作/合成；不包含历史分析、此次模型复核、首次网络上传和下载。对应历史{len(service)}次提取任务服务耗时中位数{summary['historical_service_median_seconds']}秒，不能据此承诺首次端到端速度。</p>
<p>点击图片打开原尺寸。左：参考。{comparison_note} 客户人物不同是正常替换。</p>{followup}
<select id="filter" onchange="filterCards()"><option value="all">全部</option><option value="needs_changes">需要修改</option><option value="verified">首版通过</option><option value="preview">待复核</option></select><button onclick="document.querySelectorAll('article details').forEach(x=>x.open=false)">收起说明</button> {link(root/'summary.json','完整数据')}</div>{''.join(cards)}</main>
<script>function filterCards(){{let v=document.getElementById('filter').value;document.querySelectorAll('article').forEach(x=>x.hidden=v!=='all'&&x.dataset.status!==v)}}</script></html>'''
(root/'index.html').write_text(body,encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k!='cases_detail'},ensure_ascii=False,indent=2))
