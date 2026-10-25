"""Conservative local snapping of VLM boxes to artwork/white screenshot boundaries.

Only examines +/-3% around each predicted edge. Invisible/ambiguous boundaries
remain unchanged and require review. This is a heuristic, not semantic segmentation.
"""
import numpy as np
from itertools import product

from crop_review import pixel_box

COMMON_RATIOS = {'1:1':1., '1:2':.5, '2:3':2/3, '3:2':1.5,
                 '3:4':.75, '4:3':4/3, '9:16':9/16, '16:9':16/9}


def refine_ratio(result, image_size, max_ratio_error=.03, max_edge_fraction=.02):
    """After boundary detection, anchor three edges and adjust the weakest fourth."""
    box=result['pixels']
    left,top,right,bottom=box
    width,height=right-left,bottom-top
    name,ratio=min(COMMON_RATIOS.items(),key=lambda kv:abs((width/height)/kv[1]-1))
    error=abs((width/height)/ratio-1)
    info={'applied':False,'ratio':name,'relative_error':error,'before_pixels':box.copy()}
    result=dict(result,ratio_adjustment=info)
    if error>max_ratio_error:
        info['reason']='ratio_too_far'
        return result
    desired=[right-round(height*ratio),bottom-round(width/ratio),
             left+round(height*ratio),top+round(width/ratio)]
    candidates=[]
    for index,edge in enumerate(('left','top','right','bottom')):
        evidence=result['edges'][edge]
        if evidence['status']=='image_edge':
            continue
        extent=image_size[0 if index in (0,2) else 1]
        span=width if index in (0,2) else height
        pos=desired[index]
        delta=pos-box[index]
        if not 0<=pos<=extent or abs(delta)>max_edge_fraction*span:
            continue
        trial=box.copy();trial[index]=pos
        if trial[0]>=trial[2] or trial[1]>=trial[3]:
            continue
        # Missing/ambiguous edges move first; detected strong edges serve as anchors.
        strength=evidence.get('score',0) if evidence['status']=='snapped' else -1
        candidates.append((strength,abs(delta)/span,index,trial))
    if not candidates:
        info['reason']='no_small_adjustment'
        return result
    _,_,index,trial=min(candidates)
    expands=(trial[index]<box[index]) if index in (0,1) else (trial[index]>box[index])
    if expands:
        info.update(reason='expansion_skipped',edge=('left','top','right','bottom')[index],
                    proposed_delta=trial[index]-box[index])
        return result
    info.update(applied=trial!=box,edge=('left','top','right','bottom')[index],
                delta=trial[index]-box[index],reason='anchored_ratio_adjustment')
    result['pixels']=trial
    # A ratio assumption does not resolve uncertain visual boundaries.
    return result


def postprocess_box(image, bbox, radius_fraction=.03):
    detected=snap_box(image,bbox,radius_fraction=radius_fraction,use_ratio_prior=False)
    return refine_ratio(detected,image.size)


def ratio_tiebreak(options, initial, radii):
    """Rank only comparable, observed edge candidates; never synthesize ratio-derived edges."""
    pools=[]
    for index, candidates in enumerate(options):
        if not candidates:
            return None
        best=max(candidates,key=lambda x:x['score'])
        nearest=abs(best['position']-initial[index])
        pool=[c for c in candidates if c['score']>=best['score']*.97 and
              abs(c['position']-initial[index])<=nearest+max(3,radii[index]*.15)]
        pools.append(pool[:3])
    ranked=[]
    for edges in product(*pools):
        box=[edge['position'] for edge in edges]
        width,height=box[2]-box[0],box[3]-box[1]
        if width<=0 or height<=0:
            continue
        ratio=width/height
        name,value=min(COMMON_RATIOS.items(),key=lambda kv:abs(ratio/kv[1]-1))
        ranked.append({'pixels':box,'ratio':name,'error':abs(ratio/value-1),'edges':edges})
    ranked.sort(key=lambda x:x['error'])
    if len(ranked)<2 or ranked[0]['error']>.02:
        return None
    # A weak ratio preference must not turn ambiguity into automatic acceptance.
    if ranked[1]['error']-ranked[0]['error']<.005:
        return None
    return dict(ranked[0],runner_up_error=ranked[1]['error'])


