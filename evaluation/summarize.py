"""Read-only mechanical evidence aggregation; visual verdict belongs to SDK evaluator."""
import argparse
import json
import re
import hashlib
from pathlib import Path
from datetime import datetime
from statistics import median
from html import escape


def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def dt(x):return datetime.fromisoformat(x.replace('Z','+00:00'))
def rows(p):
    result=[]
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            try:result.append(json.loads(line))
            except json.JSONDecodeError:pass
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('roots',nargs='+');p.add_argument('--output',required=True);a=p.parse_args()
    cases=[]
    for root in map(Path,a.roots):
        for sp in sorted(root.glob('*/controller/state.json')):
            case=sp.parent.parent;state=read(sp);run=case/'run'
            result=read(run/'result.json') if (run/'result.json').exists() else {}
            assessment=read(case/'evaluation.json') if (case/'evaluation.json').exists() else {}
            analysis=read(run/'analysis.json') if (run/'analysis.json').exists() else {}
            events=rows(run/'events.jsonl');sdk=rows(sp.parent/'events.jsonl')
            first=next((e for e in events if e['event']=='build_finished'),None)
            first_s=(dt(first['at'])-dt(state['started_at'])).total_seconds() if first else None
            commands=[e['item'] for e in sdk if e['type']=='item.completed' and e.get('item',{}).get('type')=='command_execution']
            forbidden=[]
            for cmd in commands:
                text=cmd.get('command','')
                if 'collage-recreate-v3' in text or 'collage-recreate-v4' in text:forbidden.append('Other-version command detected')
                if 'workflow.py' in text and ' generate ' in text:forbidden.append('Generation invoked in first-preview test')
            context=[];image_calls=[];tool_counts={}
            if state.get('thread_id'):
                for f in Path('C:/Users/admin/.codex/sessions').glob('**/*'+state['thread_id']+'.jsonl'):
                    for r in rows(f):
                        if r['type']=='turn_context':context.append({k:r['payload'].get(k) for k in ['model','effort']})
                        if r['type']=='response_item' and r.get('payload',{}).get('type')=='custom_tool_call':
                            source=r['payload'].get('input','')
                            for name in re.findall(r'tools\.([a-zA-Z_]+)',source):tool_counts[name]=tool_counts.get(name,0)+1
                            if 'view_image' in source:
                                image_calls.append({'at':r.get('timestamp'),'comparison':'comparison.png' in source,'final':'final.png' in source})
            photo=[r for r in result.get('resources',[]) if r['kind']=='photo']
            c={'name':case.name,'root':str(root),'status':state['status'],'model_evidence':context,'first_preview_seconds':first_s,'total_seconds':state.get('elapsed_seconds'),'command_count':len(commands),'nonzero_commands':sum(c.get('exit_code') not in [0,None] for c in commands),'schema_valid':(run/'scene.json').exists(),'exported':result.get('exported',False),'photo_count':len(photo),'customer_photos_verified':result.get('customer_photos_verified',False),'photo_visible_fractions':result.get('photo_visible_fractions',{}),'incomplete_objects':result.get('incomplete_objects',[]),'visual_verdict':assessment.get('visual_verdict'),'issues':assessment.get('issues',[]),'friction':assessment.get('skill_friction',[]),'generation_candidates':assessment.get('generation_candidates',[]),'protocol_flags':forbidden,'first_preview':str(run/'previews/first.png'),'comparison':str(run/'previews/comparison.png')}
            cases.append(c)
            c['sdk_run_calls']=state.get('sdk_run_calls')
            c['thread_id']=state.get('thread_id')
            c['object_count']=len(analysis.get('objects',[]))
            c['object_counts_by_kind']={kind:sum(o['kind']==kind for o in analysis.get('objects',[])) for kind in ['photo','text','overlay','background']}
            c['reference']=str(run/'prepared/reference.png')
            c['analysis_boxes']=str(run/'previews/analysis-boxes.png')
            c['analysis_size']=analysis.get('reference_size')
            c['features']={
                'rounded_photos':sum(o.get('appearance')=='rounded_photo' or o.get('style',{}).get('corner_radius',0)>0 for o in analysis.get('objects',[])),
                'photo_cards':sum(o.get('appearance')=='polaroid' or 'card' in o.get('style',{}) for o in analysis.get('objects',[])),
                'curves':sum(o.get('style',{}).get('shape')=='curve' for o in analysis.get('objects',[])),
                'shadows':sum('shadow' in o.get('style',{}) or o.get('appearance')=='polaroid' for o in analysis.get('objects',[]))}
            c['review_item_count']=len(result.get('visual_review',{}).get('items',[])) if result.get('visual_review') else None
            c['review_registered']=bool(result.get('visual_review'))
            c['extraction_sheets']=result.get('extraction_sheets',[])
            c['first_matches_final']=hashlib.sha256((run/'previews/first.png').read_bytes()).digest()==hashlib.sha256((run/'final.png').read_bytes()).digest() if (run/'previews/first.png').exists() and (run/'final.png').exists() else None
            c['build_count']=sum(e['event']=='build_finished' for e in events)
            c['build_errors']=[e.get('error') for e in events if e['event']=='build_failed']
            c['session_error']=state.get('error')
            c['extraction_events']=[e for e in events if e['event'].startswith('extraction_')]
            c['extraction_successes']=sum(e['event']=='extraction_finished' for e in events)
            c['extraction_failures']=sum(e['event']=='extraction_failed' for e in events)
            c['tool_counts']=tool_counts
            c['visual_open_evidence']=image_calls
            c['comparison_open_requested']=any(x['comparison'] for x in image_calls)
            marks=[('session_start',state['started_at'])]
            for name in ['prepare_finished','validate_finished','build_finished','review_finished']:
                match=next((e for e in events if e['event']==name),None)
                if match:marks.append((name,match['at']))
            if state.get('finished_at'):marks.append(('session_end',state['finished_at']))
            c['stage_wall_seconds']={b[0]:round((dt(b[1])-dt(a[1])).total_seconds(),3) for a,b in zip(marks,marks[1:])}
            c['tool_seconds']={e['event'].removesuffix('_finished'):e.get('elapsed_seconds') for e in events if e['event'] in ['prepare_finished','validate_finished','build_finished','review_finished'] and 'elapsed_seconds' in e}
            c['extraction_total_seconds']=round(sum(e.get('elapsed_seconds',0) for e in events if e['event'] in ['extraction_finished','extraction_failed']),3)
            c['extracted_resources']=[{'id':r['id'],'mode':r['metadata'].get('extraction_mode'),'quality':r['quality']} for r in result.get('resources',[]) if r['metadata'].get('method')=='extract']
            c['first_preview_seconds_excluding_local_tools']=round(first_s-sum(e.get('elapsed_seconds',0) for e in events if first and e['at']<=first['at'] and e['event'] in ['prepare_finished','validate_finished','build_finished']),3) if first_s else None
    output=Path(a.output);output.mkdir(parents=True,exist_ok=True)
    (output/'summary.json').write_text(json.dumps({'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# v5 首版独立验收','','执行与视觉判断：Codex Agent SDK，gpt-5.6-sol / medium。以下统计由程序读取产物汇总，不由开发主控代评视觉。','', '| 样图 | 首版秒数 | 总秒数 | 照片数 | 来源检查 | 未完成元素 | 模型视觉结论 |','|---|---:|---:|---:|---|---:|---|']
    for c in cases:
        f=round(c['first_preview_seconds']) if c['first_preview_seconds'] is not None else '-';t=round(c['total_seconds']) if c['total_seconds'] is not None else '-'
        lines.append(f"| {c['name']} | {f} | {t} | {c['photo_count']} | {c['customer_photos_verified']} | {len(c['incomplete_objects'])} | {c['visual_verdict'] or c['status']} |")
    times=[c['first_preview_seconds'] for c in cases if c['first_preview_seconds'] is not None]
    if times:lines+=['',f'首版时间中位数：{median(times):.1f} 秒。时间包含模型读取技能、分析、绑定与工具运行；不只是渲染耗时。']
    lines+=['','needs_changes 表示首版仍需调整或生成，不能解读为高相似度成品已通过。来源检查是确定性的客户文件使用检查，不代替人物裁切和视觉效果判断。','']
    for c in cases:
        lines += [f"## {c['name']}",'',f"[完整对照]({c['comparison'].replace(chr(92),'/')}) · [冻结首版]({c['first_preview'].replace(chr(92),'/')})",'','模型记录的问题：']
        lines += ['- '+str(x) for x in c['issues']]
        lines += ['','操作障碍：']+['- '+str(x) for x in c['friction']]+['']
    (output/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    cards=[]
    for c in cases:
        image=Path(c['comparison'])
        cards.append('<article><h2>'+escape(c['name'])+'</h2><p>'+escape(str(c['visual_verdict'] or c['status']))+'</p>'+(f'<a href="{image.as_uri()}"><img loading="lazy" src="{image.as_uri()}" alt="参考与首版对照"></a>' if image.exists() else '<p>暂无成图</p>')+'<ul>'+''.join('<li>'+escape(str(i))+'</li>' for i in c['issues'])+'</ul></article>')
    (output/'gallery.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>v5 首版验收</title><style>body{font:16px/1.6 system-ui;background:#eee;margin:24px;color:#222}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:24px}article{background:white;padding:20px;border-radius:10px}img{width:100%;max-height:800px;object-fit:contain}h2{margin:0}li{margin:8px 0}</style><h1>v5 独立首版验收</h1><p>制作与视觉复核：Codex Agent SDK · gpt-5.6-sol / medium。左侧参考，右侧首版。needs_changes 表示仍需修改，并非成品通过。</p><main>'+''.join(cards)+'</main></html>',encoding='utf-8')
    print(json.dumps({'cases':len(cases),'exported':sum(c['exported'] for c in cases),'first_preview_median_seconds':median(times) if times else None,'output':str(output)},ensure_ascii=False))


if __name__=='__main__':main()
