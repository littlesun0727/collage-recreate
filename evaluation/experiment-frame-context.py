"""A/B probe: preserve original extraction targets and append associated photo/text boxes."""
import argparse
import concurrent.futures
import shutil
import sys
import time
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import read,save,sha,now,timed,locked
from reveal import fetch_batch,api_key,request_object,load_cache
from prepare import prepare
from scene import compile_scene
from render import render

CASES={'拼贴2':['frame_top_left','frame_top_right','frame_bottom_left','frame_bottom_right'],
       '拼贴7':['main_polaroid','rear_right_polaroid','front_right_polaroid'],
       '海边人像拼图':['right_rain_card']}


def intersects(a,b):return min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1])


def sheet(reference,a,b,frames,case,clean=False):
    """Diagnostic crops, identical coordinates/scales for A and B; checkerboard shows alpha."""
    size=Image.open(reference).size;ref=Image.open(reference).convert('RGBA');font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',17)
    cw,ch=430,590;out=Image.new('RGB',(cw*3,ch*len(frames)), '#ffffff');draw=ImageDraw.Draw(out)
    records=[]
    for row,frame in enumerate(frames):
        oid=frame['id'];box=frame['bbox'];crop=ref.crop(box);cols=[crop]
        for layers in [a,b]:
            path=layers.get(oid,{}).get('file');layer=Image.open(path).convert('RGBA').resize(size,Image.Resampling.LANCZOS) if path else Image.new('RGBA',size)
            cols.append(layer.crop(box))
        for col,img in enumerate(cols):
            img.thumbnail((cw-20,ch-65));bg=Image.new('RGBA',img.size,'white');d=ImageDraw.Draw(bg)
            for y in range(0,img.height,14):
                for x in range(0,img.width,14):
                    if (x//14+y//14)%2:d.rectangle((x,y,x+13,y+13),fill='#d9d9d9')
            bg.alpha_composite(img);out.paste(bg.convert('RGB'),(col*cw+10,row*ch+50))
            draw.text((col*cw+10,row*ch+7),['Reference','A: original targets','B: + photo/text boxes'][col],font=font,fill='black')
        draw.text((10,row*ch+29),oid,font=font,fill='black')
    file=case/('clean-comparison.jpg' if clean else 'raw-comparison.jpg');out.save(file,quality=94)
    return str(file)


def job(name,base,root,dry_run):
    case=root/name;old=base/name/'run';run=case/'run';cache=case/'context-cache';statefile=case/'experiment-state.json'
    if statefile.exists() and read(statefile).get('status')=='completed':return read(statefile)
    case.mkdir(parents=True,exist_ok=True);analysis=read(old/'analysis.json');by_id={o['id']:o for o in analysis['objects']}
    folders=list((old/'assets/reveal/api').glob('batch_*'))
    if len(folders)!=1:raise ValueError('Probe requires one original API batch to preserve all target context')
    original=read(folders[0]/'request_meta.json')['objects'];requested={o['id']:o for o in original}
    frames=[by_id[oid] for oid in CASES[name]];contexts={}
    for frame in frames:
        photo=by_id[frame['photo_id']]
        contexts[photo['id']]={'object':photo,'role':'photo','owners':[frame['id']]}
        for text in analysis['objects']:
            if text['kind']!='text':continue
            explicit=text.get('embedded_in')==frame['id'] or text.get('parent_id')==frame['id']
            if explicit or intersects(text['bbox'],requested[frame['id']]['bbox']):
                ctx=contexts.setdefault(text['id'],{'object':text,'role':'text','owners':[]})
                if frame['id'] not in ctx['owners']:ctx['owners'].append(frame['id'])
    extras=[request_object({'id':'context_'+oid,'bbox':c['object']['bbox']},analysis['reference_size'],0 if c['role']=='photo' else .1) for oid,c in contexts.items()]
    for obj in extras:
        if contexts[obj['id'].removeprefix('context_')]['role']=='photo' and obj['bbox']!=obj['original_bbox']:
            raise ValueError('Photo context must keep its exact original bbox; API minimum would alter it')
    objects=original+extras
    if len(objects)>20:raise ValueError('Probe would exceed 20 targets; do not silently split A/B context')
    plan={'case':name,'baseline_run':str(old),'selected_frames':CASES[name],
        'original_target_count':len(original),'total_target_count':len(objects),'original_targets_unchanged':objects[:len(original)]==original,
        'context':[{'source_id':oid,'request_id':'context_'+oid,'role':c['role'],'owner_ids':c['owners'],'original_bbox':c['object']['bbox']} for oid,c in contexts.items()],
        'objects':objects,'padding_by_role':{'overlay':.1,'photo':0,'text':.1},'note':'IDs/types are local bookkeeping; API receives only image_boxes. Context layers are excluded from production composition.'}
    save(case/'request-plan.json',plan)
    if dry_run:print(name,'DRY',len(original),'->',len(objects),flush=True);return plan
    started=time.perf_counter();state={'case':name,'status':'running','started_at':now()};save(statefile,state)
    try:
        cache.mkdir(exist_ok=True);reference=old/'assets/reveal/api/input.png'
        if not (cache/'input.png').exists():shutil.copyfile(reference,cache/'input.png')
        if sha(cache/'input.png')!=sha(reference):raise ValueError('Reference bytes changed')
        fetch_batch(cache/'batch_context',cache/'input.png',objects,api_key('D:/codes/.env'),timeout=360)
        a,_=load_cache(old/'assets/reveal/api',old/'prepared/reference.png')
        b,_=load_cache(cache,old/'prepared/reference.png')
        # Only original design target IDs can be consumed by the unchanged compiler.
        meta=read(old/'input.json')
        if not run.exists():prepare(meta['original_reference']['file'],meta['materials'],run,width=meta['output_width'],cutout_model=meta.get('cutout_model'))
        for f in ['analysis.json','bindings.json']:
            if (run/f).exists() and sha(run/f)!=sha(old/f):raise ValueError('Changed frozen input')
            shutil.copyfile(old/f,run/f)
        with locked(run),timed(run,'build'):
            compile_scene(run,{'enabled':True,'remote':False,'cache':str(cache),'padding':.1})
            render(run)
            result=read(run/'result.json')
        ai=read(old/'assets/reveal/index.json');bi=read(run/'assets/reveal/index.json')
        ac={r['id']:r['clean'] for r in ai['records'] if r.get('clean')};bc={r['id']:r['clean'] for r in bi['records'] if r.get('clean')}
        crops=[{'id':f['id'],'bbox':requested[f['id']]['bbox']} for f in frames]
        rawsheet=sheet(old/'prepared/reference.png',a,b,crops,case)
        cleansheet=sheet(old/'prepared/reference.png',ac,bc,crops,case,True)
        metrics=[]
        for f in frames:
            oid=f['id'];ar=next(r for r in ai['records'] if r['id']==oid);maskfile=ar.get('window',{}).get('file')
            values={}
            if maskfile:
                mask=np.asarray(Image.open(maskfile).convert('L'))>0
                for side,layers in [('A',a),('B',b)]:
                    if oid not in layers:values[side]=None;continue
                    im=Image.open(layers[oid]['file']).convert('RGBA').resize(tuple(analysis['reference_size']),Image.Resampling.LANCZOS)
                    values[side]=round(float((np.asarray(im.getchannel('A'))[mask]>16).mean()),6)
            metrics.append({'frame_id':oid,'raw_window_alpha_coverage':values,'diagnostic_only':True})
        context_ids={x['id'] for x in extras}
        if any(r['id'] in context_ids for r in result['resources']):raise ValueError('Context image leaked into customer composition')
        save(case/'comparison.json',{'case':name,'plan':plan,'a_run':str(old),'b_run':str(run),
            'raw_sheet':rawsheet,'clean_sheet':cleansheet,'metrics':metrics,
            'returned_design_ids':sorted(set(b)-context_ids),'returned_context_ids':sorted(set(b)&context_ids),
            'missing_original_targets':sorted(set(requested)-set(b)),'inputs_unchanged':all(sha(old/f)==sha(run/f) for f in ['analysis.json','bindings.json']),
            'context_layers_excluded':True,'b_incomplete':result['incomplete_objects']})
        state.update(status='completed',elapsed_seconds=round(time.perf_counter()-started,3))
    except Exception as exc:state.update(status='failed',error=f'{type(exc).__name__}: {exc}')
    save(statefile,state);print(name,state['status'],state.get('elapsed_seconds'),flush=True);return state


def main():
    p=argparse.ArgumentParser();p.add_argument('--baseline',required=True);p.add_argument('--root',required=True);p.add_argument('--dry-run',action='store_true');args=p.parse_args()
    base=Path(args.baseline).resolve();root=Path(args.root).resolve();root.mkdir(parents=True,exist_ok=True)
    save(root/'manifest.json',{'baseline':str(base),'cases':list(CASES),'model':'gpt-5.6-sol','effort':'medium','experiment':'append-context-boxes','baseline_reused':True,'new_api_tasks_planned':3,'padding_by_role':{'overlay':.1,'photo':0,'text':.1}})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda name:job(name,base,root,args.dry_run),CASES))
    if not args.dry_run:save(root/'experiment-batch.json',results)


if __name__=='__main__':main()
