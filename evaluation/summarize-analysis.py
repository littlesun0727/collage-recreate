"""Read-only analysis experiment report. Never creates or repairs model boxes."""
import argparse
import base64
import io
import json
import re
import hashlib
from pathlib import Path
from statistics import median
from html import escape
from PIL import Image


def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def optional(p):return read(p) if Path(p).exists() else {}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def case_label(c):
    suffix='（旧错误样本）' if c['imported_analysis'] else '（1536对照）' if c['max_edge'] else '（原尺寸传图修复后）' if c.get('image_transport')=='tool-original-v1' else '（原基线）'
    return c['name']+suffix


def image_headers(value,where=''):
    if isinstance(value,dict):
        for key,item in value.items():yield from image_headers(item,where+'/'+key)
    elif isinstance(value,list):
        for i,item in enumerate(value):yield from image_headers(item,where+'/'+str(i))
    elif isinstance(value,str) and value.startswith('data:image/'):
        try:
            with Image.open(io.BytesIO(base64.b64decode(value.split(',',1)[1]))) as im:yield {'field':where,'size':list(im.size)}
        except (ValueError,OSError):pass


def session_evidence(thread_id):
    evidence={'model_contexts':[],'images':[],'tool_counts':{}}
    if not thread_id:return evidence
    for file in Path('C:/Users/admin/.codex/sessions').glob('**/*'+thread_id+'.jsonl'):
        evidence['session_file']=str(file)
        for line in file.open(encoding='utf-8'):
            row=json.loads(line);p=row.get('payload',{})
            if row.get('type')=='turn_context':evidence['model_contexts'].append({k:p.get(k) for k in ['model','effort']})
            if row.get('type')!='response_item' or p.get('type')=='reasoning':continue
            if p.get('type')=='custom_tool_call':
                for name in re.findall(r'tools\.([a-zA-Z_]+)',p.get('input','')):evidence['tool_counts'][name]=evidence['tool_counts'].get(name,0)+1
            for image in image_headers(p):evidence['images'].append({'at':row.get('timestamp'),'response_type':p.get('type'),**image})
    return evidence


