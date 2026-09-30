"""Compare raw sampling density; do not read or reuse historical analysis."""
import sys,json,math
from pathlib import Path
from statistics import median
from PIL import Image

def read(p):return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else {}
def groups(run):
    index=read(run/'assets/reveal/index.json');result={}
    for item in index.get('records',[]):
        source=item.get('source',{});raw=source.get('raw_file');crop=source.get('crop_box')
        if not raw or not crop:continue
        with Image.open(raw) as image:w,h=image.size
        cw,ch=crop[2]-crop[0],crop[3]-crop[1]
        key=(source['task'],tuple(crop))
        result[key]={'crop_box':crop,'crop_size':[cw,ch],'raw_size':[w,h],
                     'raw_pixels_per_reference_pixel':math.sqrt(w*h/(cw*ch))}
    return list(result.values()),index.get('reference',{}).get('sha256')

root=Path(sys.argv[1]);baseline=Path(sys.argv[2]);manifest=read(root/'manifest.json');items=[]
for name in manifest['names']:
    stem=Path(name).stem;new,nh=groups(root/stem/'run');old,oh=groups(baseline/stem/'run')
    if not new:continue
    ref=read(root/stem/'run/input.json').get('reference_size')
    full=[g for g in old if g['crop_box']==[0,0,*ref]] if ref else []
    base=median(g['raw_pixels_per_reference_pixel'] for g in full) if full and nh==oh else None
    for g in new:g['linear_density_vs_old_full']=round(g['raw_pixels_per_reference_pixel']/base,3) if base else None
    items.append({'name':stem,'same_reference':nh==oh,'old_full':full,'new_groups':new})
note='只读取旧提取记录和原始层尺寸作比较；未向新SDK会话提供旧analysis、bindings或缓存。倍率是相同原图区域的线性采样密度变化，不是主观清晰度或保真度评分；素材语义缺失不会因此恢复。'
(root/'resolution-evidence.json').write_text(json.dumps({'note':note,'items':items},ensure_ascii=False,indent=2),encoding='utf-8')
lines=['# 原始返回像素证据','',note,'','| 样本 | 旧整图原始层尺寸 | 新裁图尺寸 → 原始层尺寸 | 相对线性采样倍率 |','|---|---|---|---|']
for item in items:
    lines.append('| '+item['name']+' | '+str([g['raw_size'] for g in item['old_full']])+' | '+str([(g['crop_size'],g['raw_size']) for g in item['new_groups']])+' | '+str([g['linear_density_vs_old_full'] for g in item['new_groups']])+' |')
(root/'RESOLUTION.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'cases_with_raw_extraction':len(items),'first_example':items[0] if items else None},ensure_ascii=False))
