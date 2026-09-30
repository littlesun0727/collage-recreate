"""Deterministic preparation and evidence for analysis-only experiments."""
import argparse
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw, ImageFont
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import save,read,sha
from validate import analysis_check
from prepare import boxes


def prepare(reference,run,max_edge=0):
    run=Path(run)
    if run.exists():raise ValueError('Use a new analysis directory')
    (run/'prepared').mkdir(parents=True)
    with Image.open(reference) as raw:im=ImageOps.exif_transpose(raw).convert('RGB')
    original=list(im.size)
    if max_edge:im.thumbnail((max_edge,max_edge),Image.Resampling.LANCZOS)
    path=run/'prepared/reference.png';im.save(path)
    save(run/'input.json',{'reference_size':list(im.size),'original_size':original,'reference':{'file':str(path),'sha256':sha(path)},'original_reference':str(Path(reference).resolve())})
    return {'reference':str(path),'reference_size':list(im.size),'original_size':original}


def freeze(run):
    run=Path(run);a=analysis_check(read(run/'analysis.json'),read(run/'input.json'))
    frozen=run/'analysis-first.json'
    if frozen.exists():raise ValueError('First analysis already frozen; do not overwrite')
    path=boxes(run,a);shutil.copyfile(run/'analysis.json',frozen);shutil.copyfile(path,run/'previews/boxes-first.png')
    return {'schema_valid':True,'object_count':len(a['objects']),'analysis':str(frozen),'boxes':str(run/'previews/boxes-first.png')}


def evidence(run):
    run=Path(run);a=read(run/'analysis-first.json')
    with Image.open(run/'prepared/reference.png') as raw:im=raw.convert('RGB')
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',16)
    # Review-only grid: analyst never sees this before freezing its first JSON.
    grid=im.copy();d=ImageDraw.Draw(grid);w,h=im.size
    for n in range(1,10):
        x=round(w*n/10);y=round(h*n/10)
        d.line((x,0,x,h),fill='#44bbdd',width=1);d.line((0,y,w,y),fill='#44bbdd',width=1)
        d.text((x+2,2),str(x),font=font,fill='black',stroke_width=2,stroke_fill='white')
        d.text((2,y+2),str(y),font=font,fill='black',stroke_width=2,stroke_fill='white')
    gp=run/'previews/review-grid.png';grid.save(gp)
    objects=[o for o in a['objects'] if o['kind']!='background'];paths=[]
    for start in range(0,len(objects),8):
        sheet=Image.new('RGB',(1200,1000),'#eeeeee');draw=ImageDraw.Draw(sheet)
        for j,o in enumerate(objects[start:start+8]):
            x=(j%4)*300;y=(j//4)*500;l,t,r,b=o['bbox']
            draw.text((x+6,y+6),o['id'],font=font,fill='black')
            draw.text((x+6,y+29),str(o['bbox']),font=font,fill='black')
            crop=im.crop((l,t,r,b));crop.thumbnail((286,195))
            sheet.paste(crop,(x+(300-crop.width)//2,y+58))
            pad=max(30,round(max(r-l,b-t)*.3));cl=max(0,l-pad);ct=max(0,t-pad);cr=min(w,r+pad);cb=min(h,b+pad)
            context=im.crop((cl,ct,cr,cb));cd=ImageDraw.Draw(context);cd.rectangle((l-cl,t-ct,r-cl-1,b-ct-1),outline='#ff2222',width=max(2,w//400));context.thumbnail((286,210));sheet.paste(context,(x+(300-context.width)//2,y+275))
        p=run/'previews'/f'box-crops-{start//8+1}.jpg';sheet.save(p,quality=92);paths.append(str(p))
    result={'reference_size':[w,h],'grid':str(gp),'crop_sheets':paths,'analysis_sha256':sha(run/'analysis-first.json'),'boxes_sha256':sha(run/'previews/boxes-first.png')}
    inputs=read(run/'input.json')
    if inputs['original_size']!=[w,h]:
        ow,oh=inputs['original_size'];sx=ow/w;sy=oh/h;original=deepcopy(a);original['reference_size']=[ow,oh]
        with Image.open(inputs['original_reference']) as raw:original_image=ImageOps.exif_transpose(raw).convert('RGB')
        od=ImageDraw.Draw(original_image)
        for o in original['objects']:
            l,t,r,b=o['bbox'];o['bbox']=[round(l*sx),round(t*sy),round(r*sx),round(b*sy)]
            for key in ['font_size','stroke_width','outline_width']:
                if key in o.get('style',{}):o['style'][key]*=sx
            if 'dash' in o.get('style',{}):o['style']['dash']=[v*sx for v in o['style']['dash']]
            od.rectangle(o['bbox'],outline={'photo':'#00bb00','text':'#ff2222'}.get(o['kind'],'#0077ff'),width=2)
            od.text((o['bbox'][0]+2,o['bbox'][1]+2),o['id'],font=font,fill='#0077ff',stroke_width=1,stroke_fill='white')
        analysis_check(original);save(run/'analysis-original.json',original)
        original_image.save(run/'previews/boxes-original.png');result['original_boxes']=str(run/'previews/boxes-original.png')
    save(run/'evidence.json',result);return result


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','freeze','evidence']);p.add_argument('--run',required=True);p.add_argument('--reference');p.add_argument('--max-edge',type=int,default=0);args=p.parse_args()
    if args.command=='prepare':result=prepare(args.reference,args.run,args.max_edge)
    elif args.command=='freeze':result=freeze(args.run)
    else:result=evidence(args.run)
    import json
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
