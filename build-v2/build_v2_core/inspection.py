"""Only submitted rasters are used to construct comparison evidence."""
import html
from pathlib import Path
import time
from PIL import Image, ImageDraw

from .common import BuildError, oriented, sha
from .preview import checker, flatten
from .store import atomic, commit, load, locked
from .workflow import context_id, export, refresh, task_for


def preview(root, ident):
    started = time.monotonic()
    with locked(root) as root:
        state = load(root); refresh(root, state); row = task_for(state, ident)
        if row['status'] not in ('awaiting_visual', 'needs_changes', 'accepted', 'deferred'):
            raise BuildError('preview_state', 'Submit a valid current task version first')
        context = context_id(state)
        directory = root / 'previews' / ident / context
        directory.mkdir(parents=True, exist_ok=True)
        reference = oriented(root / state['snapshot'] / 'inputs/reference.png')
        canvas = checker(tuple(state['input']['canvas_size'])).convert('RGBA')
        painter = ImageDraw.Draw(canvas)
        # Deliberately labeled placeholders; no implicit access/upload of customer photos.
        unit_mode=state['plan'].get('units') is not None
        for slot in ([] if unit_mode else state['passthrough']):
            box = slot['target_box']; painter.rectangle(box, fill='#999999', outline='#666666')
            painter.text((box[0]+3, box[1]+3), 'PHOTO PLACEHOLDER', fill='white')
        assets = {o['key']: o for task in state['tasks'].values() if task.get('active') and
                  task['status'] in ('awaiting_visual', 'needs_changes', 'accepted', 'deferred') for o in task['active']['outputs']}
        layers = ['background' if l['type']=='background' else l['type']+':'+l['id'] for l in state['draft']['layer_order']]
        unit_objects={o['key']:o for t in state['tasks'].values() for o in t['package']['objects'] if o.get('unit_id')}
        if unit_objects:
            positions={k:i for i,k in enumerate(layers)}
            slots={e['key']:e for e in state['passthrough']}
            layers=[k for _,k in sorted([(o['layer_index'],o['key']) for o in unit_objects.values()]+
                                        [(positions[k],k) for k in slots])]

        for key in layers:
            if unit_mode and key in slots:
                box=slots[key]['target_box']; painter.rectangle(box,fill='#999999',outline='#666666')
                painter.text((box[0]+3,box[1]+3),'PHOTO PLACEHOLDER',fill='white')
            if key not in assets: continue
            output = assets[key]; image = oriented(root / output['file'])
            x, y, right, bottom = output.get('paint_box', output['target_box'])
            # Map the FULL raster to its actual paint bounds, retaining scale and offset.
            canvas.alpha_composite(image.resize((right-x, bottom-y), Image.Resampling.LANCZOS), (x, y))
        reference_view = reference.resize(canvas.size, Image.Resampling.LANCZOS)
        combined = Image.new('RGB', (canvas.width*2, canvas.height+30), 'white')
        combined.paste(reference_view.convert('RGB'), (0, 30)); combined.paste(canvas.convert('RGB'), (canvas.width, 30))
        ImageDraw.Draw(combined).text((5, 5), 'REFERENCE / SUBMITTED LAYERS - PHOTO PLACEHOLDERS - NOT FINAL RENDER', fill='black')
        combined.save(directory / 'combined.png')
        evidence = {str((directory/'combined.png').relative_to(root)).replace('\\', '/'): sha(directory/'combined.png')}
        cards = []
        for obj in row['package']['objects']:
            output = assets[obj['key']]; raster = flatten(oriented(root/output['file']))
            if 'paint_box' in output:
                box = output['paint_box']; cw, ch = state['input']['canvas_size']
                crop = reference.crop((round(box[0]*reference.width/cw), round(box[1]*reference.height/ch),
                                       round(box[2]*reference.width/cw), round(box[3]*reference.height/ch))).convert('RGB')
            else:
                crop = reference.crop(obj['reference_box']).convert('RGB')
            crop.thumbnail((600, 500)); raster.thumbnail((600, 500))
            tile = Image.new('RGB', (1200, max(crop.height, raster.height)+35), 'white')
            tile.paste(crop, (0, 35)); tile.paste(raster, (600, 35))
            ImageDraw.Draw(tile).text((5, 5), obj['key']+' / REFERENCE | SUBMITTED '+output['sha256'][:12], fill='black')
            path = directory / (obj['key'].replace(':', '__')+'.png'); tile.save(path)
            relative = str(path.relative_to(root)).replace('\\', '/')
            evidence[relative] = sha(path); cards.append(relative)
        page = '<!doctype html><meta charset="utf-8"><title>Build v2 inspection</title><style>body{font:16px system-ui;margin:24px}img{max-width:100%}</style>'
        page += '<h1>实际资源对照</h1><p>照片为标注占位；仅检查预览，不是最终 renders。透明边距保留。</p>'
        page += ''.join('<figure><img src="'+html.escape(Path(p).name)+'"><figcaption>'+html.escape(p)+'</figcaption></figure>' for p in [next(iter(evidence)), *cards])
        (directory/'index.html').write_text(page, encoding='utf-8')
        row['preview'] = {'context_id': context, 'submission_id': row['active']['id'], 'evidence': evidence}
        commit(root, state, 'preview', {'task': ident, 'elapsed_seconds': time.monotonic()-started}); export(root, state)
        return {'page': str(directory/'index.html'), **row['preview']}
