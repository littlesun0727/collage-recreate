"""Compare frozen model analyses without rewriting or grading their geometry."""
import argparse
import json
import os
from pathlib import Path
from collections import Counter
from datetime import datetime
from statistics import median
from urllib.parse import quote

def read(path):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (FileNotFoundError,json.JSONDecodeError):return {}
def gap(a,b):
    return round((datetime.fromisoformat(b.replace('Z','+00:00'))-datetime.fromisoformat(a.replace('Z','+00:00'))).total_seconds(),3) if a and b else None
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--baseline',required=True);args=p.parse_args()
    root=Path(args.root);baseline=Path(args.baseline);manifest=read(root/'manifest.json');cases=[]
    def url(path):return quote(os.path.relpath(path,root).replace('\\','/'),safe='/') if path.exists() else None
    def data(run,frozen):
        path=run/('analysis-first.json' if frozen else 'analysis.json');a=read(path)
        if not a:return None
        return {'analysis':a,'counts':dict(Counter(o['kind'] for o in a['objects'])),'objects':len(a['objects']),
                'json_url':url(path),'bindings_url':url(run/('bindings-first.json' if frozen else 'bindings.json')),
                'reference_url':url(run/'prepared/reference.png'),'boxes_url':url(run/'previews'/('boxes-first.png' if frozen else 'analysis-boxes.png'))}
    for name in manifest['names']:
        case=root/Path(name).stem;run=case/'run';state=read(case/'controller/state.json');frozen=read(run/'analysis-freeze.json')
        old=data(baseline/case.name/'run',False);new=data(run,True)
        review_path=case/'analysis-review.json'
        if not review_path.exists() and (run/'analysis-review.json').exists():review_path=run/'analysis-review.json'
        review=read(review_path);alternate=review_path.parent==run
        effective=state.get('status','queued')
        if effective=='incomplete' and frozen and review and alternate:effective='completed'
        cases.append({'name':case.name,'state':effective,'controller_state':state.get('status','queued'),'alternate_review_path':alternate,'old':old,'new':new,'review':review,
                      'review_url':url(review_path),'freeze_seconds':gap(state.get('started_at'),frozen.get('frozen_at')),
                      'session_seconds':state.get('elapsed_seconds'),'thread_id':state.get('thread_id')})
    def med(key):
        values=[c[key] for c in cases if c[key] is not None];return round(median(values),3) if values else None
    pairs=[c for c in cases if c['old'] and c['new']]
    kinds=['photo','overlay','text','background']
    summary={'model':manifest['model'],'effort':manifest['effort'],'total':len(cases),'completed':sum(c['state']=='completed' for c in cases),
             'frozen':sum(bool(c['new']) for c in cases),'self_verdicts':dict(Counter(c['review'].get('verdict','pending') for c in cases)),
             'paired_objects':{'old':sum(c['old']['objects'] for c in pairs),'new':sum(c['new']['objects'] for c in pairs)},
             'paired_kinds':{k:{'old':sum(c['old']['counts'].get(k,0) for c in pairs),'new':sum(c['new']['counts'].get(k,0) for c in pairs)} for k in kinds},
             'freeze_median_seconds':med('freeze_seconds'),'session_median_seconds':med('session_seconds'),
             'alternate_review_paths':[c['name'] for c in cases if c['alternate_review_path']],'cases':cases}
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# 内容归属与完整单元：仅解析对照','', '[本轮检查与已知问题](INSPECTION_NOTES.md)。这部分独立于模型自检，未改写冻结分析。','',
           '新分析全部由Codex Agent SDK / gpt-5.6-sol / medium从原参考与同一客户素材目录重新编写；旧分析只用于报告，未提供给新分析会话。没有360、抠图、生成或回填。',
           '',f"已冻结{summary['frozen']}/{len(cases)}，会话完成{summary['completed']}。成对对象数{summary['paired_objects']}；类型数{summary['paired_kinds']}。",
           '',f"冻结JSON中位数{med('freeze_seconds')}秒；包括同主控自检的会话中位数{med('session_seconds')}秒。",
           '', '对象数减少不是准确率提升。新旧每张各一次采样，旧轮任务包含后续制作，本轮只解析；不能把所有差异归因于规范。自检是同一模型的判断，不是独立视觉真值。',
           '', f"自检输出路径差异：{summary['alternate_review_paths']}。这些案例由模型写在run/analysis-review.json，报告直接读取原文件；未代写评价、未重跑、未改冻结分析。controller原始状态保留。",
           '', '| 样本 | 总对象旧→新 | 照片槽旧→新 | 装饰旧→新 | 文字旧→新 | 冻结秒 | 自检 |','|---|---:|---:|---:|---:|---:|---|']
    for c in cases:
        old=c['old'] or {};new=c['new'] or {}
        def count(side,k):return side.get('counts',{}).get(k,0) if side else '—'
        lines.append(f"| {c['name']} | {old.get('objects','—')}→{new.get('objects','—')} | {count(old,'photo')}→{count(new,'photo')} | {count(old,'overlay')}→{count(new,'overlay')} | {count(old,'text')}→{count(new,'text')} | {c['freeze_seconds']} | {c['review'].get('verdict',c['state'])} |")
    for c in cases:
        lines+=['','## '+c['name'],'',c['review'].get('summary','尚未完成自检')]
        lines+=['- '+str(i.get('id'))+' ['+i.get('category','')+'] '+i.get('reason','') for i in c['review'].get('issues',[])]
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    template=Path(__file__).with_name('ownership-analysis-gallery.html').read_text(encoding='utf-8')
    payload=json.dumps(summary,ensure_ascii=False).replace('<','\\u003c')
    (root/'index.html').write_text(template.replace('__PAYLOAD__',payload),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='cases'},ensure_ascii=False))
if __name__=='__main__':main()
