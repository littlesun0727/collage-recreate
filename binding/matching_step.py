"""Prepare reference matching against a reusable described customer catalog."""
import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from prompt_sections import read_stage
from binding_contract import VERSION, digest, read_json, read_model_json, load_slots, resolve_overrides, validate_selection, assemble_bindings

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def oriented(path):
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert('RGB')


def make_sheet(items, output, columns=4):
    cell_w, cell_h = 280, 250
    sheet = Image.new('RGB', (columns * cell_w, math.ceil(len(items) / columns) * cell_h), 'white')
    draw = ImageDraw.Draw(sheet)
    for i, (ident, path) in enumerate(items):
        image = oriented(path)
        image.thumbnail((cell_w - 12, cell_h - 36))
        x, y = i % columns * cell_w, i // columns * cell_h
        sheet.paste(image, (x + (cell_w-image.width)//2, y+30))
        draw.text((x+6, y+8), ident, fill='black')
    sheet.save(output, quality=92)


def index_assets(folders, output, max_assets=48):
    paths = {}
    for folder in folders:
        folder = Path(folder).resolve()
        if not folder.is_dir():
            raise ValueError(f'Missing materials directory: {folder}')
        for path in folder.rglob('*'):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                paths[str(path.resolve()).casefold()] = path.resolve()
    if len(paths) > max_assets:
        raise ValueError(f'{len(paths)} image files exceed the first-version limit {max_assets}; select a smaller materials folder')
    assets, warnings, seen = [], [], {}
    for path in sorted(paths.values(), key=lambda p: str(p).casefold()):
        try:
            sha = digest(path)
            if sha in seen:
                seen[sha]['aliases'].append(str(path))
                continue
            image = oriented(path)
            aid = 'asset_' + sha[:16]
            if any(a['asset_id'] == aid for a in assets):
                raise ValueError('Asset ID hash collision')
            size = list(image.size)
            image.thumbnail((480, 480))
            thumb = output/'preview'/f'{aid}.jpg'
            image.save(thumb, quality=92)
            asset = {'asset_id': aid, 'path': str(path), 'sha256': sha, 'size': size,
                     'thumbnail': str(thumb.relative_to(output)).replace('\\', '/'), 'aliases': []}
            assets.append(asset); seen[sha] = asset
        except (OSError, Image.DecompressionBombError) as exc:
            warnings.append(f'无法读取素材 {path.name}: {exc}')
    return assets, warnings


def prepare(draft_path, reference_path, folders, output, version='auto'):
    draft_path, reference_path = Path(draft_path).resolve(), Path(reference_path).resolve()
    draft = read_json(draft_path)
    metadata_path = draft_path.parent/'validation.json'
    metadata = read_json(metadata_path) if metadata_path.is_file() else {}
    if version == 'auto':
        version = metadata.get('contract_version', 'v2-2')
    slots = load_slots(draft, version)
    warnings = ['绑定只负责选图，不校正原 draft 的坐标，也不表示布局已通过视觉验收。']
    if metadata.get('valid') is False:
        raise ValueError('The upstream analysis validation failed; choose a valid draft')
    ref_hash = digest(reference_path)
    expected = metadata.get('image', {}).get('sha256')
    if expected and expected != ref_hash:
        raise ValueError('Reference file does not match upstream analysis image hash; provide its original reference')
    image = oriented(reference_path)
    if metadata.get('image', {}).get('oriented_size') not in (None, list(image.size)):
        raise ValueError('Reference dimensions differ from upstream analysis')
    (output/'preview').mkdir()
    reference_copy = output/'preview/reference.png';image.save(reference_copy)
    (output/'draft.json').write_bytes(draft_path.read_bytes())
    crops = {}
    bg = draft.get('background', {})
    for slot in slots:
        if bg.get('mode') == 'slot' and bg.get('slot_id') == slot['id']:
            expected_box = [0, 0, 1000, 1000] if version == 'v2-1' else [0, 0, 999, 999]
            if slot['source_bbox_1000'] != expected_box:
                raise ValueError('Designated background must have the full-canvas box for its draft version')
            box = [0, 0, image.width, image.height]
        else:
            box = [(v*d+500)//1000 for v, d in zip(slot['source_bbox_1000'], image.size*2)]
        if box[0] >= box[2] or box[1] >= box[3]:
            raise ValueError(f'Slot collapses to zero pixels: {slot["id"]}')
        path = output/'preview'/f'slot_{slot["id"]}.jpg'
        image.crop(box).save(path, quality=92)
        crops[slot['id']] = {'bbox_xyxy': box, 'path': str(path.relative_to(output)).replace('\\', '/')}
    assets, asset_warnings = index_assets(folders, output)
    warnings += asset_warnings
    if draft.get('questions'):
        warnings.append('原 draft 仍有 questions，原样保存在 draft.json；匹配不会解决这些上游问题。')
    image_inputs = [{'path': str(reference_copy), 'label': '完整参考图；只理解各 slot 的内容意图，不重新识别布局。'}]
    for prefix, items in [
        ('slots', [(s['id'], output/crops[s['id']]['path']) for s in slots]),
        ('assets', [(a['asset_id'], output/a['thumbnail']) for a in assets])]:
        for start in range(0, len(items), 12):
            sheet = output/'preview'/f'{prefix}_{start//12+1}.jpg'
            make_sheet(items[start:start+12], sheet)
            image_inputs.append({'path': str(sheet), 'label': ('draft 原坐标裁图，标注 slot_id' if prefix == 'slots' else '客户素材联系表，标注 asset_id')+f'，第 {start//12+1} 页；等比缩略，不拉伸。'})
    info = {'draft_path': str(draft_path), 'draft_sha256': digest(draft_path), 'draft_contract_version': version,
            'reference_path': str(reference_path), 'reference_sha256': ref_hash, 'reference_size': list(image.size),
            'materials_dirs': [str(Path(p).resolve()) for p in folders], 'slot_crops': crops,
            'image_processing': 'Reference EXIF-oriented without resize; slot crops use unchanged draft boxes; labeled contact sheets use proportional thumbnails.'}
    save(output/'input.json', info);save(output/'assets.json', assets);save(output/'images.json', image_inputs)
    return slots, assets, info, warnings


def write_preview(output, slots, assets, rows, warnings):
    esc = lambda value: html.escape(str(value), quote=True)
    by_asset = {a['asset_id']: a for a in assets}
    by_slot = {s['id']: s for s in slots}
    cards = []
    for row in rows:
        slot = by_slot[row['slot_id']];asset = by_asset.get(row['asset_id'])
        chosen = f'<img src="{esc(asset["thumbnail"])}"><p>{esc(Path(asset["path"]).name)}</p>' if asset else '<p>未绑定</p>'
        cards.append(f'<article><h2>{esc(slot["id"])} · {esc(slot.get("label", ""))}</h2><div class="pair"><div><p>参考原框裁图</p><img src="preview/slot_{esc(slot["id"])}.jpg"></div><div><p>客户素材（完整缩略图，尚未裁剪）</p>{chosen}</div></div><p>{esc(row["reason"])}</p><small>{esc(row["method"])}</small></article>')
    page = '<!doctype html><meta charset="utf-8"><title>客户素材绑定对照</title><style>body{font:16px sans-serif;max-width:1100px;margin:28px auto;background:#f5f4f0}article{background:white;padding:20px;margin:18px 0}.pair{display:flex;gap:24px}.pair>div{width:48%}img{max-width:100%;height:300px;object-fit:contain}small{color:#666}</style><h1>客户素材绑定对照</h1><p>这是选图结果，不是最终排版或裁剪预览。</p><details><summary>完整参考图</summary><img style="height:650px" src="preview/reference.png"></details><ul>'+''.join('<li>'+esc(w)+'</li>' for w in warnings)+'</ul>'+''.join(cards)
    (output/'index.html').write_text(page, encoding='utf-8')


def main(argv=None):
    p = argparse.ArgumentParser(description='Existing draft + reference + customer images -> bindings; Kimi/high, no re-analysis.')
    p.add_argument('--draft', type=Path, required=True)
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--materials', type=Path, nargs='+', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--draft-version', choices=('auto', 'v2-1', 'v2-2'), default='auto')
    p.add_argument('--catalog', type=Path, help='Validated customer catalog from catalog.py complete')
    p.add_argument('--overrides', type=Path, help='Optional JSON {slot_id: asset_id or full file path}')
    p.add_argument('--credentials', '--config', dest='config', type=Path, default=Path('D:/codes/yibu_credentials.local.json'))
    p.add_argument('--openclaw-root', type=Path)
    p.add_argument('--timeout-seconds', type=int, default=600)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--prepare-only', action='store_true', help='Build indexes and labeled reference/material sheets without calling a model')
    a = p.parse_args(argv)
    if a.timeout_seconds <= 0:
        p.error('timeout must be positive')
    output = a.output.resolve()
    if any(output.is_relative_to(folder.resolve()) for folder in a.materials):
        p.error('Output must be outside customer materials directories')
    output.mkdir(parents=True, exist_ok=False)
    result = {'status': 'preparing', 'model_called': False, 'reasoning': 'high'}
    try:
        slots, assets, info, warnings = prepare(a.draft, a.reference, a.materials, output, a.draft_version)
        overrides = resolve_overrides(read_json(a.overrides) if a.overrides else {}, slots, assets)
        wanted = [s for s in slots if 'source_slot_id' not in s and s['id'] not in overrides]
        save(output/'overrides.json', overrides)
        descriptions = {}
        if a.catalog:
            from catalog import catalog_rows
            descriptions = catalog_rows(a.catalog, assets)
            info['catalog_path'] = str(a.catalog.resolve())
            info['catalog_sha256'] = digest(a.catalog)
            save(output/'input.json', info)
            # Stage two sees descriptions first; customer thumbnails remain available on demand.
            image_inputs = [item for item in read_json(output/'images.json') if '客户素材联系表' not in item['label']]
            save(output/'images.json', image_inputs)
        elif wanted and assets:
            raise ValueError('--catalog is required: describe customer materials before matching')
        request_data = {'slots_to_match': wanted, 'locked_bindings': overrides,
                        'assets': [{'asset_id': x['asset_id'], 'size': x['size'], **descriptions.get(x['asset_id'], {})} for x in assets]}
        save(output/'matching-input.json', request_data)
        save(output/'prepared.json', {'input_sha256': digest(output/'input.json'), 'assets_sha256': digest(output/'assets.json'),
                                     'overrides_sha256': digest(output/'overrides.json'), 'warnings': warnings})
        prompt = read_stage('match')+'\n\n以下 JSON 是本次数据：\n'+json.dumps(request_data, ensure_ascii=False)
        (output/'prompt.txt').write_text(prompt, encoding='utf-8')
        if a.prepare_only:
            result.update(status='prepared', input=str(output/'input.json'), assets=str(output/'assets.json'))
        else:
            selected = {}
            if wanted and assets:
                if not a.config:
                    raise ValueError('--config is required for automatic matching')
                node = shutil.which('node')
                if not node:
                    raise ValueError('Node.js is required')
                cmd = [node, str(ROOT/'match_request.mjs'), '--images', str(output/'images.json'), '--prompt', str(output/'prompt.txt'),
                       '--config', str(a.config.resolve()), '--output', str(output/'request'), '--timeout-seconds', str(a.timeout_seconds)]
                if a.openclaw_root: cmd += ['--openclaw-root', str(a.openclaw_root.resolve())]
                if a.dry_run: cmd += ['--dry-run']
                result['model_called'] = None
                with (output/'runner.stdout.txt').open('wb') as stdout, (output/'runner.stderr.txt').open('wb') as stderr:
                    proc = subprocess.run(cmd, stdout=stdout, stderr=stderr, timeout=a.timeout_seconds+30)
                call = read_json(output/'request/call.json')
                result.update(model_called=call['http_dispatches'] > 0, elapsed_seconds=call['elapsed_seconds'])
                if proc.returncode or call['status'] not in ('completed', 'dry_run_verified'):
                    raise ValueError('Matching request failed or was incomplete; see request/call.json')
                if not a.dry_run:
                    selected = validate_selection(read_model_json(output/'request/raw-response.txt'), {s['id'] for s in wanted}, assets)
            elif wanted:
                selected = {s['id']: {'slot_id': s['id'], 'asset_id': None, 'reason': '没有可读取的客户图片'} for s in wanted}
            if a.dry_run:
                result['status'] = 'dry_run_verified' if (output/'request/call.json').exists() else 'dry_run_no_request_needed'
            else:
                if a.catalog and digest(a.catalog) != info['catalog_sha256']:
                    raise ValueError('Catalog changed during matching')
                if digest(a.draft) != info['draft_sha256'] or digest(a.reference) != info['reference_sha256']:
                    raise ValueError('Draft or reference changed during matching')
                for asset in assets:
                    if digest(asset['path']) != asset['sha256']:
                        raise ValueError('Customer asset changed during matching')
                rows, extra = assemble_bindings(slots, assets, selected, overrides)
                warnings += extra
                status = 'completed' if all(row['asset_id'] for row in rows) else 'partial'
                document = {'schema_version': VERSION, 'status': status, 'input': info, 'assets': assets, 'bindings': rows, 'warnings': warnings}
                write_preview(output, slots, assets, rows, warnings)
                save(output/'bindings.json', document)
                result.update(status=status, bindings=str(output/'bindings.json'), preview=str(output/'index.html'),
                              bound_slots=sum(bool(row['asset_id']) for row in rows), total_slots=len(rows),
                              fallback_slots=sum(row['method']=='fallback' for row in rows), visual_status='unreviewed')
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
        result.update(status='failed', error=str(exc))
        audit = output/'request/call.json'
        if audit.is_file():
            try:
                call = read_json(audit);result['model_called'] = call.get('http_dispatches', 0) > 0
            except (OSError, ValueError):
                pass
        result['outcome_unknown'] = result['model_called'] is not False
    save(output/'result.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('completed', 'prepared', 'dry_run_verified', 'dry_run_no_request_needed') else 2


if __name__ == '__main__':
    raise SystemExit(main())
