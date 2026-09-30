"""Offline backfill experiment using frozen design/photos and existing Reveal PNGs."""
import argparse
import copy
import html
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from common import read, save, sha, verify_source
from photos import fit_image
from overlays import draw_overlay
from effects import scale_style

BASE = Path('D:/codes/collage_outputs/v5-eval-merged-first-20260928')
CACHE = BASE / '360_overlay_results'
NAMES = ['20260908-192922', '拼贴2', '拼贴7', '海边人像拼图']


def area(b):
    return max(0, b[2]-b[0])*max(0, b[3]-b[1])


def contains(outer, inner):
    b = [max(outer[0],inner[0]), max(outer[1],inner[1]),
         min(outer[2],inner[2]), min(outer[3],inner[3])]
    return area(b)/max(1, area(inner))


def window_polygon(box, rotation):
    l,t,r,b = box; cx,cy = (l+r)/2,(t+b)/2
    angle = math.radians(rotation); co,si = math.cos(angle),math.sin(angle)
    return [(cx+(x-cx)*co-(y-cy)*si, cy+(x-cx)*si+(y-cy)*co)
            for x,y in [(l,t),(r,t),(r,b),(l,b)]]


def run_case(name, output):
    started = time.perf_counter()
    old = BASE/name/'run'; dest = output/name; dest.mkdir(parents=True,exist_ok=True)
    scene = read(old/'scene.json'); result = read(old/'result.json')
    analysis = read(old/'analysis.json')
    objects = {o['id']:o for o in scene['objects']}
    resources = {r['id']:r for r in result['resources']}
    photos = [o for o in scene['objects'] if o['kind']=='photo']
    rw,rh = scene['reference_size']; w,h = scene['canvas_size']; scale=w/rw
    with Image.open(old/'prepared/reference.png') as im: reference=im.convert('RGB')
    with Image.open(CACHE/name/'input.png') as im:
        if im.size != reference.size or not np.array_equal(np.asarray(im.convert('RGB')),np.asarray(reference)):
            raise ValueError('Cached extraction belongs to a different reference')
    for o in photos: verify_source(o['source'])
    recovered = {}; fallback = {}; frames = {}; details=[]; suppressed={}
    for mapping in sorted((CACHE/name).glob('batch_*/layers_mapping.json')):
        for entry in read(mapping):
            oid=entry['id']; obj=objects[oid]
            if obj['kind']!='overlay':raise ValueError('Expected an overlay cache')
            src=CACHE/entry['image']
            with Image.open(src) as im: raw=im.convert('RGBA')
            detail={'id':oid,'source':str(src),'source_sha256':sha(src),'api_size':list(raw.size)}
            enclosed=[p for p in photos if p.get('mode','cover')=='cover'
                      and contains(obj['bbox'],p['bbox'])>.94 and area(p['bbox'])<area(obj['bbox'])]
            frame_hint=(obj.get('style',{}).get('shape')=='frame'
                        or any(s in obj['label'].lower() for s in ['相框','相纸','拍立得','polaroid','frame']))
            broad = len(enclosed)>1 or (area(obj['bbox'])/(rw*rh)>.22
                    and sum(contains(obj['bbox'],p['bbox'])>.35 for p in photos)>1)
            patterned_band = '条纹带' in obj['label'] and obj.get('style',{}).get('shape')=='rectangle'
            if broad or patterned_band:
                reason='复杂底板夹带照片风险，使用本地底板' if broad else '条纹带存在人物残留风险，使用本地条纹'
                fallback[oid]=reason;detail.update(action='local_fallback',reason=reason)
                details.append(detail);continue
            full=raw.resize((w,h),Image.Resampling.LANCZOS)
            if frame_hint and len(enclosed)==1:
                photo=enclosed[0]
                # Two service pixels of overlap hide thin residual source-photo edges.
                pad=2*w/raw.width
                box=[round(v*scale) for v in photo['bbox']]
                box=[max(0,round(box[0]-pad)),max(0,round(box[1]-pad)),
                     min(w,round(box[2]+pad)),min(h,round(box[3]+pad))]
                mask=Image.new('L',(w,h));ImageDraw.Draw(mask).polygon(window_polygon(box,photo.get('rotation',0)),fill=255)
                alpha=np.asarray(full.getchannel('A')).copy();alpha[np.asarray(mask)>0]=0
                full.putalpha(Image.fromarray(alpha))
                frames[photo['id']]={'box':box,'mask':mask,'owner':oid}
                detail.update(action='clear_photo_window',photo_id=photo['id'],window_bbox=box,
                              note='矩形窗口原型；复杂边缘侵入装饰需复核')
                mask.save(dest/(oid+'-window.png'))
            else:detail['action']='reuse_layer'
            recovered[oid]=full
            target=dest/(oid+'-clean.png');full.save(target);detail['clean_file']=str(target)
            details.append(detail)
    # Embedded text is a deduplication candidate, not proof of exact OCR fidelity.
    for oid,o in objects.items():
        if o['kind']=='photo' or o.get('text_status')=='unreadable':continue
        candidates=[]
        if o.get('parent_id') in recovered:candidates.append(o['parent_id'])
        if o['kind']=='text':
            candidates += [pid for pid in recovered if pid!=oid and contains(objects[pid]['bbox'],o['bbox'])>.95]
        for parent in dict.fromkeys(candidates):
            box=[round(v*scale) for v in o['bbox']]
            patch=np.asarray(recovered[parent].crop(box));valid=patch[:,:,3]>128
            if valid.mean()<.45:continue
            values=patch[:,:,:3].mean(2)[valid]
            if len(values) and np.percentile(values,95)-np.percentile(values,5)>55:
                suppressed[oid]=parent;break
    canvas=Image.new('RGBA',(w,h),'white')
    for oid in scene['layer_order']:
        if oid in suppressed:continue
        o=objects[oid];box=[round(v*scale) for v in o['bbox']]
        if oid in recovered:
            # Full-canvas layers already encode location, angle, shadow and alpha.
            canvas.alpha_composite(recovered[oid]);continue
        if oid in frames:
            info=frames[oid];box=info['box'];binding=o['binding']
            with Image.open(verify_source(o['source'])) as im: tile=ImageOps.exif_transpose(im).convert('RGBA')
            if binding.get('mirror_x'):tile=ImageOps.mirror(tile)
            crop=binding.get('source_crop',[0,0,1,1])
            tile=tile.crop([round(crop[0]*tile.width),round(crop[1]*tile.height),round(crop[2]*tile.width),round(crop[3]*tile.height)])
            tile=fit_image(tile,(box[2]-box[0],box[3]-box[1]),binding.get('crop_center',[.5,.5]))
        elif oid in fallback:
            local=copy.deepcopy(o);local['method']='local';local['style']=scale_style(o.get('style',{}),scale)
            tile,_=draw_overlay(local,(box[2]-box[0],box[3]-box[1]),reference,photos,dest,None)
        else:
            record=resources[oid]
            if sha(record['file'])!=record['sha256']:raise ValueError('Frozen resource changed')
            with Image.open(record['file']) as im:tile=im.convert('RGBA')
        if o.get('rotation',0):tile=tile.rotate(-o['rotation'],Image.Resampling.BICUBIC,expand=True)
        xy=(round((box[0]+box[2]-tile.width)/2),round((box[1]+box[3]-tile.height)/2))
        layer=Image.new('RGBA',(w,h));layer.alpha_composite(tile,xy)
        if oid in frames:
            alpha=np.asarray(layer.getchannel('A')).copy();alpha[np.asarray(frames[oid]['mask'])==0]=0
            layer.putalpha(Image.fromarray(alpha))
        canvas.alpha_composite(layer)
    final=dest/'first.png';canvas.convert('RGB').save(final)
    comparison=Image.new('RGB',(w*3,h),'white')
    comparison.paste(reference.resize((w,h)),(0,0))
    with Image.open(old/'previews/first.png') as im:comparison.paste(im.resize((w,h)),(w,0))
    comparison.paste(canvas.convert('RGB'),(2*w,0));comparison.save(dest/'comparison.png')
    record={'name':name,'status':'unreviewed_prototype','seconds':time.perf_counter()-started,
            'reference':str(old/'prepared/reference.png'),'previous':str(old/'previews/first.png'),
            'first':str(final),'comparison':str(dest/'comparison.png'),
            'reused_layers':len(recovered),'frame_windows':len(frames),'fallbacks':fallback,
            'embedded_content_candidates':suppressed,'layers':details,
            'analysis_sha256':sha(old/'analysis.json'),'bindings_sha256':sha(old/'bindings.json'),
            'note':'离线回填验证；未重新分析/请求API；嵌入文字和矩形窗口仍需视觉复核，不是成品通过。'}
    save(dest/'backfill.json',record);return record


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    root=Path(a.output);root.mkdir(parents=True,exist_ok=True)
    rows=[]
    for name in NAMES:
        row=run_case(name,root);rows.append(row)
        print(json.dumps({k:row[k] for k in ['name','seconds','reused_layers','frame_windows','fallbacks']},ensure_ascii=False),flush=True)
    save(root/'summary.json',rows)
    cards=[]
    for r in rows:
        pictures=''.join('<figure><figcaption>'+label+'</figcaption><a href="'+Path(r[key]).as_uri()+'" target="_blank"><img loading="lazy" src="'+Path(r[key]).as_uri()+'"></a></figure>' for label,key in [('参考图','reference'),('原本地首版','previous'),('提取后回填','first')])
        notes='；'.join(k+'：'+v for k,v in r['fallbacks'].items()) or '无自动回退'
        cards.append('<section><h2>'+html.escape(r['name'])+'</h2><p>提取图层 '+str(r['reused_layers'])+' · 清理窗口 '+str(r['frame_windows'])+' · 离线处理 '+str(round(r['seconds'],1))+' 秒</p><div class="grid">'+pictures+'</div><p>'+html.escape(notes)+'</p><details><summary>嵌入文字/子装饰去重候选</summary><pre>'+html.escape(json.dumps(r['embedded_content_candidates'],ensure_ascii=False,indent=2))+'</pre></details><a href="'+(root/r['name']/'backfill.json').as_uri()+'">完整处理记录</a></section>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>360提取素材回填预览</title><style>body{margin:24px;background:#eef0f3;color:#20232a;font:16px/1.6 system-ui}section{background:white;padding:20px;margin:24px 0;border-radius:12px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}figure{margin:0}img{width:100%}figcaption{font-weight:700;margin-bottom:8px}pre{white-space:pre-wrap}a{color:#245cac}header{max-width:1000px}.note{padding:14px;background:#fff3cf}@media(max-width:700px){body{margin:10px}.grid{grid-template-columns:1fr}}</style><header><h1>先回填，看真实结果</h1><p>同一份旧分析、同一份照片绑定，复用已下载的360图层。左：参考；中：原首版；右：清理窗口并回填后的预览。点图打开原尺寸。</p><p class="note">这是离线回填原型，尚未完成模型视觉验收。复杂照片底板采用本地回退；嵌入文字去重与窗口边缘可能仍有缺陷。显示耗时不包含此前的分析和API提取。</p></header>'''+''.join(cards)+'</html>'
    (root/'index.html').write_text(page,encoding='utf-8')


if __name__=='__main__':main()
