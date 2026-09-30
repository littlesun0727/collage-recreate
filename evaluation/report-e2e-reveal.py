"""Live E2E report: actual API receipts and agent timings, no visual scoring by the harness."""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from html import escape
from pathlib import Path
from statistics import median
from urllib.parse import quote
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import save

def read(p):
    try:return json.loads(Path(p).read_text(encoding='utf-8-sig'))
    except (FileNotFoundError,json.JSONDecodeError):return {}
def rows(p):
    output=[]
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            try:output.append(json.loads(line))
            except json.JSONDecodeError:pass
    return output
def dt(s):return datetime.fromisoformat(s.replace('Z','+00:00'))
def gap(a,b):return round((dt(b)-dt(a)).total_seconds(),3) if a and b else None
def med(values):
    values=[x for x in values if x is not None];return round(median(values),3) if values else None

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);args=p.parse_args();root=Path(args.root)
    manifest=read(root/'manifest.json');cases=[];cards=[]
    def rel(path):return quote(os.path.relpath(path,root).replace('\\','/'),safe='/')
    def link(path,label):return f'<a target="_blank" href="{rel(path)}">{escape(label)}</a>'
    for name in manifest['names']:
        case=root/Path(name).stem;run=case/'run';state=read(case/'controller/state.json');result=read(run/'result.json');analysis=read(run/'analysis.json');assessment=read(case/'evaluation.json');index=read(run/'assets/reveal/index.json')
        events=rows(run/'events.jsonl');first=next((e for e in events if e['event']=='build_finished'),{})
        build_starts=[e for e in events if e['event']=='build_started' and (not first or e['at']<=first['at'])];build_start=build_starts[-1] if build_starts else {}
        api_events=[e for e in events if e['event'] in ['reveal_request_finished','reveal_request_failed']]
        api_seconds=round(sum(e.get('elapsed_seconds',0) for e in api_events if build_start['at']<=e['at']<=first['at']),3) if first else None
        tasks=[]
        for folder in sorted((run/'assets/reveal/api').glob('batch_*')):
            meta=read(folder/'request_meta.json');submit=read(folder/'submit_response.json');query=read(folder/'query_response.json');timing=read(folder/'timing.json')
            tasks.append({'path':str(folder),'task_id':submit.get('task_id'),'submit_status':submit.get('response_status'),'status':query.get('status','submission_unknown' if (folder/'submission_started.json').exists() else 'not_submitted'),
                          'requested':len(meta.get('objects',[])),'returned':len(query.get('output',{}).get('boxes_mapping_index',[])),
                          'service_seconds':query.get('generation_time'),'timing':timing,'objects':meta.get('objects',[])})
        action_counts=Counter(x['action'] for x in index.get('records',[]));status=result.get('status') or state.get('status','queued')
        row={'name':case.name,'file':name,'state':state.get('status','queued'),'thread_id':state.get('thread_id'),'sdk_run_calls':state.get('sdk_run_calls'),
             'status':status,'review_registered':bool(result.get('visual_review')),'first_seconds':gap(state.get('started_at'),first.get('at')),
             'total_seconds':state.get('elapsed_seconds'),'prebuild_seconds':gap(state.get('started_at'),build_start.get('at')),
             'build_seconds':first.get('elapsed_seconds'),'api_wall_seconds':api_seconds,'local_build_seconds':round(first['elapsed_seconds']-api_seconds,3) if first else None,
             'postbuild_seconds':gap(first.get('at'),state.get('finished_at')),'tasks':tasks,'api_errors':[e for e in api_events if e['event']=='reveal_request_failed'],
             'failed_builds':[e for e in events if e['event']=='build_failed'],'successful_builds':sum(e['event']=='build_finished' for e in events),
             'session_api_wall_seconds':round(sum(e.get('elapsed_seconds',0) for e in api_events),3),
             'objects':len(analysis.get('objects',[])),'object_kinds':dict(Counter(o['kind'] for o in analysis.get('objects',[]))),
             'customer_photos_verified':result.get('customer_photos_verified'),'photo_visible_fractions':result.get('photo_visible_fractions',{}),
             'actions':dict(action_counts),'incomplete':result.get('incomplete_objects',[]),'issues':assessment.get('issues',[]),'friction':assessment.get('skill_friction',[]),
             'strengths':assessment.get('strengths',[]),'summary':(result.get('visual_review') or {}).get('summary'),'extraction_sheets':result.get('extraction_sheets',[])}
        cases.append(row)
        titles={'verified':'首版通过','needs_changes':'需要修改','preview':'待复核','running':'执行中','queued':'排队中'}
        figures=[]
        for label,image in [('参考图',run/'prepared/reference.png'),('本次端到端首版',run/'previews/first.png')]:
            if image.exists():figures.append(f'<figure><figcaption>{label}</figcaption><a target="_blank" href="{rel(image)}"><img loading="lazy" src="{rel(image)}"></a></figure>')
        extra=[]
        for label,path in [('analysis',run/'analysis.json'),('bindings',run/'bindings.json'),('分析框',run/'previews/analysis-boxes.png'),('扩框检查图',case/'request-boxes.png'),('请求框JSON',run/'assets/reveal/request-targets.json'),('回填记录',run/'assets/reveal/index.json'),('review',run/'review.json')]:
            if path.exists():extra.append(link(path,label))
        extra += [link(Path(path),f'素材检查表{i+1}') for i,path in enumerate(row['extraction_sheets'])]
        def bullet(items):return '<ul>'+''.join('<li>'+escape(str(x))+'</li>' for x in items)+'</ul>' if items else '<p>暂无记录。</p>'
        cards.append(f'''<article data-status="{status}"><h2>{escape(case.name)} <span class="{status}">{titles.get(status,status)}</span></h2>
<p>首版 {row['first_seconds'] if row['first_seconds'] is not None else '—'}s · 全程 {row['total_seconds'] or '—'}s · build {row['build_seconds'] or '—'}s · API {api_seconds}s · 实际提交 {sum(bool(t['task_id']) for t in tasks)} 次</p>
<div class="images">{''.join(figures) or '<p>正在准备。</p>'}</div><p>{' · '.join(extra)}</p>
<details open><summary>指定模型视觉结论</summary><p>{escape(row['summary'] or '尚未完成复核。')}</p>{bullet(row['issues'])}</details>
<details><summary>模型记录的优点与操作障碍</summary>{bullet(row['strengths'])}{bullet(row['friction'])}</details>
<details><summary>接口和回填</summary><pre>{escape(json.dumps({'actions':row['actions'],'incomplete':row['incomplete'],'api_tasks':[{k:v for k,v in t.items() if k not in ['objects','path']} for t in tasks]},ensure_ascii=False,indent=2))}</pre></details></article>''')
    all_tasks=[t for c in cases for t in c['tasks']];counts=Counter(c['status'] for c in cases)
    summary={'case_count':len(cases),'completed':sum(c['state']=='completed' for c in cases),'exported':sum(c['first_seconds'] is not None for c in cases),'statuses':dict(counts),
             'model':manifest['model'],'effort':manifest['effort'],'padding_per_edge':.1,'new_analysis':True,
             'submitted_tasks':sum(bool(t['task_id']) for t in all_tasks),'done_tasks':sum(t['status']=='done' for t in all_tasks),'request_targets':sum(t['requested'] for t in all_tasks),'returned_targets':sum(t['returned'] for t in all_tasks),
             'api_failures':sum(len(c['api_errors']) for c in cases),
             'medians_seconds':{k:med(c[k] for c in cases) for k in ['first_seconds','total_seconds','prebuild_seconds','build_seconds','api_wall_seconds','local_build_seconds','postbuild_seconds']},
             'service_task_median_seconds':med(t['service_seconds'] for t in all_tasks if t['status']=='done'),'cases':cases}
    timing_labels={'prebuild_seconds':'出图前：准备、分析、绑定与工具往返','build_seconds':'制作首版（包含API）','api_wall_seconds':'其中：360提交、等待、下载','local_build_seconds':'其中：本地处理与合成','postbuild_seconds':'出图后：看图、复核与登记','first_seconds':'启动到首版','total_seconds':'启动到复核结束'}
    timing_stats={}
    for key,label in timing_labels.items():
        values=[c[key] for c in cases if c[key] is not None]
        timing_stats[key]={'label':label,'n':len(values),'median':med(values),'min':min(values) if values else None,'max':max(values) if values else None}
    summary['timing_statistics_seconds']=timing_stats
    save(root/'summary.json',summary)
    def duration(value):
        if value is None:return '—'
        seconds=round(value);return f'{seconds//60}分{seconds%60:02d}秒'
    timing_table='<div style="overflow-x:auto"><table style="width:100%;text-align:left"><thead><tr><th>阶段</th><th>样本数</th><th>中位数</th><th>范围</th></tr></thead><tbody>'+''.join(f"<tr><td>{v['label']}</td><td>{v['n']}</td><td>{duration(v['median'])}</td><td>{duration(v['min'])}–{duration(v['max'])}</td></tr>" for v in timing_stats.values())+'</tbody></table></div>'
    timing_note='单张任务耗时，不含批量排队或二轮修图；各列独立取中位数，不能相加。API包含网络和轮询等待；未调用API的简单样本计0秒。分析和复核区间包含工具读写，不是纯模型推理时间。'
    timing_lines=['# 端到端时延实测','',timing_note,'','| 阶段 | 样本数 | 中位数 | 最短 | 最长 |','|---|---:|---:|---:|---:|']
    timing_lines += [f"| {v['label']} | {v['n']} | {duration(v['median'])} | {duration(v['min'])} | {duration(v['max'])} |" for v in timing_stats.values()]
    timing_lines += ['','| 样本 | 出图前 | 制作首版 | 其中API | 出图后 | 首版 | 全程 |','|---|---:|---:|---:|---:|---:|---:|']
    timing_lines += ['| '+c['name']+' | '+' | '.join(duration(c[k]) for k in ['prebuild_seconds','build_seconds','api_wall_seconds','postbuild_seconds','first_seconds','total_seconds'])+' |' for c in cases]
    (root/'TIMING.md').write_text('\n'.join(timing_lines)+'\n',encoding='utf-8')
    m=summary['medians_seconds'];lines=['# 10%扩框：真实端到端首版测试','',f"模型：{manifest['model']} / {manifest['effort']}；从零分析/绑定，实际调用360，冻结首版后由同一SDK主控看图复核。",'',f"完成{summary['completed']}/{len(cases)}；已出图{summary['exported']}；视觉状态{dict(counts)}。实际提交{summary['submitted_tasks']}个任务，done {summary['done_tasks']}，请求{summary['request_targets']}个目标，服务返回{summary['returned_targets']}个映射目标。",'',f"耗时中位数（秒）：{m}",'',
    '首版=SDK启动到首次build完成；全程含整图复核。prebuild含准备、模型看图/分析/写JSON及工具往返，不能称纯推理。build内API包含提交、查询/轮询等待、下载；local_build为build减去API区间。postbuild包含看图、写review、工具重试和会话收尾。各列中位数不可相加当作总中位数。','',
    '这轮重新分析和选图，不是固定bbox的0%/10%双组实验，因此不能把视觉差异全部归因于扩框。扩框正确、API返回成功或照片来源通过，都不替代视觉质量判断。','',
    '| 样本 | 首版秒 | 全程秒 | build秒 | API秒 | 提交/完成 | 结论 |','|---|---:|---:|---:|---:|---|---|']
    for c in cases:lines.append(f"| {c['name']} | {c['first_seconds']} | {c['total_seconds']} | {c['build_seconds']} | {c['api_wall_seconds']} | {sum(bool(t['task_id']) for t in c['tasks'])}/{sum(t['status']=='done' for t in c['tasks'])} | {c['status']} |")
    for c in cases:lines+=['','## '+c['name'],'',str(c['summary'] or c['state'])]+['- '+str(x) for x in c['issues']]
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    page=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v5 · 10%扩框端到端实测</title>
<style>body{{font:16px/1.65 system-ui,sans-serif;color:#202329;background:#eef1f5;margin:0}}main{{max-width:1400px;margin:auto;padding:24px}}article,.intro{{background:white;border-radius:12px;margin:22px 0;padding:22px}}h1{{margin-bottom:8px}}h2{{font-size:21px}}h2 span{{font-size:14px;padding:5px 9px;background:#eef0f5;border-radius:5px}}h2 .verified{{background:#d8f1df}}h2 .needs_changes{{background:#fff0cf}}.images{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}}figure{{margin:0}}figcaption{{font-weight:600;margin:8px 0}}img{{width:100%;height:auto;background:#ddd}}a{{color:#1762b0}}summary{{cursor:pointer;font-weight:600}}details{{margin:12px 0}}pre{{white-space:pre-wrap;font-size:13px}}select{{padding:9px}}@media(max-width:700px){{main{{padding:8px}}article{{padding:12px}}.images{{grid-template-columns:1fr}}}}</style>
<main><h1>v5：每边外扩10% · 真实端到端</h1><div class="intro"><p>每张由 <b>Codex Agent SDK / gpt-5.6-sol / medium</b> 从原参考和客户素材重新分析、绑定、真实请求360、回填、复核。分析JSON保持原框；请求框每边外扩原宽/高10%，照片清窗不跟着扩大。</p>
<p><b>已出图 {summary['exported']}/{len(cases)} · 会话完成 {summary['completed']}/{len(cases)}</b> · 实际提交 {summary['submitted_tasks']} 次 · API完成 {summary['done_tasks']} 次</p><p>视觉状态：{escape(str(dict(counts)))}</p>
<p>中位数：首版 <b>{m['first_seconds']}s</b> · 全程 <b>{m['total_seconds']}s</b> · build {m['build_seconds']}s · build内API {m['api_wall_seconds']}s。未完成任务暂不计入对应耗时；API列按实际已发生区间累计。</p>
<p>这是重新分析的端到端测试，不是0%/10%的严格双组对照。照片内容不同属正常客户替换。点击图片查看原尺寸，打开“扩框检查图”可比较原框与提交框。</p>
<details open><summary>当前时延分布</summary>{timing_table}<p>{timing_note}</p></details>
<p>{link(root/'REPORT.md','报告')} · {link(root/'TIMING.md','逐图耗时')} · {link(root/'summary.json','完整数据')}</p><select onchange="document.querySelectorAll('article').forEach(x=>x.hidden=this.value!=='all'&&x.dataset.status!==this.value)"><option value="all">全部</option><option value="verified">首版通过</option><option value="needs_changes">需要修改</option><option value="preview">待复核</option></select></div>{''.join(cards)}</main></html>'''
    (root/'index.html').write_text(page,encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'},ensure_ascii=False))
if __name__=='__main__':main()