def main():
    parser=argparse.ArgumentParser();parser.add_argument('roots',nargs='+');parser.add_argument('--output',required=True);a=parser.parse_args();cases=[]
    for root in map(Path,a.roots):
        manifest=read(root/'manifest.json')
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            run=directory/'analysis'
            if not (run/'input.json').exists():continue
            inputs=read(run/'input.json');analysis=optional(run/'analysis-first.json');self_check=optional(directory/'self-check.json');review=optional(directory/'independent-review.json')
            analyst=optional(directory/'analyst-controller/state.json');critic=optional(directory/'critic-controller/state.json')
            source_state=optional(Path(analyst['source']).parent.parent/'controller/state.json') if analyst.get('status')=='imported' else {}
            av=session_evidence(analyst.get('thread_id') or source_state.get('thread_id'));cv=session_evidence(critic.get('thread_id'))
            ids={o['id'] for o in analysis.get('objects',[])};errors=review.get('errors',[]);flags=[]
            if analysis and sha(run/'analysis.json')!=sha(run/'analysis-first.json'):flags.append('Analysis changed after freeze')
            if review and set(review.get('checked_ids',[]))!=ids:flags.append('Critic checked_ids does not exactly cover objects')
            if any(e.get('id') not in ids for e in errors):flags.append('Critic contains unknown object IDs')
            initial_size=next((i['size'] for i in av['images'] if i['response_type']=='message'),None)
            image_size=av['images'][0]['size'] if av['images'] else None
            transport=manifest.get('image_transport',{}).get('analyst','sdk-local-image-baseline')
            if transport=='tool-original-v1' and analyst.get('status')=='completed':
                if initial_size:flags.append('Unexpected initial image attachment in original transport')
                if image_size!=inputs['reference_size']:flags.append('First visual input missing or dimensions do not match analysis canvas')
            c={'name':directory.name,'root':str(root),'max_edge':manifest['max_edge'],'source_size':inputs['original_size'],'analysis_size':inputs['reference_size'],'actual_initial_image_size':image_size,'image_was_resized':image_size!=inputs['reference_size'] if image_size else None,'analyst_status':analyst.get('status'),'critic_status':critic.get('status'),'analyst_seconds':analyst.get('elapsed_seconds'),'critic_seconds':critic.get('elapsed_seconds'),'object_count':len(ids),'self_verdict':self_check.get('verdict'),'self_issues':self_check.get('issues',[]),'self_coordinate_basis':self_check.get('coordinate_basis'),'critic_verdict':review.get('verdict'),'errors':errors,'major_count':sum(e.get('severity')=='major' for e in errors),'pattern':review.get('pattern'),'hypotheses':review.get('hypotheses',[]),'flags':flags,'analyst_evidence':av,'critic_evidence':cv,'boxes':str(run/'previews/boxes-first.png'),'original_boxes':str(run/'previews/boxes-original.png') if (run/'previews/boxes-original.png').exists() else None}
            cases.append(c)
            c['actual_initial_image_size']=initial_size
            c['actual_first_visual_size']=image_size
            c['image_transport']=transport
            c['imported_analysis']=analyst.get('status')=='imported'
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True);(out/'summary.json').write_text(json.dumps({'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Analysis 批量诊断','','分析与独立复核均由 Codex Agent SDK + gpt-5.6-sol / medium 完成。开发主控没有代写 bbox。独立复核仍是模型判断，建议框不是人工真值；本表不能解释成客观定位准确率。','','| 样图 | 分析画布 | 实际输入图 | 分析秒 | 自检 | 独立复核 | 重大问题数 |','|---|---|---|---:|---|---|---:|']
    for c in cases:
        lines.append(f"| {case_label(c)} | {c['analysis_size']} | {c['actual_first_visual_size']} | {round(c['analyst_seconds']) if c['analyst_seconds'] else '-'} | {c['self_verdict'] or c['analyst_status']} | {c['critic_verdict'] or c['critic_status']} | {c['major_count']} |")
    lines+=['','实际输入图尺寸来自 SDK 会话中图片字节的解码头；不是磁盘文件尺寸，也不是从模型回答推测。正式制作、绑定、抠图和生图均未运行。','']
    cards=[]
    for c in cases:
        label=case_label(c)
        lines += ['## '+label,'',f"[首次叠框]({c['boxes'].replace(chr(92),'/')})",'',str(c['pattern'] or '独立复核未完成'),'']
        for e in c['errors']:lines.append(f"- {e.get('id')} [{e.get('severity')} / {e.get('confidence')}]: {e.get('reason')}；原框 {e.get('observed_bbox')}，复核估计 {e.get('suggested_bbox')}")
        lines+=['','坐标自述：'+str(c['self_coordinate_basis']),'']
        image=Path(c['original_boxes'] or c['boxes']);cards.append('<article><h2>'+escape(label)+'</h2><p>'+escape(str(c['critic_verdict'] or c['critic_status']))+'</p>'+(f'<a href="{image.as_uri()}"><img loading="lazy" src="{image.as_uri()}"></a>' if image.exists() else '')+'<p>'+escape(str(c['pattern'] or ''))+'</p><ul>'+''.join('<li>'+escape(str(e.get('id'))+': '+str(e.get('reason')))+'</li>' for e in c['errors'])+'</ul></article>')
    (out/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    (out/'gallery.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Analysis 诊断</title><style>body{font:16px/1.6 system-ui;margin:24px;background:#eee}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:20px}article{background:white;padding:18px}img{width:100%;max-height:900px;object-fit:contain}</style><h1>Analysis 独立复核</h1><p>所有分析与视觉复核：gpt-5.6-sol / medium。首次坐标冻结；仅诊断，不制作。</p><main>'+''.join(cards)+'</main></html>',encoding='utf-8')
    print(json.dumps({'cases':len(cases),'analyst_completed':sum(c['analyst_status']=='completed' for c in cases),'critic_completed':sum(c['critic_status']=='completed' for c in cases),'resized_inputs':sum(c['image_was_resized'] is True for c in cases)},ensure_ascii=False))


if __name__=='__main__':main()
