"""Run a resumable, single-attempt five-layer comparison batch."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import html
import io
import json
import re
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageOps

PROMPT = 'Decompose this image into 5 layers.'
ENDPOINT = 'https://api.deepinfra.com/v1/inference/inclusionAI/Ming-Image-0.1-Design-Layer'
PARAMETERS = {'num_layers': 5, 'num_inference_steps': 12, 'guidance_scale': 2.0}
EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}


def save(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def tile(image, label, width=240, height=370):
    result = Image.new('RGB', (width, height), '#eeeeee')
    draw = ImageDraw.Draw(result)
    for y in range(26, height, 12):
        for x in range(0, width, 12):
            if (x//12 + y//12) % 2:
                draw.rectangle((x, y, x+11, y+11), fill='#c5c5c5')
    draw.text((8, 6), label, fill='#111111')
    thumb = image.convert('RGBA')
    thumb.thumbnail((width-12, height-36))
    result.paste(thumb, ((width-thumb.width)//2, 30), thumb)
    return result


def previews(case, original, layers):
    composite = Image.new('RGBA', layers[0].size)
    for layer in reversed(layers):
        composite = Image.alpha_composite(composite, layer)
    composite.save(case / 'composite.png')
    # Preserve the alternative stacking order for inspection; never relabel layers.
    reverse = Image.new('RGBA', layers[0].size)
    for layer in layers:
        reverse = Image.alpha_composite(reverse, layer)
    reverse.save(case / 'composite_reverse.png')
    items = [('Original', original), ('Composite (5 to 1)', composite)]
    items += [(f'Layer {i}', layer) for i, layer in enumerate(layers, 1)]
    sheet = Image.new('RGB', (240*len(items), 370), 'white')
    for i, (label, image) in enumerate(items):
        sheet.paste(tile(image, label), (240*i, 0))
    sheet.save(case / 'preview.jpg', quality=92)


def run_one(source, case, key):
    report_path = case / 'report.json'
    if report_path.exists():
        return json.loads(report_path.read_text(encoding='utf-8'))
    case.mkdir(parents=True, exist_ok=True)
    report = {'source': str(source), 'case': case.name, 'status': 'preparing',
              'prompt': PROMPT, 'parameters': PARAMETERS, 'endpoint': ENDPOINT}
    start = time.monotonic()
    try:
        raw = source.read_bytes()
        original = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGBA')
        original.save(case / 'input.png')
        upload = (case / 'input.png').read_bytes()
        (case / 'prompt.txt').write_text(PROMPT, encoding='utf-8')
        report.update(input_size=list(original.size), input_sha256=hashlib.sha256(raw).hexdigest(),
                      submitted_at=datetime.now().isoformat(), status='submitted')
        save(report_path, report)
        print('SUBMIT ' + source.name, flush=True)
        # requests performs no retries by default. Refuse redirects with credentials.
        response = requests.post(ENDPOINT, headers={'Authorization': 'Bearer ' + key},
                                 data={'prompt': PROMPT, **PARAMETERS},
                                 files={'input_image': ('input.png', upload, 'image/png')},
                                 timeout=(30, 600), allow_redirects=False)
        report.update(http_status=response.status_code, seconds=round(time.monotonic()-start, 2))
        (case / 'response.json').write_text(response.text.replace(key, '[REDACTED]'), encoding='utf-8')
        if response.status_code != 200:
            report.update(status='http_error', error=response.text.replace(key, '[REDACTED]')[:2000])
        else:
            body = response.json()
            report.update(inference_status=body.get('inference_status'), request_id=body.get('request_id'), seed=body.get('seed'))
            images = body.get('images', [])
            report.update(received_layers=len(images), layers=[])
            layers = []
            for index, data in enumerate(images, 1):
                if not isinstance(data, str) or not data.startswith('data:image/') or ';base64,' not in data:
                    raise ValueError('Unexpected image encoding; raw response is saved')
                raw_layer = base64.b64decode(data.split(',', 1)[1], validate=True)
                image = Image.open(io.BytesIO(raw_layer))
                image.load()
                filename = f'layer_{index:02d}.png'
                (case / filename).write_bytes(raw_layer)
                alpha = image.getchannel('A') if 'A' in image.getbands() else None
                report['layers'].append({'file': filename, 'size': list(image.size), 'mode': image.mode,
                                         'format': image.format, 'alpha_extrema': list(alpha.getextrema()) if alpha else None})
                layers.append(image.convert('RGBA'))
            if not layers:
                report['status'] = 'empty_response'
            elif len({layer.size for layer in layers}) != 1:
                report['status'] = 'size_mismatch'
            else:
                previews(case, original, layers)
                report['status'] = 'complete' if len(layers) == 5 else 'layer_count_mismatch'
    except Exception as error:
        report.update(status='error', error=str(error).replace(key, '[REDACTED]'), seconds=round(time.monotonic()-start, 2))
    save(report_path, report)
    cost = (report.get('inference_status') or {}).get('cost')
    print(f"DONE {source.name}: {report['status']}; layers={report.get('received_layers')}; cost={cost}; seconds={report.get('seconds')}", flush=True)
    return report


def build_index(root, sources, cases):
    reports = []
    sections = []
    for source, case in zip(sources, cases):
        path = case / 'report.json'
        report = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'status': 'pending', 'source': str(source), 'case': case.name}
        reports.append(report)
        escaped = html.escape(source.name)
        status = html.escape(report['status'])
        cost = (report.get('inference_status') or {}).get('cost')
        sizes = report.get('layers', [{}])[0].get('size') if report.get('layers') else None
        details = f"{status} · 输出 {sizes} · {report.get('seconds', '—')} 秒 · ${cost if cost is not None else '—'}"
        figures = ''
        for label, file in [('原图', 'input.png'), ('合成（5→1）', 'composite.png')] + [(f'图层 {i}', f'layer_{i:02d}.png') for i in range(1, 6)]:
            if (case / file).exists():
                url = f'{case.name}/{file}'
                figures += f'<figure><figcaption>{label}</figcaption><a href="{url}" target="_blank"><img loading="lazy" src="{url}"></a></figure>'
        links = f'<a href="{case.name}/report.json">请求记录</a>' if path.exists() else ''
        if (case / 'preview.jpg').exists():
            links += f' · <a href="{case.name}/preview.jpg">整行预览</a> · <a href="{case.name}/composite_reverse.png">反向合成</a>'
        error = f'<pre>{html.escape(report.get("error", ""))}</pre>' if report.get('error') else ''
        sections.append(f'<section><h2>{escaped}</h2><p>{details} · {links}</p>{error}<div class="row">{figures}</div></section>')
    complete = sum(r['status'] == 'complete' for r in reports)
    reported_cost = sum((r.get('inference_status') or {}).get('cost') or 0 for r in reports)
    summary = {'input_directory': str(sources[0].parent) if sources else '', 'prompt': PROMPT,
               'parameters': PARAMETERS, 'total': len(sources), 'complete': complete,
               'reported_cost_usd': round(reported_cost, 6), 'cases': reports}
    save(root / 'summary.json', summary)
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Ming 五层批量对照</title><style>
body{margin:24px;background:#151719;color:#eee;font:15px system-ui,sans-serif}h1{font-size:26px}h2{font-size:18px}p{color:#bbb}a{color:#99caff}section{border-top:1px solid #454545;padding:20px 0}.row{display:flex;gap:12px;overflow-x:auto;padding-bottom:12px}figure{margin:0;flex:0 0 220px}figcaption{padding:8px 0}img{width:220px;height:360px;object-fit:contain;background:repeating-conic-gradient(#bbb 0% 25%,#eee 0% 50%) 0/20px 20px}pre{white-space:pre-wrap}button{padding:8px 14px;margin-right:8px}body.white img{background:white}body.black img{background:#111}
</style><h1>Ming 五层批量对照</h1>'''
    page += f'<p>完成 {complete}/{len(sources)} · API 已报告费用 ${reported_cost:.4f}</p><p>统一提示词：{PROMPT}<br>12 步 · CFG 2 · 每张只提交一次。点击图片查看原尺寸。</p>'
    page += '''<button onclick="document.body.className=''">棋盘格</button><button onclick="document.body.className='white'">白底</button><button onclick="document.body.className='black'">黑底</button>'''
    page += ''.join(sections) + '</html>'
    (root / 'index.html').write_text(page, encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default=r'D:\datas\图片排版样图_去水印')
    parser.add_argument('--output', required=True)
    parser.add_argument('--render-only', action='store_true')
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    sources = sorted(p for p in Path(args.input).iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS)
    cases = [root / f'{i:02d}_{hashlib.sha256(p.name.encode()).hexdigest()[:8]}' for i, p in enumerate(sources, 1)]
    build_index(root, sources, cases)
    if not args.render_only:
        env = Path(r'D:\codes\ming-layer-workflow\.env').read_text(encoding='utf-8-sig')
        match = re.search(r'^\s*(?:export\s+)?DEEPINFRA_API_KEY\s*=\s*(.*?)\s*$', env, re.M)
        if not match:
            raise ValueError('DEEPINFRA_API_KEY missing')
        key = match.group(1).strip().strip('\"\'')
        if not key:
            raise ValueError('DEEPINFRA_API_KEY empty')
        print(f'Batch: {len(sources)} images; 3 concurrent requests; output={root}', flush=True)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(run_one, source, case, key) for source, case in zip(sources, cases)]
            for future in as_completed(futures):
                future.result()
                build_index(root, sources, cases)
    summary = build_index(root, sources, cases)
    print(json.dumps({k:v for k,v in summary.items() if k != 'cases'}, ensure_ascii=True), flush=True)
    print('Preview: ' + str(root / 'index.html'), flush=True)


if __name__ == '__main__':
    main()
