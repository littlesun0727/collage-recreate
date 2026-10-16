import base64
import hashlib
import io
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from PIL import Image, ImageDraw


def main():
    task = Path(r'D:\codes\ming-e2e-20261014\20260908-192922_21ca6c64e4')
    env = Path(r'D:\codes\ming-layer-workflow\.env').read_text(encoding='utf-8-sig')
    match = re.search(r'^\s*(?:export\s+)?DEEPINFRA_API_KEY\s*=\s*(.*?)\s*$', env, re.M)
    if not match:
        raise RuntimeError('DEEPINFRA_API_KEY is missing')
    key = match.group(1).strip().strip('\"\'')
    if not key:
        raise RuntimeError('DEEPINFRA_API_KEY is empty')
    prompt = (task / 'prompt.txt').read_text(encoding='utf-8-sig')
    count = int(re.search(r'Number of layers:\s*(\d+)', prompt)[1])
    raw = (task / '.input.png').read_bytes()
    output = Path(__file__).resolve().parent / 'artifacts' / ('ming-deepinfra-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'input.png').write_bytes(raw)
    (output / 'prompt.txt').write_text(prompt, encoding='utf-8')
    endpoint = 'https://api.deepinfra.com/v1/inference/inclusionAI/Ming-Image-0.1-Design-Layer'
    report = {'task': str(task), 'endpoint': endpoint, 'expected_layers': count,
              'input_sha256': hashlib.sha256(raw).hexdigest(), 'status': 'submitted',
              'parameters': {'num_layers': count, 'num_inference_steps': 12, 'guidance_scale': 2.0}}

    def save_report():
        (output / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')

    save_report()
    print('Output: ' + str(output), flush=True)
    print('Submitting one inference request; automatic retries disabled.', flush=True)
    start = time.monotonic()
    try:
        response = requests.post(endpoint, headers={'Authorization': 'Bearer ' + key},
                                 data={'prompt': prompt, **report['parameters']},
                                 files={'input_image': ('input.png', raw, 'image/png')},
                                 timeout=(30, 600), allow_redirects=False)
    except requests.RequestException as error:
        report.update(status='transport_error', error=str(error).replace(key, '[REDACTED]'), seconds=round(time.monotonic()-start, 2))
        save_report()
        print(json.dumps(report, ensure_ascii=True), flush=True)
        return
    report.update(http_status=response.status_code, seconds=round(time.monotonic()-start, 2))
    safe_text = response.text.replace(key, '[REDACTED]')
    (output / 'response.json').write_text(safe_text, encoding='utf-8')
    if response.status_code != 200:
        report.update(status='http_error', error=safe_text[:2500])
        save_report()
        print(json.dumps(report, ensure_ascii=True), flush=True)
        return
    body = response.json()
    report.update(inference_status=body.get('inference_status'), request_id=body.get('request_id'), seed=body.get('seed'))
    images = body.get('images', [])
    report['received_layers'] = len(images)
    report['layers'] = []
    opened = []
    for index, data in enumerate(images, 1):
        if not isinstance(data, str) or not data.startswith('data:image/') or ';base64,' not in data:
            raise ValueError('Unexpected image output encoding; raw response saved')
        png = base64.b64decode(data.split(',', 1)[1], validate=True)
        im = Image.open(io.BytesIO(png))
        im.load()
        filename = f'layer_{index:02d}.png'
        (output / filename).write_bytes(png)
        alpha = im.getchannel('A') if 'A' in im.getbands() else None
        report['layers'].append({'file': filename, 'mode': im.mode, 'format': im.format,
                                 'size': list(im.size), 'alpha_extrema': alpha.getextrema() if alpha else None})
        opened.append(im.convert('RGBA'))
    if opened:
        tiles = []
        for label, im in [('input', Image.open(io.BytesIO(raw)).convert('RGBA'))] + [(f'layer {i}', im) for i, im in enumerate(opened, 1)]:
            tile = Image.new('RGB', (260, 490), '#dddddd')
            draw = ImageDraw.Draw(tile)
            for y in range(30, 490, 16):
                for x in range(0, 260, 16):
                    if (x//16 + y//16) % 2:
                        draw.rectangle((x,y,x+15,y+15), fill='#bbbbbb')
            thumb = im.copy()
            thumb.thumbnail((252, 450))
            tile.paste(thumb, ((260-thumb.width)//2, 32), thumb)
            draw.text((8, 8), label, fill='black')
            tiles.append(tile)
        sheet = Image.new('RGB', (260*4, 490*((len(tiles)+3)//4)), 'white')
        for i, tile in enumerate(tiles):
            sheet.paste(tile, ((i%4)*260, (i//4)*490))
        sheet.save(output / 'preview.jpg', quality=90)
        if len({im.size for im in opened}) == 1:
            for name, sequence in [('composite_front_to_back', list(reversed(opened))), ('composite_back_to_front', opened)]:
                canvas = Image.new('RGBA', opened[0].size)
                for im in sequence:
                    canvas = Image.alpha_composite(canvas, im)
                canvas.save(output / (name + '.png'))
    report['status'] = 'complete' if opened else 'empty_response'
    save_report()
    print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
