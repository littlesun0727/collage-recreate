"""Opt-in manual placement; original analysis and asset coordinates stay intact."""
from copy import deepcopy
import math


def offset(obj):
    value=obj.get('editor_transform',{})
    return value.get('x',0),value.get('y',0)


def capabilities(scene):
    objects={o['id']:o for o in scene['objects']}
    owner={o['id']:o.get('embedded_owner') or o.get('recovery_owner') or o['id'] for o in objects.values()}
    for group in scene.get('generated_groups',[]):
        for identifier in group['member_ids']:owner[identifier]=group['primary']
    def root(identifier):
        seen=set()
        while owner.get(identifier,identifier)!=identifier and identifier not in seen:
            seen.add(identifier);identifier=owner[identifier]
        return identifier
    visible={root(i) for i in objects}
    links={i:{i} for i in visible}
    for o in objects.values():
        related=[o.get('parent_id'),o.get('photo_id'),o.get('reveal_frame_id')]
        # Only explicit photo/carrier links group layers. Ordinary overlapping
        # stickers or paper backgrounds never become an implicit giant group.
        if o['kind']!='photo':related=[o.get('photo_id')]
        for other in related:
            if other in objects:
                a,b=root(o['id']),root(other)
                links[a].add(b);links[b].add(a)
    result={}
    for i,o in objects.items():
        group={root(i)};pending=list(group)
        while pending:
            for other in links[pending.pop()]-group:group.add(other);pending.append(other)
        independent=root(i)==i
        movable=independent and o['kind'] in ['photo','overlay','text'] and not any(objects[g]['kind']=='background' for g in group)
        fused=any(i in g['member_ids'] for g in scene.get('generated_groups',[]))
        editable=independent and not fused and o['kind']=='text' and o.get('method','local')!='extract' and not o.get('generated') and not o.get('recovered')
        result[i]={'move':movable,'text':editable,'group':sorted(group),'owner':root(i)}
    return result


def apply_edits(scene, changes):
    if not isinstance(changes,list) or not 1<=len(changes)<=200:raise ValueError('请提交1至200个对象修改')
    scene=deepcopy(scene);objects={o['id']:o for o in scene['objects']};caps=capabilities(scene)
    seen=set();moves={};w,h=scene['reference_size']
    for change in changes:
        if not isinstance(change,dict) or set(change)-{'id','transform','text'}:raise ValueError('不支持的编辑字段')
        identifier=change.get('id')
        if not isinstance(identifier,str) or identifier not in objects or identifier in seen:raise ValueError('对象无效或重复')
        seen.add(identifier);o=objects[identifier];cap=caps[identifier]
        if len(change)==1:raise ValueError('修改内容为空')
        if 'transform' in change:
            value=change['transform']
            if not cap['move'] or not isinstance(value,dict) or set(value)!={'x','y'}:raise ValueError('该对象不能独立移动')
            if any(type(v) not in [int,float] or not math.isfinite(v) or abs(v)>max(w,h)*2 for v in value.values()):raise ValueError('位移坐标无效')
            ox,oy=offset(o);dx,dy=value['x']-ox,value['y']-oy
            for member in cap['group']:
                x,y=offset(objects[member]);target={'x':round(x+dx,3),'y':round(y+dy,3)}
                if member in moves and moves[member]!=target:raise ValueError('同一组合收到冲突位移')
                moves[member]=target
        if 'text' in change:
            value=change['text']
            if not cap['text'] or not isinstance(value,str) or not value.strip() or len(value)>2000:raise ValueError('只能修改独立文字，文字不能为空或超过2000字')
            o.update(text=value,text_origin='customer_revision',text_status='known',text_unresolved=False)
    for identifier,transform in moves.items():
        o=objects[identifier];l,t,r,b=o['bbox'];x,y=transform['x'],transform['y']
        if r+x<=0 or b+y<=0 or l+x>=w or t+y>=h:raise ValueError('对象不能完全移出画布')
        o['editor_transform']=transform
    scene['revision']=scene.get('revision',0)+1
    return scene


def translate(image,obj,scale):
    x,y=offset(obj)
    if not x and not y:return image
    from PIL import Image
    shifted=Image.new(image.mode,image.size)
    shifted.paste(image,(round(x*scale),round(y*scale)))
    return shifted
