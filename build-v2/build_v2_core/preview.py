"""Resource-only inspection pages and labeled reference/output contact sheets."""
import html
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .common import oriented


def checker(size, cell=16):
    image = Image.new('RGB', size, '#dddddd')
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill='#aaaaaa')
    return image


def flatten(image):
    base = checker(image.size).convert('RGBA'); base.alpha_composite(image.convert('RGBA'))
    return base.convert('RGB')


def make_preview(output, row, entry, inputs):
    slug = row['key'].replace(':', '__')
    root = output / 'previews'; root.mkdir(exist_ok=True)
    image = oriented(output / row['asset'])
    flattened = flatten(image)
    flattened.thumbnail((700, 700), Image.Resampling.LANCZOS)
    result_path = root / (slug + '-resource.jpg'); flattened.save(result_path, quality=92)
    if entry['type'] == 'slot' and entry['draft']['mode'] == 'cutout' and entry.get('source_asset'):
        reference = oriented(entry['source_asset']['path'], 'RGB')
    else:
        reference = inputs['reference'].crop(entry['reference_box']).convert('RGB')
    reference.thumbnail((700, 700), Image.Resampling.LANCZOS)
    ref_path = root / (slug + '-reference.jpg'); reference.save(ref_path, quality=92)
    row['previews'] = {'reference': str(ref_path.relative_to(output)).replace('\\', '/'), 'resource': str(result_path.relative_to(output)).replace('\\', '/')}
    if row.get('openings'):
        test = Image.new('RGBA', image.size, '#222222')
        for index, opening in enumerate(row['openings']):
            mask = oriented(output / opening['mask'], 'L')
            bound = mask.getbbox()
            tile = Image.new('RGBA', image.size, ('#eeb755', '#a1d9f3', '#d8c5ee')[index % 3])
            draw = ImageDraw.Draw(tile)
            for x in range(0, tile.width, max(8, tile.width // 12)):
                draw.line((x, 0, x, tile.height), fill='white', width=1)
            for y in range(0, tile.height, max(8, tile.height // 12)):
                draw.line((0, y, tile.width, y), fill='white', width=1)
            tile.putalpha(mask); test.alpha_composite(tile)
        test.alpha_composite(image)
        test.thumbnail((700, 700), Image.Resampling.LANCZOS)
        path = root / (slug + '-opening-test.jpg'); test.convert('RGB').save(path, quality=92)
        row['previews']['opening_test'] = str(path.relative_to(output)).replace('\\', '/')


def sheets(output, rows, batch_size=4):
    valid = [row for row in rows if row.get('previews')]
    groups = []
    font = ImageFont.load_default(size=16)
    for start in range(0, len(valid), batch_size):
        group = valid[start:start + batch_size]
        canvas = Image.new('RGB', (960, len(group) * 310), '#eeeeee')
        draw = ImageDraw.Draw(canvas)
        for i, row in enumerate(group):
            for column, name in enumerate(('reference', 'resource')):
                pic = oriented(output / row['previews'][name], 'RGB')
                pic.thumbnail((455, 268), Image.Resampling.LANCZOS)
                canvas.paste(pic, (column * 480 + (480 - pic.width) // 2, i * 310 + 35 + (268 - pic.height) // 2))
                draw.text((column * 480 + 10, i * 310 + 8), row['key'] + ' / ' + name, fill='black', font=font)
        path = output / 'previews' / f'contact-{start // batch_size + 1:02d}.jpg'
        canvas.save(path, quality=92)
        groups.append((group, path))
    return groups


def write_page(output, rows, result):
    e = html.escape
    cards = []
    for row in rows:
        pictures = ''.join(f'<figure><img src="{e(path)}"><figcaption>{e(name)}</figcaption></figure>' for name, path in row.get('previews', {}).items())
        if row.get('asset'):
            pictures += f'<p><a href="{e(row["asset"])}" target="_blank">原尺寸 PNG {e(str(row.get("geometry", {}).get("size", [])))}</a></p>'
        issues = row.get('visual_review', {}).get('issues', [])
        detail = row.get('error', '') or '; '.join(issues)
        state = row.get('visual_review', {}).get('status', 'unreviewed')
        cards.append(f'<article><h2>{e(row["key"])}</h2><p>{e(row["status"])} · {e(row.get("method", ""))} · visual: {e(state)} · cache: {row.get("reused", False)}</p><p>{e(detail)}</p><div class="images">{pictures}</div></article>')
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>build 资源检查</title><style>body{font:16px system-ui;max-width:1500px;margin:30px auto;padding:0 20px;background:#f2f3f5;color:#222}article{background:white;padding:20px;margin:20px 0;border-radius:8px}.images{display:flex;gap:20px;flex-wrap:wrap}figure{margin:0;max-width:440px}img{max-width:100%;max-height:400px;object-fit:contain}figcaption{color:#666}a{color:#1764a5}</style><h1>build 资源检查</h1><p>本页仅为独立资源和局部测试，不是最终排版。opening_test 使用测试网格。</p>'
    page += f'<p>执行状态：{e(result["status"])}；资源可进入 renders：{result.get("ready_for_render", False)}；本次范围：{len(rows)} 项。</p><p><a href="assets.json">资源清单</a> · <a href="build-plan.json">制作计划</a> · <a href="result.json">结果摘要</a></p>'
    page += ''.join(cards) + '</html>'
    (output / 'index.html').write_text(page, encoding='utf-8')
