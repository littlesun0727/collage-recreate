from copy import deepcopy
from pathlib import Path
from common import read, save, sha, fingerprint, verify_source
from validate import analysis_check, bindings_check, review_check
from prepare import boxes
from effects import resolve_style


def compile_scene(run, reveal_config=None):
    run=Path(run);inputs=read(run/'input.json');verify_source(inputs['reference'])
    analysis=analysis_check(read(run/'analysis.json'),inputs)
    catalog=read(run/'prepared/catalog.json');bindings=bindings_check(read(run/'bindings.json'),analysis,catalog)
    assets={a['id']:a for a in catalog['assets']};bound={b['slot_id']:b for b in bindings['photos']};texts={t['object_id']:t for t in bindings['texts']}
    scale=inputs['output_width']/analysis['reference_size'][0]
    objects=[]
    for original in analysis['objects']:
        o=deepcopy(original);o['style']=resolve_style(o)
        if o['id'] in texts:o['text']=texts[o['id']]['text'];o['text_origin']=texts[o['id']]['origin']
        o['text_unresolved']=o.get('text_status')=='unreadable' and o['id'] not in texts
        if o['kind']=='photo':
            o.setdefault('mode','cover')
            o['binding']=bound[o['id']];o['source']=assets[o['binding']['asset_id']]
        objects.append(o)
    scene={'schema_version':'collage-scene-v1','reference':inputs['reference'],'reference_size':analysis['reference_size'],'canvas_size':[inputs['output_width'],round(analysis['reference_size'][1]*scale)],'objects':objects,'layer_order':analysis['layer_order'],'cutout_model':inputs.get('cutout_model'),'sources':{'analysis':sha(run/'analysis.json'),'bindings':sha(run/'bindings.json'),'input':sha(run/'input.json'),'catalog':sha(run/'prepared/catalog.json')},'revision':0}
    config_file=run/'reveal-config.json'
    if reveal_config is not None:save(config_file,reveal_config)
    config=read(config_file) if config_file.exists() else {}
    if config.get('enabled'):
        from reveal_assets import attach
        attach(run,scene,config)
    if config_file.exists():scene['reveal_config_sha256']=sha(config_file)
    save(run/'scene.json',scene);boxes(run,analysis)
    return scene


def load_scene(run):
    run=Path(run);s=read(run/'scene.json')
    for name,p in [('analysis','analysis.json'),('bindings','bindings.json'),('input','input.json'),('catalog','prepared/catalog.json')]:
        if sha(run/p)!=s['sources'][name]:raise ValueError(f'{p} changed: run build to compile new inputs')
    verify_source(s['reference'])
    if s.get('reveal_config_sha256') and sha(run/'reveal-config.json')!=s['reveal_config_sha256']:
        raise ValueError('Reveal configuration changed; build again')
    from reveal_assets import verify
    verify(s)
    return s


def checked_review(run, path):
    run=Path(run);s=load_scene(run);r=review_check(read(path),s)
    result=read(run/'result.json')
    if r.get('render_id'):
        if r['render_id']!=result.get('render_id'):raise ValueError('Review is stale: render_id changed')
        r.setdefault('scene_sha256',result['scene_sha256']);r.setdefault('final_sha256',result['final_sha256'])
    if r['scene_sha256']!=sha(run/'scene.json') or r['final_sha256']!=sha(run/'final.png'):
        raise ValueError('Review is stale: use current scene_sha256 and final_sha256 from result.json')
    return s,r


def apply_review(run, path):
    run=Path(run);s,r=checked_review(run,path);by_id={o['id']:o for o in s['objects']};pending=[]
    for item in r['items']:
        o=by_id[item['id']]
        if item['action']=='adjust':
            protected=o.get('recovered') or o.get('embedded_owner') or o.get('recovery_owner') or o.get('photo_window')
            if protected and set(item['changes'])-{'crop_center','source_crop','mirror_x'}:
                raise ValueError('Recovered geometry/text belongs to the extraction: update analysis and build again')
            if any(o['id'] in g['member_ids'] for g in s.get('generated_groups',[])):
                raise ValueError('Fused generated member cannot be adjusted separately; recompile analysis to split or regenerate the whole group')
            changes=item['changes']
            for key,value in changes.items():
                if key=='style':
                    for name,setting in value.items():
                        if isinstance(setting,dict):o['style'][name]={**o['style'].get(name,{}),**setting}
                        else:o['style'][name]=setting
                elif key in ['crop_center','source_crop','mirror_x']:
                    if o['kind']!='photo':raise ValueError('Photo placement parameters only apply to photos')
                    o['binding'][key]=value
                else:o[key]=value
            if 'bbox' in changes:
                l,t,rr,b=o['bbox'];w,h=s['reference_size']
                if not (0<=l<rr<=w and 0<=t<b<=h):raise ValueError('Adjusted bbox is out of bounds')
        elif item['action']=='generate':pending.append(o['id'])
    s['pending_generation']=pending;s['revision']+=1
    save(run/'reviews'/f'revision-{s["revision"]}.json',r);save(run/'scene.json',s)
    return s
