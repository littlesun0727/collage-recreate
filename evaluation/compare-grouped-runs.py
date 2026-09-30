"""Build an inspectable comparison from saved artifacts; no image/model edits."""
import json
import sys
from html import escape
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def figure(label, path):
    if not path.exists():
        return ""
    uri = escape(path.as_uri(), quote=True)
    return f'<figure><figcaption>{escape(label)}</figcaption><a href="{uri}"><img loading="lazy" src="{uri}"></a></figure>'


def object_map(run, title):
    analysis = read(run / "analysis.json")
    if not analysis:
        return ""
    width, height = analysis["reference_size"]
    objects = [o for o in analysis["objects"] if o.get("method") == "extract"]
    parts = [f'<section><h2>{escape(title)}：{len(objects)}个提取目标</h2>',
             '<p>框表示分析时的素材单元；不是请求分组。点击框可查看对象描述。</p>',
             f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="提取目标框">',
             f'<image href="{escape((run / "prepared/reference.png").as_uri(), quote=True)}" width="{width}" height="{height}"/>']
    for index, obj in enumerate(objects):
        left, top, right, bottom = obj["bbox"]
        color = f"hsl({index * 137.5 % 360},85%,43%)"
        parts.append(f'<a href="#item-{escape(title)}-{index}"><rect x="{left}" y="{top}" width="{right-left}" height="{bottom-top}" fill="none" stroke="{color}" stroke-width="4" pointer-events="all"><title>{escape(obj["id"] + ": " + obj["description"])}</title></rect><text x="{left+4}" y="{top+20}" font-size="20" fill="{color}" stroke="white" stroke-width="3" paint-order="stroke">{index+1}</text></a>')
    parts.append('</svg><ol>')
    for index, obj in enumerate(objects):
        parts.append(f'<li id="item-{escape(title)}-{index}"><b>{escape(obj["id"])}</b> {escape(obj["label"])}<br>{escape(obj["description"])}</li>')
    parts.append('</ol></section>')
    return ''.join(parts)


old, new, output = map(Path, sys.argv[1:4])
evaluation = read(new.parent / "evaluation.json")
result = read(new / "result.json")
plan = read(new / "assets/reveal/request-plan.json")
summary = {"old_extract_targets": sum(o.get("method") == "extract" for o in read(old / "analysis.json").get("objects", [])),
           "new_extract_targets": sum(o.get("method") == "extract" for o in read(new / "analysis.json").get("objects", [])),
           "new_request_groups": len(plan.get("batches", [])), "model_evaluation": evaluation,
           "gate": result.get("asset_gate_summary"), "incomplete": result.get("incomplete_objects")}
html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>拼贴3：局部组合规则端到端对照</title><style>
body{font:16px/1.65 system-ui;margin:24px;background:#eee;color:#202020}h1{font-size:26px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.maps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}figure,section{margin:0;background:white;padding:12px}img,svg{width:100%;height:auto}figcaption{font-weight:600;margin-bottom:8px}pre{white-space:pre-wrap;background:white;padding:16px}li{margin-bottom:12px}a{color:#164bb0}@media(max-width:850px){.grid,.maps{grid-template-columns:1fr}}</style>
<h1>拼贴3：局部组合规则端到端对照</h1><p>新结果由 Codex Agent SDK + gpt-5.6-sol / medium 从原图重新分析、绑定、真实提取、筛选与复核。旧结果仅供事后对照，未提供给新测试模型。客户选图也可能变化；不能把整图差异全部归因于规则。点击图片可打开原文件。</p>'''
html += '<div class="grid">' + figure('参考图', new / 'prepared/reference.png') + figure('旧规则：整页混合结果', old / 'final.png') + figure('新规则：当前结果', new / 'final.png') + '</div>'
html += '<h2>新测试首版与筛选结果</h2><div class="grid">' + figure('筛选前首版', new / 'previews/first.png') + figure('筛选后当前图', new / 'final.png') + '</div>'
html += '<div class="maps">' + object_map(old, '旧分析') + object_map(new, '新分析') + '</div>'
html += '<h2>新提取素材检查表</h2><div class="grid">' + ''.join(figure(f'素材检查表 {index+1}', Path(path)) for index, path in enumerate(result.get('extraction_sheets', []))) + '</div>'
html += '<h2>指定模型结论与程序记录</h2><p>以下视觉结论来自指定测试模型，不是人工真值。分析框数不代表每个返回层都完整正确。</p><pre>' + escape(json.dumps(summary, ensure_ascii=False, indent=2)) + '</pre>'
html += '<p>' + ' · '.join(f'<a href="{escape(path.as_uri(), quote=True)}">{label}</a>' for label, path in [('新分析', new/'analysis.json'), ('提取分组', new/'assets/reveal/request-plan.json'), ('最终scene', new/'scene.json'), ('视觉评价', new.parent/'evaluation.json')]) + '</p></html>'
output.write_text(html, encoding='utf-8')
print(json.dumps({k: v for k, v in summary.items() if k != 'model_evaluation'}, ensure_ascii=False))
