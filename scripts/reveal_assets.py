"""Compile recovered full-canvas layers and shared customer-photo windows."""
from copy import deepcopy
import math
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageChops
from common import read, save, sha, fingerprint, verify_source
from effects import photo_layout, rounded_mask
from reveal import acquire, request_object
from recovery import load_plan, compose_groups, verify_asset


def area(b):return max(0,b[2]-b[0])*max(0,b[3]-b[1])


def contains(a,b):
    return area([max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])])/max(1,area(b))


def frame_hint(o):
    return (o.get('style',{}).get('shape')=='frame' or o.get('photo_id')
            or any(s in o.get('label','').lower() for s in ['相框','相纸','拍立得','polaroid','frame']))


def targets(scene):
    result=[]
    for o in scene['objects']:
        if o['kind']=='overlay':result.append({**o,'request':o.get('method')!='local' or bool(frame_hint(o))})
        elif o['kind']=='photo' and (o.get('appearance')=='polaroid' or 'card' in o['style']):
            result.append({'id':'frame_'+fingerprint(o['id'])[:16],'kind':'overlay','bbox':o['bbox'],
                           'photo_id':o['id'],'label':o['label']+' 相纸','description':'Derived photo frame',
                           'derived':True,'request':True,'style':{},'rotation':o.get('rotation',0)})
    return result


def make_window(canvas_size, box, angle=0, radius=0):
    l,t,r,b=box;tile=rounded_mask((r-l,b-t),radius)
    if angle:tile=tile.rotate(-angle,Image.Resampling.BICUBIC,expand=True)
    result=Image.new('L',canvas_size)
    result.paste(tile,(round((l+r-tile.width)/2),round((t+b-tile.height)/2)))
    return result


def has_ink(image,box):
    patch=np.asarray(image.crop(box));visible=patch[:,:,3]>128
    if visible.mean()<.45:return False
    values=patch[:,:,:3].mean(2)[visible]
    return bool(len(values) and np.percentile(values,95)-np.percentile(values,5)>55)


