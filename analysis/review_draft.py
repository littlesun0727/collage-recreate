"""Offline CLI: preserve response, validate, map original boxes, produce review views."""
import argparse
import hashlib
import html
import json
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from draft_contract import CONTRACT_VERSION, map_boxes, parse_response, validate

COLORS = {'slot': '#16a34a', 'text': '#2563eb', 'overlay': '#e05616'}
VIEWS = {'all': None, 'slots': 'slot', 'texts': 'text', 'overlays': 'overlay'}
VIEW_LABELS = {'all': '总览', 'slots': '照片', 'texts': '文字', 'overlays': '装饰'}


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def render_views(image, boxes, output):
    """No resize or edge refinement. Pixel max edges are exclusive."""
    for name, kind in VIEWS.items():
        view = image.copy()
        draw = ImageDraw.Draw(view)
        for box in boxes:
            if kind is not None and box['type'] != kind:
                continue
            x1, y1, x2, y2 = box['pixel_bbox_xyxy']
            color = COLORS[box['type']]
            draw.rectangle((x1, y1, x2 - 1, y2 - 1), outline=color, width=2)
            label = f"{box['type']}: {box['id']}"
            left, top, right, bottom = draw.textbbox((0, 0), label)
            tw, th = right-left+6, bottom-top+4
            tx = max(0, min(x1, image.width-tw))
            ty = max(0, min(y1-th if y1 >= th else y1, image.height-th))
            draw.rectangle((tx, ty, tx+tw, ty+th), fill=color)
            draw.text((tx+3-left, ty+2-top), label, fill='white')
        view.save(output / f'preview-{name}.png')


def render_page(output, report, draft, boxes):
    esc = lambda value: html.escape(str(value), quote=True)
    ok = report['valid']
    heading = '格式检查通过 · 视觉效果待核对' if ok else '检查未通过 · 未绘制草案框'
    details = '<ul>' + ''.join(f"<li><code>{esc(e['path'])}</code>：{esc(e['message'])}</li>" for e in report['errors']) + '</ul>' if report['errors'] else ''
    buttons = ''
    source = 'reference.png' if report['image'] else ''
    if report['preview_status'] == 'generated':
        buttons = ''.join(f'<button type="button" data-view="{name}">{label}</button>' for name, label in VIEW_LABELS.items())
        source = 'preview-slots.png'
    image_tag = f'<img id="preview" src="{source}" alt="参考图与未经修正的草案原框">' if source else '<p>参考图不可读取。</p>'
    rows = ''.join('<tr>' + ''.join(f'<td>{esc(value)}</td>' for value in (b['type'], b['id'], b['label'], b['source_bbox_1000'], b['pixel_bbox_xyxy'], b['mapping_rule'])) + '</tr>' for b in boxes)
    table = '<table><thead><tr><th>类型</th><th>ID</th><th>名称</th><th>原始 0–999 框</th><th>原图像素边界</th><th>换算规则</th></tr></thead><tbody>' + rows + '</tbody></table>' if boxes else ''
    questions = draft.get('questions', []) if isinstance(draft, dict) else []
    questions_html = '<h2>待确认内容</h2><pre>' + esc(json.dumps(questions, ensure_ascii=False, indent=2)) + '</pre>' if questions else ''
    warnings_html = '<details><summary>检查说明</summary><pre>' + esc(json.dumps(report['warnings'], ensure_ascii=False, indent=2)) + '</pre></details>' if report['warnings'] else ''
    links = ' · '.join(f'<a href="{filename}">{label}</a>' for filename, label in [('raw-response.txt','原始响应'),('parsed-draft.json','解析草案'),('validation.json','检查结果'),('mapped-boxes.json','坐标换算')] if (output / filename).exists())
    raw_details = '<details><summary>完整草案（含来源和外观说明）</summary><pre>' + esc(json.dumps(draft, ensure_ascii=False, indent=2)) + '</pre></details>' if draft is not None else ''
    document = f'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>草案原框预览</title><style>
