"""Read-only production checks and an incremental gallery for fresh SDK runs."""
import sys,json,importlib.util
from pathlib import Path
from datetime import datetime
from statistics import median
from collections import Counter
from html import escape
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha
from scene import load_scene,checked_review
from validate import analysis_check,bindings_check
from PIL import Image
spec=importlib.util.spec_from_file_location('evidence',Path(__file__).with_name('summarize-analysis.py'))
evidence=importlib.util.module_from_spec(spec);spec.loader.exec_module(evidence)

def optional(path):return read(path) if path.exists() else {}
def rows(path):
    out=[]
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:out.append(json.loads(line))
            except json.JSONDecodeError:pass
    return out
def timestamp(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def gap(a,b):return round(timestamp(b)-timestamp(a),3) if a and b else None
def union_seconds(intervals):
    end=None;total=0
    for a,b in sorted(intervals):
        total+=max(0,b-max(a,end if end is not None else a));end=max(b,end or b)
    return round(total,3)
def med(values):
    values=[v for v in values if v is not None]
    return round(median(values),2) if values else None

root=Path(sys.argv[1]);manifest=read(root/'manifest.json');cases=[];cards=[];errors=[]
snapshot=Path(manifest['skill'])
for f,h in manifest['skill_files'].items():
    if sha(snapshot/f)!=h:errors.append('Frozen skill changed: '+f)
for name in manifest['names']:
    case=root/Path(name).stem;run=case/'run';state=optional(case/'controller/state.json')
    result=optional(run/'result.json');evaluation=optional(case/'evaluation.json');plan=optional(run/'assets/reveal/request-plan.json')
    events=rows(run/'events.jsonl');first=next((e for e in events if e['event']=='build_finished'),{})
    starts=[e for e in events if e['event']=='build_started' and (not first or e['at']<first['at'])]
    start=starts[-1] if starts else {};tasks=[];intervals=[];flags=[]
    for folder in sorted((run/'assets/reveal/grouped').glob('batch_*')):
        meta=optional(folder/'request_meta.json');submit=optional(folder/'submit_response.json');query=optional(folder/'query_response.json');timing=optional(folder/'timing.json')
        raw=next(folder.glob('layers_aug_*.png'),None)
        size=list(Image.open(raw).size) if raw else None
        task={'task_id':submit.get('task_id'),'status':query.get('status'),'boxes':len(meta.get('objects',[])),
              'returned':len(query.get('output',{}).get('boxes_mapping_index',[])),'raw_size':size,'timing':timing}
        tasks.append(task)
        if timing.get('started_at') and timing.get('updated_at') and first:
            a=max(timestamp(timing['started_at']),timestamp(start['at']));b=min(timestamp(timing['updated_at']),timestamp(first['at']))
            if b>a:intervals.append((a,b))
        if task['boxes']>20:flags.append('More than 20 request boxes')
    submitted=sum(bool(t['task_id']) for t in tasks)
    if submitted>3 or len(plan.get('batches',[]))>3:flags.append('More than 3 groups/submissions')
    if plan and plan.get('mode')!='grouped':flags.append('Unexpected extraction mode')
    screening=optional(run/'screening.json')
    if (run/'recovery.json').exists() or list((run/'assets/repairs').glob('*/job.json')):flags.append('Unexpected pixel repair')
    cfg=optional(run/'reveal-config.json')
    if cfg.get('cache'):flags.append('Unexpected old extraction cache')
    ep=case/'controller/session-evidence.json'
    ev=optional(ep) if state.get('status')=='completed' else {}
    if not ev.get('model_contexts'):
        ev=evidence.session_evidence(state.get('thread_id'))
        if state:save(ep,ev)
    if ev.get('model_contexts') and any(c!={'model':'gpt-5.6-sol','effort':'medium'} for c in ev['model_contexts']):flags.append('Actual model/effort mismatch')
    if state.get('status')=='completed':
        try:
            inputs=read(run/'input.json');analysis=read(run/'analysis.json');scene=load_scene(run)
            analysis_check(analysis,inputs);bindings_check(read(run/'bindings.json'),analysis,read(run/'prepared/catalog.json'))
            checked_review(run,run/'review.json')
            if not result.get('visual_review'):flags.append('Review not registered')
            if result.get('final_sha256')!=sha(run/'final.png') or result.get('scene_sha256')!=sha(run/'scene.json'):flags.append('Current render hash mismatch')
            if result.get('renders_verified') and result.get('incomplete_objects'):flags.append('Incorrect pass with missing objects')
            if not ev.get('model_contexts') or inputs['reference_size'] not in [x['size'] for x in ev.get('images',[])]:flags.append('Missing actual model/original image evidence')
            if evaluation.get('model')!='gpt-5.6-sol' or evaluation.get('effort')!='medium':flags.append('Evaluation model label mismatch')
        except Exception as exc:flags.append(str(exc))
    api=union_seconds(intervals) if first else None
    timing={'to_first':gap(state.get('started_at'),first.get('at')),'prebuild':gap(state.get('started_at'),start.get('at')),
            'build':first.get('elapsed_seconds'),'api_wall':api,'local_build':round(first['elapsed_seconds']-api,3) if first else None,
            'screen':next((e['elapsed_seconds'] for e in events if e['event']=='screen_finished'),None),'total':state.get('elapsed_seconds')}
    c={'name':case.name,'state':state.get('status','queued'),'thread_id':state.get('thread_id'),'model_contexts':ev.get('model_contexts',[]),
       'timing_seconds':timing,'groups':len(plan.get('batches',[])),'gains':[b['estimated_sampling_gain'] for b in plan.get('batches',[])],
       'crops':[b['crop_box'] for b in plan.get('batches',[])],'tasks':tasks,'screen_actions':dict(Counter(d['action'] for d in screening.get('decisions',[]))),
       'evaluation':evaluation,'incomplete_objects':result.get('incomplete_objects',[]),'gate':result.get('asset_gate_summary'),'errors':flags}
    cases.append(c);errors += [case.name+': '+f for f in flags]
    figures=[]
    for label,p in [('参考图',run/'prepared/reference.png'),('新分析＋新分组首版',run/'previews/first.png'),('一次筛选后当前图',run/'final.png')]:
        if p.exists():figures.append(f'<figure><figcaption>{label}</figcaption><a href="{p.as_uri()}"><img loading="lazy" src="{p.as_uri()}"></a></figure>')
    links=[]
    for label,p in [('新analysis',run/'analysis.json'),('新bindings',run/'bindings.json'),('分组预览',run/'assets/reveal/groups.png'),('筛选决定',run/'screening.json'),('模型评价',case/'evaluation.json'),('当前scene',run/'scene.json')]:
        if p.exists():links.append(f'<a href="{p.as_uri()}">{label}</a>')
    cards.append('<article><h2>'+escape(case.name)+'</h2><p>'+escape(c['state']+' / '+evaluation.get('visual_verdict','未复核'))+'</p><p>'+escape(json.dumps(timing,ensure_ascii=False))+'</p><div class="images">'+''.join(figures)+'</div><p>'+' · '.join(links)+'</p><details open><summary>指定模型的观察</summary><pre>'+escape(json.dumps(evaluation,ensure_ascii=False,indent=2))+'</pre></details></article>')
summary={'model':'gpt-5.6-sol','effort':'medium','cases':len(cases),'completed':sum(c['state']=='completed' for c in cases),
         'exported':sum(c['timing_seconds']['to_first'] is not None for c in cases),'states':dict(Counter(c['state'] for c in cases)),
         'visual_verdicts':dict(Counter(c['evaluation'].get('visual_verdict') for c in cases if c['evaluation'])),
         'submitted_tasks':sum(bool(t['task_id']) for c in cases for t in c['tasks']),
         'screen_actions':dict(sum((Counter(c['screen_actions']) for c in cases),Counter())),
         'median_seconds':{k:med(c['timing_seconds'][k] for c in cases) for k in ['to_first','prebuild','build','api_wall','local_build','screen','total']},'errors':errors}
save(root/'verification.json',{**summary,'items':cases})
note='每例从头分析、绑定、真实分组提取，由指定SDK模型独立筛选与复核。无旧analysis或提取缓存；默认不做OCR/LaMa。采样倍率为程序估算，不等于视觉清晰度评分。模型观察不是人工真值。'
if (root/'fixes-after-snapshot.json').exists():
    note+=' 当前源码另已修正screen不能调整已有local图形的问题，并通过140项程序测试；本页16例保持原冻结快照，未把这一修正混入模型效果。'
(root/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>分组提取与轻量筛选实测</title><style>body{font:16px/1.6 system-ui;background:#eee;margin:24px}article{background:white;padding:20px;margin:24px 0}.images{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}figure{margin:0}img{width:100%}pre{white-space:pre-wrap}</style><h1>新分析＋最多三组提取＋一次screen</h1><p>'+note+'</p><pre>'+escape(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre>'+''.join(cards),encoding='utf-8')
lines=['# 分组提取与轻量筛选实测','',note,'',f'完成 {summary["completed"]}/{summary["cases"]}；已出首版 {summary["exported"]}/{summary["cases"]}；实际提交 {summary["submitted_tasks"]} 次。',
       '',f'指定模型整图结论：{summary["visual_verdicts"]}。',f'耗时中位数（秒）：{summary["median_seconds"]}。','',
       'to_first含准备/看图/分析/绑定/提取/合成；prebuild不是纯模型推理。api_wall为API任务时间区间的并集，避免并行任务重复加总；local_build为build扣除该区间。screen只计程序操作，模型判断还在total中。重新分析导致布局/选图也可能不同，不能把全部变化归因于分组。',
       '', '| 样本 | 执行 | 首版秒 | screen秒 | 全程秒 | 分组数 | 视觉结论 |','|---|---|---:|---:|---:|---:|---|']
for c in cases:
    t=c['timing_seconds'];lines.append(f'| {c["name"]} | {c["state"]} | {t["to_first"]} | {t["screen"]} | {t["total"]} | {c["groups"]} | {c["evaluation"].get("visual_verdict","-")} |')
if errors:lines += ['', '核验异常：', *['- '+e for e in errors]]
(root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))
