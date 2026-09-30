"""Verify actual model/session evidence and summarize independent SDK evaluations."""
import sys,json,hashlib,importlib.util
from pathlib import Path
from html import escape
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha
spec=importlib.util.spec_from_file_location('session_evidence',Path(__file__).with_name('summarize-analysis.py'))
evidence=importlib.util.module_from_spec(spec);spec.loader.exec_module(evidence)

root=Path(sys.argv[1]);manifest=read(root/'manifest.json');cases=[];errors=[]
batch_active=any(read(p).get('status')=='running' for p in root.glob('*/controller/state.json'))
snapshot=Path(manifest['skill'])
for name,digest in manifest['skill_files'].items():
    if sha(snapshot/name)!=digest:errors.append('Frozen implementation changed: '+name)
def image(p,label):return f'<figure><figcaption>{escape(label)}</figcaption><a href="{p.as_uri()}"><img loading="lazy" src="{p.as_uri()}"></a></figure>' if p.exists() else ''
cards=[]
for name in manifest['names']:
    folder=root/name;run=folder/'run';state_path=folder/'controller/state.json'
    if not state_path.exists():continue
    state=read(state_path);flags=[];evaluation=read(folder/'evaluation.json') if (folder/'evaluation.json').exists() else {}
    if batch_active and not state.get('thread_id') and state['status']=='failed':state['status']='queued_after_initial_launch_failure'
    result=read(run/'result.json') if (run/'result.json').exists() else {}
    ev=evidence.session_evidence(state.get('thread_id'));save(folder/'controller/session-evidence.json',ev)
    if state['status']=='completed':
        if not ev['model_contexts'] or any(c!={'model':'gpt-5.6-sol','effort':'medium'} for c in ev['model_contexts']):flags.append('Actual model/effort mismatch')
        if not ev['images']:flags.append('No actual image payload evidence')
        for f,digest in state['input_hashes'].items():
            if sha(run/f)!=digest:flags.append('Frozen input changed: '+f)
        if not result.get('visual_review'):flags.append('Review not registered')
        if result.get('scene_sha256')!=sha(run/'scene.json') or result.get('final_sha256')!=sha(run/'final.png'):flags.append('Render output hash mismatch')
        if read(run/'reveal-config.json').get('remote'):flags.append('Remote extraction enabled')
        if evaluation.get('model')!='gpt-5.6-sol' or evaluation.get('effort')!='medium':flags.append('Evaluation model label mismatch')
        if result.get('renders_verified') and result.get('incomplete_objects'):flags.append('Incorrect pass with missing objects')
    events_file=run/'events.jsonl'
    if events_file.exists():
        for row in map(json.loads,events_file.read_text(encoding='utf-8').splitlines()):
            if row.get('event') in ['reveal_submit_started','reveal_request_started']:flags.append('Unexpected API submission')
    repair_records=[]
    reveal_index=run/'assets/reveal/index.json'
    if reveal_index.exists():
        for record in read(reveal_index).get('records',[]):
            for edit in record.get('edits',[]):
                repair_records.append({'id':record.get('id'),**edit})
    workers=[]
    for job_file in (run/'assets/repairs').glob('*/job.json'):
        job=read(job_file);receipt=job_file.with_name('receipt.json')
        workers.append({'kind':job['kind'],'job':str(job_file),'completed':receipt.exists()})
    if state['status'] not in ['completed','running','queued_after_initial_launch_failure']:flags.append('Controller '+state['status'])
    first_path=run/'previews/first.png'
    item={'name':name,'controller':state['status'],'thread_id':state.get('thread_id'),'seconds':state.get('elapsed_seconds'),
        'model_contexts':ev['model_contexts'],'actual_images':len(ev['images']),'evaluation':evaluation,
        'render_status':result.get('status'),'incomplete_objects':result.get('incomplete_objects',[]),
        'gate_summary':result.get('asset_gate_summary'),'errors':flags,
        'repair_records':repair_records,'local_workers':workers,
        'recovery_written':(run/'recovery.json').exists(),
        'first_changed':first_path.exists() and (run/'final.png').exists() and sha(first_path)!=sha(run/'final.png')}
    cases.append(item);errors.extend(name+': '+f for f in flags)
    body='<h2>'+escape(name)+'</h2><p>'+escape(str(evaluation.get('comparison',state['status'])))+' · '+escape(str(evaluation.get('visual_verdict','未完成')))+'</p>'
    body+='<div class="images">'+image(folder/'baseline.png','层级修正基线')+image(first_path,'指定模型执行 build 首版')+image(run/'final.png','指定模型修复后当前图')+'</div>'
    for key,label in [('strengths','模型观察到的改善'),('issues','剩余问题'),('gate_false_positives','模型指出的误拦'),('gate_missed_issues','模型指出的漏检'),('skill_friction','操作障碍')]:
        values=evaluation.get(key,[])
        if values:body+='<details open><summary>'+label+'</summary><pre>'+escape(json.dumps(values,ensure_ascii=False,indent=2))+'</pre></details>'
    body+='<p>'+''.join(f'<a href="{p.as_uri()}">{label}</a> · ' for p,label in [(folder/'evaluation.json','模型评价'),(run/'scene.json','scene.json'),(run/'assets/gate/report.json','门禁记录'),(folder/'controller/session-evidence.json','实际模型证据')] if p.exists())+'</p>'
    cards.append('<article>'+body+'</article>')