body{{font:16px system-ui,sans-serif;margin:24px auto;padding:0 20px;max-width:1100px;color:#202733;background:#f7f8fa}}
h1{{font-size:24px}}button{{padding:8px 16px;margin:0 8px 8px 0;cursor:pointer;border:1px solid #aab3c2;border-radius:6px;background:white}}
button[aria-pressed=true]{{background:#233951;color:white}}img{{display:block;max-width:100%;height:auto;background:white;border:1px solid #ddd}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin:20px 0}}td,th{{border:1px solid #ccc;padding:7px;text-align:left}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:12px}}code{{overflow-wrap:anywhere}}.muted{{color:#526170}}
</style><h1>{heading}</h1><p class="muted">展示草案原框。未吸边、未自动改坐标；矩形框不代表已恢复剪影或遮罩；仅指定的摄影背景槽按背景语义铺满画布。</p>
{details}<nav>{buttons}</nav>{image_tag}<p>{links}</p>{questions_html}{warnings_html}{table}{raw_details}
<script>
const buttons = document.querySelectorAll('[data-view]');
function choose(name){{document.getElementById('preview').src='preview-'+name+'.png';buttons.forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===name)));}}
buttons.forEach(b=>b.addEventListener('click',()=>choose(b.dataset.view)));
if(buttons.length) choose('slots');
</script></html>'''
    (output / 'index.html').write_text(document, encoding='utf-8')


def run(reference, response, output):
    reference, response, output = Path(reference), Path(response), Path(output)
    # A new directory prevents mixing an invalid run with previews from an older run.
    output.mkdir(parents=True, exist_ok=False)
    report = {'contract_version': CONTRACT_VERSION, 'valid': False, 'visual_status': 'unreviewed',
              'preview_status': 'not_generated', 'errors': [], 'warnings': [], 'image': None,
              'response_sha256': None, 'analysis_sha256': hashlib.sha256((ROOT / 'analysis.md').read_bytes()).hexdigest()}
    draft, parsed, image, boxes = None, False, None, []
    try:
        raw = response.read_bytes()
        (output / 'raw-response.txt').write_bytes(raw)
        report['response_sha256'] = hashlib.sha256(raw).hexdigest()
        draft = parse_response(raw)
        parsed = True
        save_json(output / 'parsed-draft.json', draft)
        errors, warnings = validate(draft)
        report['errors'].extend(errors)
        report['warnings'].extend(warnings)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        report['errors'].append({'path': '$input', 'code': 'response_read_or_parse', 'message': str(exc)})
    try:
        source = reference.read_bytes()
        with Image.open(BytesIO(source)) as original:
            original_size = list(original.size)
            orientation = original.getexif().get(274, 1)
            image = ImageOps.exif_transpose(original).convert('RGBA')
        image.save(output / 'reference.png')
        report['image'] = {'sha256': hashlib.sha256(source).hexdigest(), 'stored_size': original_size,
                           'exif_orientation': orientation, 'oriented_size': list(image.size),
                           'resized': False, 'coordinate_basis': 'EXIF-oriented complete image'}
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        report['errors'].append({'path': '$reference', 'code': 'image_read', 'message': str(exc)})
    if parsed and image is not None and not report['errors']:
        boxes, errors = map_boxes(draft, *image.size)
        report['errors'].extend(errors)
        save_json(output / 'mapped-boxes.json', {'image_size': list(image.size), 'rounding': 'integer round-half-up on each edge',
                  'coordinate_range': [0, 999], 'coordinate_scale': 1000,
                  'background_mapping': 'designated background slot covers the full canvas; original JSON unchanged',
                  'pixel_edges': 'half-open xyxy; right and bottom may equal image dimensions', 'boxes': boxes})
        if not errors:
            try:
                render_views(image, boxes, output)
                report['preview_status'] = 'generated'
            except (OSError, ValueError) as exc:
                report['errors'].append({'path': '$preview', 'code': 'render', 'message': str(exc)})
    report['valid'] = not report['errors']
    save_json(output / 'validation.json', report)
    render_page(output, report, draft, boxes)
    return report


def main():
    parser = argparse.ArgumentParser(description='Validate v2 draft and preview unmodified boxes offline. Output directory must not exist.')
    parser.add_argument('--reference', required=True, type=Path)
    parser.add_argument('--input', required=True, type=Path, help='UTF-8 raw response containing exactly one JSON value')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run(args.reference, args.input, args.output)
    except OSError as exc:
        parser.exit(2, f'Output error: {exc}\n')
    print(json.dumps({'valid': report['valid'], 'visual_status': report['visual_status'], 'errors': len(report['errors']),
                      'preview': str(args.output / 'index.html')}, ensure_ascii=False))
    return 0 if report['valid'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
