from copy import deepcopy
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageChops
from common import read, save, sha, fingerprint, verify_source, now, ROOT
from photos import make_photo
from text import render_text
from overlays import draw_overlay
from scene import load_scene, checked_review
from effects import scale_style,shadow_layer


def render(run):
    run=Path(run);s=load_scene(run);w,h=s['canvas_size'];scale=w/s['reference_size'][0]
    if w*h>20_000_000:raise ValueError('Canvas exceeds 20 million pixels')
    # Invalidate old visual acceptance before attempting a new render.
    save(run/'result.json',{'exported':False,'renders_verified':False,'status':'rendering','at':now()})
    with Image.open(verify_source(s['reference'])) as raw:reference=raw.convert('RGB')
    photo_objects=[o for o in s['objects'] if o['kind']=='photo'];by_id={o['id']:o for o in s['objects']}
    canvas=Image.new('RGBA',(w,h),'white');layers=[];issues=[];incomplete=[];records=[]
    code=fingerprint({str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'scripts').rglob('*.py')})
    grouped={member:group for group in s.get('generated_groups',[]) for member in group['member_ids']}
    def recovered_owner(obj):return obj.get('recovery_owner') or obj.get('embedded_owner')
    def embedded_in_recovered(obj):
        owner=recovered_owner(obj)
        return bool(owner and not obj.get('generated') and obj['id'] not in grouped
                    and not by_id[owner].get('generated') and owner not in grouped)
    for oid in s['layer_order']:
        if embedded_in_recovered(by_id[oid]):continue
        group_info=grouped.get(oid)
        if group_info and group_info['primary']!=oid:continue
        original=by_id[oid];o=deepcopy(original)
        if group_info:o.update(bbox=group_info['bbox'],generated=group_info['source'],kind='overlay',rotation=0,style={},text_unresolved=any(by_id[i]['text_unresolved'] for i in group_info['member_ids']))
        if o.get('recovered') and not o.get('generated'):
            dx,dy=o.get('extracted_offset',[0,0]);rw,rh=s['reference_size']
            o.update(bbox=[dx,dy,rw+dx,rh+dy],rotation=0,style={})
        box=[round(n*scale) for n in o['bbox']];size=(max(1,box[2]-box[0]),max(1,box[3]-box[1]))
        o['style']=scale_style(o['style'],scale)
        group='photos' if o['kind']=='photo' else 'text' if o['kind']=='text' else 'overlays'
        folder=run/'assets'/group;folder.mkdir(parents=True,exist_ok=True)
        path=folder/(oid+'.png');receipt_path=folder/(oid+'.json')
        key=fingerprint([o,size,s['reference'],s.get('cutout_model'),code])
        if o['kind']=='photo':verify_source(o['source'])
        cached=read(receipt_path) if receipt_path.exists() else {}
        cm=cached.get('metadata',{}).get('content_mask')
        mask_valid=not cm or (Path(cm).exists() and sha(cm)==cached['metadata'].get('content_mask_sha256'))
        if cached.get('input_key')==key and path.exists() and cached.get('sha256')==sha(path) and mask_valid:
            with Image.open(path) as raw:tile=raw.convert('RGBA')
            metadata=cached['metadata']
        else:
            if o.get('gate',{}).get('status') in ['pending','rejected'] and not o.get('gate_local') and not o.get('generated'):
                tile=Image.new('RGBA',size)
                metadata={'quality':'unresolved','method':'quarantined','note':o.get('reveal_warning','Asset gate blocked this layer')}
            elif o['kind']=='photo':tile,metadata=make_photo(o,o['source'],o['binding'],size,run,s.get('cutout_model'));metadata['quality']='ready'
            elif o.get('generated'):
                generated=o['generated'];p=verify_source(generated)
                with Image.open(p) as raw:tile=raw.convert('RGBA').resize(size,Image.Resampling.LANCZOS)
                metadata={'quality':'needs_review','method':'generated','request':generated['request']}
            elif o.get('recovered'):
                asset=o['recovered']
                with Image.open(verify_source(asset)) as raw:tile=raw.convert('RGBA').resize(size,Image.Resampling.LANCZOS)
                metadata={'quality':'needs_review','method':'reveal','resource_type':'reference_extraction','source':asset['source'],
                          'reference_crop':asset['reference_crop'],'foreground':asset['foreground'],'note':'Recovered decoration; inspect window edges, embedded text and reference contamination'}
            elif o['kind']=='text' and not o['text_unresolved']:
                tile,metadata=render_text(o['text'],size,o['style']);metadata['quality']='approximate'
            else:
                tile,metadata=draw_overlay(o,size,reference,photo_objects,run,s.get('cutout_model'))
            if o.get('text_unresolved'):metadata.update(quality='unresolved',note='Unknown text has no replacement')
            if o.get('reveal_warning') and not o.get('generated') and not o.get('gate_local'):
                metadata.update(quality='unresolved',note=o['reveal_warning'])
            if o.get('gate_local'):
                metadata['note']='Explicit local fallback; approximate and requires visual review'
            opacity=o['style'].get('opacity',1)
            if opacity!=1:tile.putalpha(tile.getchannel('A').point(lambda v:round(v*opacity)))
            tile.save(path);save(receipt_path,{'input_key':key,'sha256':sha(path),'metadata':metadata})
        if metadata['quality'] in ['placeholder','unresolved']:incomplete.append(oid)
        if metadata.get('note'):issues.append({'id':oid,'note':metadata['note']})
        for note in original.get('reveal_notes',[]):issues.append({'id':oid,'note':note})
        content_alpha=None
        if o['kind']=='photo':
            with Image.open(metadata['content_mask']) as raw:content_alpha=raw.convert('L')
            if o['style'].get('opacity',1)!=1:content_alpha=content_alpha.point(lambda v:round(v*o['style']['opacity']))
        if o.get('rotation',0):
            tile=tile.rotate(-o['rotation'],Image.Resampling.BICUBIC,expand=True)
            if content_alpha is not None:content_alpha=content_alpha.rotate(-o['rotation'],Image.Resampling.BICUBIC,expand=True)
        x=round((box[0]+box[2]-tile.width)/2);y=round((box[1]+box[3]-tile.height)/2)
        layer=Image.new('RGBA',(w,h));layer.alpha_composite(tile,(x,y))
        if o['kind']=='overlay' and o.get('method','local')=='local' and not o.get('recovered') and not o.get('generated'):
            # Newly drawn carriers use the existing customer-photo geometry for holes.
            # No extracted pixels or customer crop coordinates are modified.
            from reveal_assets import make_window
            for photo in photo_objects:
                if photo.get('mode','cover')!='cover' or not (photo['id']==o.get('photo_id') or photo.get('parent_id')==oid):continue
                window=make_window((w,h),[round(v*scale) for v in photo.get('window_bbox',photo['bbox'])],
                                   photo.get('rotation',0),photo['style'].get('corner_radius',0)*scale)
                layer.putalpha(ImageChops.multiply(layer.getchannel('A'),ImageChops.invert(window)))
        photo_alpha=Image.new('L',(w,h)) if content_alpha is not None else None
        if photo_alpha is not None:photo_alpha.paste(content_alpha,(x,y))
        if o.get('photo_window'):
            with Image.open(verify_source(o['photo_window'])) as raw:window=raw.convert('L').resize((w,h),Image.Resampling.LANCZOS)
            layer.putalpha(ImageChops.multiply(layer.getchannel('A'),window))
            photo_alpha=ImageChops.multiply(photo_alpha,window)
        if 'shadow' in o['style']:canvas=Image.alpha_composite(canvas,shadow_layer(layer.getchannel('A'),o['style']['shadow']))
        canvas=Image.alpha_composite(canvas,layer)
        layers.append((oid,o['kind'],layer.getchannel('A'),photo_alpha))
        for member in group_info['member_ids'] if group_info else [oid]:
            records.append({'id':member,'kind':by_id[member]['kind'],'file':str(path),'sha256':sha(path),'quality':metadata['quality'],'source':metadata.get('source'),'metadata':metadata,'group':group_info['primary'] if group_info else None})
    for obj in s['objects']:
        if embedded_in_recovered(obj):
            owner=next(r for r in records if r['id']==recovered_owner(obj))
            records.append({**owner,'id':obj['id'],'kind':obj['kind'],'group':recovered_owner(obj),
                            'metadata':{**owner['metadata'],'embedded_content':True}})
    for oid,owners in s.get('reveal_unconfirmed_text',{}).items():
        issues.append({'id':oid,'note':'Check possible duplicated embedded text in '+', '.join(owners)})
    visibility={};trans=np.ones((h,w),dtype='float32')
    for oid,kind,alpha,photo_alpha in reversed(layers):
        a=np.asarray(alpha,dtype='float32')/255
        if kind=='photo':
            customer=np.asarray(photo_alpha,dtype='float32')/255
            fraction=float((customer*trans).sum()/max(float(customer.sum()),1))
            visibility[oid]=round(fraction,5)
            if fraction<.005:issues.append({'id':oid,'note':'Customer photo fully or almost fully occluded'});incomplete.append(oid)
        trans*=1-a
    final=canvas.convert('RGB');tmp=run/'final.tmp.png';final.save(tmp);tmp.replace(run/'final.png')
    previews=run/'previews';previews.mkdir(exist_ok=True)
    if not (previews/'first.png').exists():
        final.save(previews/'first.png');save(previews/'first-scene.json',s)
    comp=Image.new('RGB',(w*2,h),'#eeeeee');comp.paste(reference.resize((w,h)),(0,0));comp.paste(final,(w,0));comp.save(previews/'comparison.png')
    from review_previews import publish as review_previews
    review_regions=review_previews(run,s,reference,final)
    from extraction import sheets
    extraction_sheets=sheets(run,[r for r in records if not r['metadata'].get('embedded_content')])
    proof=all(r['kind']!='photo' or r['metadata'].get('resource_type')=='customer_photo' for r in records)
    result={'schema_version':'collage-result-v1','exported':True,'status':'preview','renders_verified':False,'coverage_complete':len(records)==len(s['objects']),'customer_photos_verified':proof and len(visibility)==len(photo_objects),'photo_visible_fractions':visibility,'incomplete_objects':sorted(set(incomplete)),'issues':issues,'scene_sha256':sha(run/'scene.json'),'final_sha256':sha(run/'final.png'),'resources':records,'visual_review':None,'at':now()}
    result['extraction_sheets']=extraction_sheets
    result['review_regions']=review_regions
    if s.get('asset_gate'):
        result['asset_gate']=s['asset_gate'];result['asset_gate_summary']=s['asset_gate_summary']
    if s.get('reveal_index'):result['reveal_index']=s['reveal_index']
    result['render_id']=fingerprint([result['scene_sha256'],result['final_sha256']])[:16]
    save(run/'result.json',result)
    return {k:v for k,v in result.items() if k!='resources'}


def accept_review(run,path):
    run=Path(run);s,r=checked_review(run,path);result=read(run/'result.json')
    if not result.get('exported') or result['scene_sha256']!=sha(run/'scene.json') or result['final_sha256']!=sha(run/'final.png'):
        raise ValueError('Render result is stale; render before review')
    for item in result['resources']:
        if sha(item['file'])!=item['sha256']:raise ValueError('Resource changed since rendering')
        if item.get('source'):verify_source(item['source'])
        cm=item.get('metadata',{}).get('content_mask')
        if cm and sha(cm)!=item['metadata']['content_mask_sha256']:raise ValueError('Photo content mask changed since rendering')
    passed=r['verdict']=='pass'
    if passed and (result['incomplete_objects'] or not result['customer_photos_verified']):
        raise ValueError('Cannot pass with unresolved/placeholder/hidden photos')
    result.update(status='verified' if passed else 'needs_changes',renders_verified=passed,visual_review=r)
    save(run/'review.json',r);save(run/'result.json',result)
    return {'status':result['status'],'renders_verified':passed}