def snap_box(image, bbox, radius_fraction=.03, use_ratio_prior=True):
    im=np.asarray(image.convert('RGB'),dtype=np.float32)
    h,w=im.shape[:2]
    initial=list(pixel_box(bbox,w,h))
    result=initial.copy()
    evidence={}
    options=[]
    radii=[]
    # Use the central 80% of the opposite span to avoid rounded corners and outside UI.
    for index,name in enumerate(('left','top','right','bottom')):
        vertical=index in (0,2)
        extent=w if vertical else h
        prior=initial[index]
        radius=max(4,round(extent*radius_fraction))
        radii.append(radius)
        if prior in (0,extent):
            evidence[name]={'status':'image_edge','position':prior}
            options.append([{'position':prior,'score':1.0}])
            continue
        lo,hi=(initial[1],initial[3]) if vertical else (initial[0],initial[2])
        trim=max(1,int((hi-lo)*.1))
        lo,hi=lo+trim,hi-trim
        # Moving dimension first, RGB pixels along each line second.
        a=np.transpose(im[lo:hi,:,:],(1,0,2)) if vertical else im[:,lo:hi,:]
        candidates=[]
        for pos in range(max(4,prior-radius),min(extent-4,prior+radius)+1):
            before=a[pos-3:pos].mean(axis=0)
            after=a[pos:pos+3].mean(axis=0)
            outside,inside=(before,after) if index in (0,1) else (after,before)
            contrast=np.max(np.abs(before-after),axis=1)
            support=float(np.mean(contrast>=18))
            outside_white=float(np.mean(np.min(outside,axis=1)>=235))
            inside_content=float(np.mean(np.min(inside,axis=1)<225))
            if support<.65 or outside_white<.85 or inside_content<.6:
                continue
            jump=float(np.median(contrast))
            adjacent_jump=float(np.median(np.max(np.abs(a[pos]-a[pos-1]),axis=1)))
            # Broad transitions should beat isolated internal photo edges; proximity is secondary.
            score=support*min(jump/100,1.5)*outside_white+.5*adjacent_jump/255-.08*abs(pos-prior)/radius
            candidates.append({'position':pos,'support':round(support,4),
                'outside_white':round(outside_white,4),'inside_content':round(inside_content,4),
                'median_jump':round(jump,2),'adjacent_jump':round(adjacent_jump,2),'score':round(score,5)})
        candidates.sort(key=lambda x:x['score'],reverse=True)
        if not candidates:
            evidence[name]={'status':'unresolved','position':prior,'radius':radius}
            options.append([])
            continue
        best=candidates[0]
        rival=next((x for x in candidates if abs(x['position']-best['position'])>6),None)
        if rival and rival['score']>=best['score']*.85:
            evidence[name]={'status':'ambiguous','position':prior,'best':best,'rival':rival}
            # Nonmaximum suppression: one candidate per distinct boundary, not adjacent pixels.
            peaks=[]
            for candidate in candidates:
                if all(abs(candidate['position']-x['position'])>6 for x in peaks):
                    peaks.append(candidate)
                if len(peaks)==3:
                    break
            options.append(peaks)
            continue
        options.append([best])
        result[index]=best['position']
        evidence[name]=dict(best,status='snapped',delta=best['position']-prior,radius=radius)
    ratio_evidence={'enabled':use_ratio_prior,'applied':False}
    if use_ratio_prior and any(e['status']=='ambiguous' for e in evidence.values()):
        choice=ratio_tiebreak(options,initial,radii)
        if choice:
            result=choice['pixels']
            ratio_evidence.update(applied=True,ratio=choice['ratio'],relative_error=choice['error'],
                                  runner_up_error=choice['runner_up_error'])
            for index,name in enumerate(('left','top','right','bottom')):
                if evidence[name]['status']=='ambiguous':
                    evidence[name]=dict(choice['edges'][index],status='snapped',
                        delta=result[index]-initial[index],radius=radii[index],selection='ratio_tiebreak')
    if result[0]>=result[2] or result[1]>=result[3]:
        return {'coarse_pixels':initial,'pixels':initial,'status':'review','edges':evidence,'reason':'invalid_geometry'}
    resolved=all(x['status'] in ('snapped','image_edge') for x in evidence.values())
    return {'coarse_pixels':initial,'pixels':result,'status':'resolved' if resolved else 'review',
            'edges':evidence,'ratio_prior':ratio_evidence}
