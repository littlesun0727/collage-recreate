"""Publish live first-render results without assigning visual judgments."""
import argparse
import json
import os
from collections import Counter
from html import escape
from pathlib import Path
from statistics import median
from urllib.parse import quote


def read(path):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (FileNotFoundError, json.JSONDecodeError):return {}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);args=p.parse_args();root=Path(args.root)
    manifest=read(root/'build-manifest.json');source=Path(manifest['analysis_root']);cases=[];cards=[]
    def url(path):return quote(os.path.relpath(path,root).replace('\\','/'),safe='/')
    def link(path,label):return f'<a href="{url(path)}" target="_blank">{escape(label)}</a>' if path.exists() else ''
    for name in manifest['names']:
        case=root/name;run=case/'run';state=read(case/'build-state.json');ev=read(case/'build-evidence.json')
        result=read(run/'result.json');review_state=read(case/'review-controller/state.json');assessment=read(case/'evaluation.json')
        tasks=[]
        for d in sorted((run/'assets/reveal/api').glob('batch_*')):
            meta=read(d/'request_meta.json');submit=read(d/'submit_response.json');query=read(d/'query_response.json')
            tasks.append({'task_id':submit.get('task_id'),'status':query.get('status','pending'),
                'requested':len(meta.get('objects',[])), 'returned':len(query.get('output',{}).get('boxes_mapping_index',[])),
                'service_seconds':query.get('generation_time')})
        events=[json.loads(x) for x in (run/'events.jsonl').read_text(encoding='utf-8').splitlines()] if (run/'events.jsonl').exists() else []
        api=[e for e in events if e.get('event') in ['reveal_request_finished','reveal_request_failed']]
        queued='queued_build' if (source/name/'run/analysis-freeze.json').exists() else 'waiting_analysis'
        first=(run/'previews/first.png').exists();status=result.get('status',state.get('status',queued))
        row={'name':name,'built':first,'build_state':state.get('status','waiting_analysis'),'status':status,
            'build_seconds':ev.get('build_seconds'),'prepare_and_build_seconds':ev.get('prepare_and_build_seconds'),
            'api_seconds':round(sum(e.get('elapsed_seconds',0) for e in api),3) if first or api else None,
            'review_seconds':review_state.get('elapsed_seconds'),'review_state':review_state.get('status','pending'),
            'actions':ev.get('actions',{}),'incomplete':result.get('incomplete_objects',[]),
            'tasks':tasks,'api_errors':[e for e in api if e['event']=='reveal_request_failed'],
            'summary':(result.get('visual_review') or {}).get('summary'),
            'issues':assessment.get('issues',[]),'build_error':state.get('error')}
        cases.append(row)
        images=''
        for label,path in [('参考图',run/'prepared/reference.png'),('本轮第一次360回填',run/'previews/first.png')]:
            if path.exists():images+=f'<figure><figcaption>{label}</figcaption><a href="{url(path)}" target="_blank"><img loading="lazy" src="{url(path)}" alt="{escape(name)} {label}"></a></figure>'
        links=[link(run/'analysis.json','analysis.json'),link(run/'bindings.json','bindings.json'),
            link(run/'previews/comparison.png','完整对照图'),link(run/'assets/reveal/index.json','素材处理记录'),
            link(run/'review.json','整图复核')]
        links += [link(Path(f),f'素材检查表{i+1}') for i,f in enumerate(result.get('extraction_sheets',[]))]
        issue_html=''.join('<li>'+escape(str(x))+'</li>' for x in row['issues'])
        cards.append(f'''<article data-name="{escape(name)}"><h2>{escape(name)} <small>{escape(status)}</small></h2>
<p>制作 {row['build_seconds'] or '—'} 秒 · 其中360 {row['api_seconds'] if row['api_seconds'] is not None else '—'} 秒 · 复核 {row['review_seconds'] or '—'} 秒</p>
<div class="images">{images or '<p>等待本轮解析结果。</p>'}</div><p>{' · '.join(x for x in links if x)}</p>
<p>{escape(row['summary'] or row['build_error'] or '首版产出后，由gpt-5.6-sol medium集中复核。')}</p><ul>{issue_html}</ul>
<details><summary>提取与回填详情</summary><pre>{escape(json.dumps({'actions':row['actions'],'incomplete':row['incomplete'],'tasks':tasks,'api_errors':row['api_errors']},ensure_ascii=False,indent=2))}</pre></details></article>''')
    def med(key):
        values=[c[key] for c in cases if c[key] is not None];return round(median(values),3) if values else None
    tasks=[t for c in cases for t in c['tasks']]
    summary={'total':len(cases),'rendered':sum(c['built'] for c in cases),
        'reviews_completed':sum(c['review_state']=='completed' for c in cases),'statuses':dict(Counter(c['status'] for c in cases)),
        'tasks_submitted':sum(bool(t['task_id']) for t in tasks),'tasks_done':sum(t['status']=='done' for t in tasks),
        'targets_requested':sum(t['requested'] for t in tasks),'targets_returned':sum(t['returned'] for t in tasks),
        'api_errors':sum(len(c['api_errors']) for c in cases),
        'median_seconds':{k:med(k) for k in ['build_seconds','prepare_and_build_seconds','api_seconds','review_seconds']},'cases':cases}
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    note='沿用本轮冻结analysis与bindings，真实请求360（每边外扩10%），一次回填合成。没有重新分析或二轮修图。照片内容变化是客户素材替换。分析阶段与本次制作分开计时；各项中位数不能相加。'
    lines=['# 统一相框后：本轮解析的真实360首版','',note,'',f"已出图 {summary['rendered']}/{len(cases)}；指定模型已完成复核 {summary['reviews_completed']}。",'',f"实际API任务 {summary['tasks_submitted']}，完成 {summary['tasks_done']}；请求目标 {summary['targets_requested']}，返回 {summary['targets_returned']}。",'',f"时延中位数（秒）：{summary['median_seconds']}",'',
        '分析批次前4例读取允许复用素材的旧句，后续12例读取用户运行中确认的新句；本次原样沿用各例冻结绑定，不重新选素材。相框归属规则一致。', '',
        '| 样本 | build秒 | API秒 | 复核秒 | 状态 |','|---|---:|---:|---:|---|']
    for c in cases:lines.append(f"| {c['name']} | {c['build_seconds']} | {c['api_seconds']} | {c['review_seconds']} | {c['status']} |")
    for c in cases:lines+=['','## '+c['name'],'',str(c['summary'] or c['build_error'] or c['status'])]+['- '+str(x) for x in c['issues']]
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    options='<option value="all">全部样本</option>'+''.join(f'<option value="{escape(n)}">{escape(n)}</option>' for n in manifest['names'])
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>统一相框 · 本轮360首版</title>
<style>body{{margin:0;background:#eef1f5;font:16px/1.6 system-ui;color:#243041}}main{{max-width:1450px;margin:auto;padding:24px}}article,header{{background:white;border-radius:12px;padding:20px;margin-bottom:20px}}h1{{margin:0}}h2 small{{font-size:14px;color:#657387}}.images{{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}}figure{{margin:0}}img{{width:100%;display:block}}a{{color:#1668b2}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}nav{{position:sticky;top:0;background:#eef1f5;padding:12px;z-index:2}}select{{padding:8px;font:inherit}}@media(max-width:750px){{main{{padding:10px}}.images{{grid-template-columns:1fr}}}}</style>
<main><header><h1>统一相框后的第一次360回填</h1><p>{note}</p><p><b>已出图 {summary['rendered']}/{len(cases)} · 已复核 {summary['reviews_completed']}/{len(cases)} · API完成 {summary['tasks_done']}/{summary['tasks_submitted']}</b></p>
<p>视觉复核：Codex Agent SDK · gpt-5.6-sol / medium。没有问题清单时不代表已通过，需看状态。</p><p>{link(source/'index.html','本轮解析对照')} · {link(root/'REPORT.md','制作报告')} · {link(root/'summary.json','完整数据')}</p></header>
<nav><select aria-label="选择样本" onchange="document.querySelectorAll('article').forEach(x=>x.hidden=this.value!=='all'&&x.dataset.name!==this.value)">{options}</select></nav>{''.join(cards)}</main></html>'''
    (root/'index.html').write_text(page,encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'},ensure_ascii=False))


if __name__=='__main__':main()