def attach(run,scene,config):
    run=Path(run);folder=run/'assets/reveal';folder.mkdir(parents=True,exist_ok=True)
    repair=load_plan(run,scene)
    requested=targets(scene);save(folder/'targets.json',requested)
    assets,receipts=acquire(run,scene,requested,config)
    assets,merged_owners=compose_groups(run,scene,assets,repair)
    window_repairs={(w['overlay_id'],w['photo_id']):w for w in repair.get('windows',[])}
    omitted={o['id']:o['reason'] for o in repair.get('omit',[])}
    missing={w['overlay_id'] for w in repair.get('windows',[])}-assets.keys()
    if missing:raise ValueError('Missing window repair sources: '+', '.join(sorted(missing)))
    by_id={o['id']:o for o in scene['objects']};photos=[o for o in scene['objects'] if o['kind']=='photo']
    width,height=scene['reference_size'];records=[];recovered={};owners={};used_photos=set()
    for target in requested:
        oid=target['id'];o=target if target.get('derived') else by_id[oid]
        expected=request_object(target,scene['reference_size'],config.get('padding',.1))
        rec={'id':oid,'input_bbox':target['bbox'],'request_bbox':expected['bbox'],'padding':expected['padding']};records.append(rec)
        if oid in merged_owners:
            o['recovery_owner']=merged_owners[oid]
            rec.update(action='grouped_member',owner=merged_owners[oid],source=assets.get(oid))
            continue
        def fallback(reason):
            rec.update(action='local_fallback',reason=reason)
            obj=by_id[target['photo_id']] if target.get('derived') else o
            obj['reveal_warning']=reason
            if obj['kind']!='photo':
                obj['method']='local' if obj.get('style',{}).get('shape') else 'placeholder'
        if oid not in assets:
            if target.get('request'):fallback('Reveal target unavailable; local fallback requires review')
            else:rec['action']='local_primitive'
            continue
        source=assets[oid]
        # The legacy service widens very thin boxes; no arbitrary layout remapping.
        tolerance=max(2,np.ceil(8*max(width,height)/1024))
        if any(abs(a-b)>tolerance for a,b in zip(source['original_bbox'],target['bbox'])):
            fallback('Cached target box differs from current design; request a matching layer');continue
        with Image.open(verify_source(source)) as raw:
            api_size=raw.size;image=raw.convert('RGBA').resize((width,height),Image.Resampling.LANCZOS)
        rec.update(source=source,api_size=list(api_size),scale_to_reference=[width/api_size[0],height/api_size[1]])
        if image.getchannel('A').getextrema()[1]<16:
            fallback('Reveal layer is empty or nearly transparent');continue
        enclosed=[p for p in photos if p.get('mode')=='cover' and contains(o['bbox'],p['bbox'])>.94 and area(p['bbox'])<area(o['bbox'])]
        rec['action']='recover_group' if source.get('recovery_group') else 'reuse_layer'
        notes=[]
        if oid in omitted:
            image.putalpha(0);rec.update(action='omit_after_review',reason=omitted[oid])
            o['reveal_warning']='Returned layer omitted after visual review: '+omitted[oid]
        if len(enclosed)>1 or (area(o['bbox'])/(width*height)>.22 and sum(contains(o['bbox'],p['bbox'])>.35 for p in photos)>1):
            notes.append('Layer spans multiple photos; inspect actual pixels, retained without automatic rejection')
        related=[p for p in photos if p['id']==o.get('photo_id') or p.get('parent_id')==oid]
        if oid in omitted:related=[]
        if frame_hint(o) and not related:notes.append('Frame has no explicit photo relationship; returned pixels retained')
        for photo in related:
            if photo['id'] in used_photos or photo.get('mode')!='cover':
                notes.append('Photo window not attached: ambiguous ownership or unsupported photo mode')
                continue
            if target.get('derived'):
                l,t,r,b=photo['bbox'];inset=photo_layout((r-l,b-t),photo['style'])
                box=photo.get('window_bbox',[l+inset[0],t+inset[1],l+inset[2],t+inset[3]])
                if photo.get('rotation',0):
                    # Shift the window center around the outer card's rotation center.
                    cx,cy=(l+r)/2,(t+b)/2;wx,wy=(box[0]+box[2])/2,(box[1]+box[3])/2
                    a=math.radians(photo['rotation']);dx=(wx-cx)*math.cos(a)-(wy-cy)*math.sin(a)+cx-wx
                    dy=(wx-cx)*math.sin(a)+(wy-cy)*math.cos(a)+cy-wy
                    box=[round(box[0]+dx),round(box[1]+dy),round(box[2]+dx),round(box[3]+dy)]
                # Legacy outer-card coordinates convert once to an inner window.
                photo['bbox']=box
                photo['style']={k:v for k,v in photo['style'].items() if k not in ['card','shadow','outline_width','outline_color']}
            else:box=photo.get('window_bbox',photo['bbox'])
            mask=make_window((width,height),box,photo.get('rotation',0),photo['style'].get('corner_radius',0))
            mp=folder/(oid+'-'+photo['id']+'-window.png');mask.save(mp)
            photo['photo_window']={'file':str(mp),'sha256':sha(mp)}
            photo['reveal_frame_id']=oid
            selected=np.asarray(mask)>128;alpha=np.asarray(image.getchannel('A'))
            fraction=float((alpha[selected]>16).mean()) if selected.any() else 0
            item={'photo_id':photo['id'],'window':photo['photo_window'],'window_bbox':box,
                  'original_visible_fraction':round(fraction,6),'action':'preserve_returned_pixels'}
            operation=window_repairs.get((oid,photo['id']))
            if operation:
                pad=operation.get('padding',0)
                clear_box=[round(box[0]-pad),round(box[1]-pad),round(box[2]+pad),round(box[3]+pad)]
                clear_mask=make_window((width,height),clear_box,photo.get('rotation',0),photo['style'].get('corner_radius',0))
                image.putalpha(ImageChops.multiply(image.getchannel('A'),ImageChops.invert(clear_mask)))
                cp=folder/(oid+'-'+photo['id']+'-clear.png');clear_mask.save(cp)
                item.update(action='clear_photo_window',reason=operation['reason'],padding=pad,
                            clear_mask={'file':str(cp),'sha256':sha(cp)})
                rec['action']='clear_photo_window'
            elif fraction>.05:
                notes.append('Window '+photo['id']+' contains visible pixels; inspect for old photos or intentional decoration')
            rec.setdefault('windows',[]).append(item);used_photos.add(photo['id'])
        if notes:rec['notes']=notes;o['reveal_notes']=notes
        file=folder/(oid+'.png');image.save(file)
        reference_crop=folder/(oid+'-reference.png');foreground=folder/(oid+'-preview.png')
        with Image.open(scene['reference']['file']) as ref:ref.crop(source['bbox']).save(reference_crop)
        image.crop(source['bbox']).save(foreground)
        o['recovered']={'file':str(file),'sha256':sha(file),'source':source,'reference_crop':str(reference_crop),
                        'foreground':str(foreground),'action':rec['action']}
        rec['clean']=o['recovered'];recovered[oid]=image;owners[oid]=o
        if target.get('derived'):
            obj={k:v for k,v in o.items() if k not in ['derived','request']};obj['text_unresolved']=False
            scene['objects'].append(obj);by_id[oid]=obj
            scene['layer_order'].insert(scene['layer_order'].index(photo['id'])+1,oid)
        elif related:
            # A frame with a transparent opening belongs above its customer photo.
            order=scene['layer_order']
            attached=[p for p in related if p.get('reveal_frame_id')==oid]
            if attached:
                last=max(attached,key=lambda p:order.index(p['id']))
                if order.index(oid)<order.index(last['id']):order.remove(oid);order.insert(order.index(last['id'])+1,oid)
    suppressed={};candidates={}
    for obj in scene['objects']:
        if obj['kind']=='photo' or obj.get('text_unresolved') or obj.get('text_origin'):continue
        owner=obj.get('embedded_in') or obj.get('parent_id')
        if owner in merged_owners:owner=merged_owners[owner]
        if owner in by_id and by_id[owner].get('reveal_frame_id'):owner=by_id[owner]['reveal_frame_id']
        if owner in recovered and contains(owners[owner]['bbox'],obj['bbox'])>.9 and has_ink(recovered[owner],obj['bbox']):
            obj['embedded_owner']=owner;suppressed[obj['id']]=owner
        elif obj['kind']=='text':
            possible=[oid for oid,image in recovered.items() if contains(owners[oid]['bbox'],obj['bbox'])>.95 and has_ink(image,obj['bbox'])]
            if possible:candidates[obj['id']]=possible
    index={'reference':scene['reference'],'records':records,'embedded_content':suppressed,
           'unconfirmed_embedded_text':candidates,'service_tasks':receipts,'config':config,
           'recovered_groups':scene.get('recovered_groups',[]),'postprocess':'preserve-unless-explicit-repair-v1'}
    save(folder/'index.json',index)
    scene['reveal_index']={'file':str(folder/'index.json'),'sha256':sha(folder/'index.json')}
    scene['reveal_unconfirmed_text']=candidates
    return index


def verify(scene):
    if scene.get('reveal_index'):verify_source(scene['reveal_index'])
    if scene.get('recovery_plan'):verify_source(scene['recovery_plan'])
    for obj in scene['objects']:
        if obj.get('recovered'):
            verify_source(obj['recovered']);verify_asset(obj['recovered']['source'])
        if obj.get('photo_window'):verify_source(obj['photo_window'])
