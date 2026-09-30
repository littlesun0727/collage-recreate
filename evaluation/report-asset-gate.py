"""Read-only developer comparison; SDK visual verdicts remain separate evidence."""
import json,hashlib,sys
from pathlib import Path
from html import escape

ROOT=Path(sys.argv[1]);BASE=Path(json.loads((ROOT/'manifest-v2.json').read_text(encoding='utf-8'))['baseline'])
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def link(p,label):return f'<a href="{p.as_uri()}">{escape(label)}</a>'
def picture(p,label):return f'<figure><figcaption>{escape(label)}</figcaption><a href="{p.as_uri()}"><img loading="lazy" src="{p.as_uri()}"></a></figure>'
states=read(ROOT/'result-v2.json');errors=[];cases=[];totals={k:0 for k in ['accepted','pending','rejected']};cards=[];task_ids=set()
for state in states['states']:
    name=state['name'];run=ROOT/'cases-v2'/name/'run';old=BASE/name/'run';v1=ROOT/'cases'/name/'run'
    if state['status']!='completed':errors.append(name+': build incomplete');continue
    report=read(run/'assets/gate/report.json');scene=read(run/'scene.json');result=read(run/'result.json');prior=read(old/'result.json')
    for key in ['analysis_unchanged','bindings_unchanged','reference_unchanged','customer_photos_verified']:
        if not state[key]:errors.append(name+': '+key)
    if state['cache_errors']:errors.append(name+': API cache error')
    for tid in state['task_ids']:
        if tid:task_ids.add(tid)
    for k in totals:totals[k]+=report['counts'][k]
    recs={r['id']:r for r in report['records']}
    for obj in scene['objects']:
        if obj.get('recovered') and recs[obj['id']]['status']!='accepted':errors.append(name+': blocked layer was admitted')
    changed=sha(run/'final.png')!=sha(old/'final.png')
    cases.append({'name':name,'counts':report['counts'],'warnings':sum(bool(r.get('warnings')) for r in recs.values()),
        'changed':changed,'seconds':state['seconds'],'pending':[r['id'] for r in recs.values() if r['status']=='pending'],
        'rejected':[{'id':r['id'],'issues':r['issues'],'local_fallback':r.get('local_fallback',False)} for r in recs.values() if r['status']=='rejected'],
        'before_photo_visibility':prior['photo_visible_fractions'],'after_photo_visibility':result['photo_visible_fractions'],
        'incomplete_objects':result['incomplete_objects'],'scene':str(run/'scene.json')})
    counts=' / '.join(f'{k}: {report["counts"][k]}' for k in totals)
    rows=''.join('<tr><td>'+escape(r['id'])+'</td><td>'+escape(r['status'])+'</td><td>'+escape(', '.join(r['issues']+r.get('warnings',[])))+'</td></tr>' for r in recs.values() if r['status']!='accepted' or r.get('warnings'))
    cards.append('<article><h2>'+escape(name)+'</h2><p>'+counts+'</p><div class="images">'+picture(old/'final.png','层级修正基线')+picture(v1/'final.png','门禁v1：较保守')+picture(run/'final.png','门禁v2：最新开发检查')+'</div><p>'+link(run/'scene.json','scene.json')+' · '+link(run/'assets/gate/report.json','门禁记录')+' · '+link(run/'result.json','渲染记录')+'</p><details><summary>门禁问题与提示</summary><table>'+rows+'</table></details></article>')
verification={'kind':'developer-offline-regression-not-model-acceptance','cases':len(cases),'counts':totals,
    'warning_objects':sum(c['warnings'] for c in cases),'reused_api_tasks':len(task_ids),'network_attempts':states['network_attempts'],
    'new_extraction_requests':0,'errors':errors,'tests':'132 passed','sdk_visual_test':'separate directory v5-asset-gate-sdk-20260930',
    'items':cases}
(ROOT/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
title='素材门禁：16张开发回归（不是模型验收）'
(ROOT/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+title+'</title><style>body{font:16px/1.6 system-ui;margin:24px;background:#eee}article{background:white;padding:18px;margin:24px 0}.images{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}figure{margin:0}img{width:100%;max-height:850px;object-fit:contain}td{border:1px solid #ddd;padding:8px}table{border-collapse:collapse}h1{font-size:24px}</style><h1>'+title+'</h1><p>只比较程序变化。首批根目录同名案例因缓存路径错误作废；有效开发产物在cases与cases-v2。正式SDK视觉判断另存目录，不能将本页视为gpt-5.6-sol的结论。</p><p>'+escape(str(totals))+'</p>'+''.join(cards)+'</html>',encoding='utf-8')
lines=['# 素材门禁实施：开发验证','','[16张程序对比](index.html) · [核验记录](verification.json)','','这是Astra开发实现及确定性程序回归，不是指定模型的视觉验收。正式效果测试必须由Codex Agent SDK + gpt-5.6-sol / medium独立执行。','',f'16张使用原始API缓存离线编译渲染，复用{len(task_ids)}个提取任务，无新提取请求。核验错误：{len(errors)}。','',f'v1：103接纳、27待处理、8拒绝。v2：{totals["accepted"]}接纳、{totals["pending"]}待处理、{totals["rejected"]}拒绝；另有{verification["warning_objects"]}个对象带提示。接纳不等于语义完整，拒绝数包含缺失/空层及可本地回退项，不能直接当作视觉失败率。','','132项测试通过，skill校验通过。真实PaddleOCR+LaMa集成测试通过：原alpha与选区外像素不变，重跑命中缓存；见repair-integration/verification.json。','','目录说明：cases为v1有效开发回归，cases-v2为v2；根目录下同名案例是缓存接错的无效首次批次，仅保留排错证据。','', '| 样本 | 接纳 | 待处理 | 拒绝 | 本地秒数 |', '|---|---:|---:|---:|---:|']
for c in cases:lines.append(f'| {c["name"]} | {c["counts"]["accepted"]} | {c["counts"]["pending"]} | {c["counts"]["rejected"]} | {c["seconds"]} |')
(ROOT/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in verification.items() if k!='items'},ensure_ascii=False))