summary={'model':'gpt-5.6-sol','effort':'medium','sdk_version':manifest['sdk_version'],
    'cases':len(cases),'completed':sum(c['controller']=='completed' for c in cases),'errors':errors,
    'visual_verdicts':{k:sum(c['evaluation'].get('visual_verdict')==k for c in cases) for k in ['pass','needs_changes','blocked']},
    'comparisons':{k:sum(c['evaluation'].get('comparison')==k for c in cases) for k in ['improved','mixed','unchanged','regressed']},
    'repair_cases':sum(c['recovery_written'] for c in cases),'changed_after_first':sum(c['first_changed'] for c in cases),
    'ocr_worker_jobs':sum(w['kind']=='ocr' and w['completed'] for c in cases for w in c['local_workers']),
    'lama_worker_jobs':sum(w['kind']=='lama' and w['completed'] for c in cases for w in c['local_workers']),
    'text_removal_operations_applied':sum(e.get('status')=='applied' and e['edit']['operation']=='remove_text' for c in cases for e in c['repair_records']),
    'items':cases,'note':'Visual conclusions belong to the designated model and are not human ground truth.'}
save(root/'verification.json',summary)
(root/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>素材门禁 SDK 效果测试</title><style>body{font:16px/1.6 system-ui;margin:24px;background:#eee}article{background:white;padding:18px;margin:24px 0}.images{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}figure{margin:0}img{width:100%;max-height:850px;object-fit:contain}pre{white-space:pre-wrap}h1{font-size:24px}</style><h1>Codex Agent SDK + gpt-5.6-sol / medium</h1><p>冻结分析/绑定与原始提取缓存，由指定模型独立执行build、判断、离线修复和整图复核。开发者未提供逐图修复答案。所有视觉评价来自指定模型，不等于人工真值。</p><p>'+escape(json.dumps({k:v for k,v in summary.items() if k not in ['items','note']},ensure_ascii=False))+'</p>'+''.join(cards)+'</html>',encoding='utf-8')
lines=['# 指定模型的素材门禁效果测试','','[逐图对比](index.html) · [会话与产物核验](verification.json)','',f'Codex Agent SDK {manifest["sdk_version"]}，实际模型 gpt-5.6-sol / medium。完成 {summary["completed"]}/{len(manifest["names"])} 个样本。','',f'模型整图结论：{summary["visual_verdicts"]}；相对基线评价：{summary["comparisons"]}。',f'写入修复计划 {summary["repair_cases"]} 例，首版后成图变化 {summary["changed_after_first"]} 例。核验异常 {len(errors)} 项。','','基线和测试沿用相同冻结analysis/bindings；测试没有重新分析或新增提取。SDK从技能快照独立执行，不使用Astra的逐图判断。视觉评价是指定模型的判断，不能解释为人工准确率。','', '| 样本 | 执行 | 视觉结论 | 基线比较 | 秒数 |', '|---|---|---|---|---:|']
for c in cases:lines.append(f'| {c["name"]} | {c["controller"]} | {c["evaluation"].get("visual_verdict","-")} | {c["evaluation"].get("comparison","-")} | {c["seconds"] or "-"} |')
if errors:lines+=['','核验异常：',*['- '+e for e in errors]]
(root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in summary.items() if k not in ['items','note']},ensure_ascii=False))
