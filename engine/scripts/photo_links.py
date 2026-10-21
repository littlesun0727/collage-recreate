"""One source-to-canvas mapping for a base window and its pop-out subject."""
from copy import deepcopy
import math
import numpy as np
from PIL import Image, ImageOps


def sync_bindings(bindings,plan):
    rows={b['slot_id']:b for b in bindings['photos']};notes=[]
    for spec in plan.get('photos',[]):
        root=spec.get('linked_to')
        if not root:continue
        row=rows[spec['photo_id']];base=rows[root]
        for key in ['asset_id','source_crop','crop_center','mirror_x']:
            if row.get(key)!=base.get(key):notes.append(spec['photo_id']+': synchronized '+key+' with '+root)
            if key in base:row[key]=deepcopy(base[key])
        row['reason']=row.get('reason','')+'；出框与 '+root+' 共用客户素材、裁切和视频时轴'
    return notes


def attach(objects):
    by_id={o['id']:o for o in objects}
    for obj in objects:obj.pop('shared_photo',None)
    for child in objects:
        root=child.get('photo_window_spec',{}).get('linked_to')
        if not root:continue
        if root not in by_id:raise ValueError('Pop-out base photo is missing: '+root)
        base=by_id[root]
        child['source']=deepcopy(base['source'])
        child['binding']={**deepcopy(base['binding']),'slot_id':child['id']}
        transform={k:deepcopy(base[k]) for k in ['id','bbox','rotation','binding'] if k in base}
        base['shared_photo']=deepcopy(transform);child['shared_photo']=deepcopy(transform)


def _tile_to_canvas(box,angle):
    l,t,r,b=box;cx,cy=(l+r)/2,(t+b)/2
    a=math.radians(angle);c,s=math.cos(a),math.sin(a)
    return np.array([[c,-s,cx-c*(r-l)/2+s*(b-t)/2],
                     [s,c,cy-s*(r-l)/2-c*(b-t)/2],[0,0,1.]])


def shared_tile(obj,image,size,scale):
    root=obj['shared_photo'];binding=root['binding'];source_w,source_h=image.size
    crop=binding.get('source_crop',[0,0,1,1]);center=binding.get('crop_center',[.5,.5])
    l,t,r,b=[int(v*(source_w if i%2==0 else source_h)) for i,v in enumerate(crop)]
    root_box=[round(v*scale) for v in root['bbox']];rw,rh=root_box[2]-root_box[0],root_box[3]-root_box[1]
    ratio=max(rw/max(1,r-l),rh/max(1,b-t));cw,ch=rw/ratio,rh/ratio
    l+=(r-l-cw)*center[0];t+=(b-t-ch)*center[1]
    to_source=np.array([[1/ratio,0,l],[0,1/ratio,t],[0,0,1.]])
    if binding.get('mirror_x'):
        # Mirror within the declared crop, before cover centring, exactly once.
        x0=int(crop[0]*source_w);x1=int(crop[2]*source_w)
        to_source=np.array([[-1,0,x0+x1],[0,1,0],[0,0,1.]])@to_source
    obj_box=[round(v*scale) for v in obj['bbox']]
    mapping=to_source@np.linalg.inv(_tile_to_canvas(root_box,root.get('rotation',0)))@_tile_to_canvas(obj_box,obj.get('rotation',0))
    return image.transform(size,Image.Transform.AFFINE,tuple(mapping[:2].flatten()),Image.Resampling.BICUBIC)
