"""One offline admission pass. Never edits extracted pixels or calls a model."""
from copy import deepcopy
from pathlib import Path
from common import read, save, sha, verify_source


def screen(run, path):
    from scene import load_scene
    from asset_gate import publish
    from validate import analysis_check
    from reveal_assets import contains
    from render import render
    run=Path(run);scene=load_scene(run);result=read(run/'result.json');plan=read(path)
    if set(plan)-{'render_id','decisions'} or plan.get('render_id')!=result.get('render_id'):
        raise ValueError('Screen needs the current result.render_id')
    if result.get('scene_sha256')!=sha(run/'scene.json') or result.get('final_sha256')!=sha(run/'final.png'):
        raise ValueError('Screen result is stale; render first')
    if scene.get('screening'):raise ValueError('Screen already applied; inspect final output and report remaining issues')
    if scene.get('revision') or scene.get('generated_groups') or any(o.get('generated') for o in scene['objects']):
        raise ValueError('Screen before later manual revisions or generation')
    decisions=plan.get('decisions')
    if not isinstance(decisions,list):raise ValueError('Screen decisions must be a list')
    report=deepcopy(read(verify_source(scene['asset_gate'])))
    records={r['id']:r for r in report['records']};objects={o['id']:o for o in scene['objects']}
    analysis=deepcopy(read(run/'analysis.json'));analysis_objects={o['id']:o for o in analysis['objects']}
    seen=set();claims=set()
    for d in decisions:
        if not isinstance(d,dict) or set(d)-{'id','action','reason','style','embedded_ids'}:
            raise ValueError('Unknown screen decision fields')
        oid=d.get('id');o=objects.get(oid)
        local_draw=o and o.get('method')=='local' and d.get('action')=='draw'
        if oid in seen or not o or o['kind']!='overlay' or (oid not in records and not local_draw):
            raise ValueError('Screen needs a unique extracted overlay, or an existing local overlay for draw: '+str(oid))
        seen.add(oid)
        if d.get('action') not in ['keep','drop','draw'] or not isinstance(d.get('reason'),str) or not d['reason'].strip():
            raise ValueError('Screen needs keep/drop/draw and a reason: '+oid)
        if o.get('recovery_owner') or any(oid in g['member_ids'] for g in scene.get('recovered_groups',[])):
            raise ValueError('Screen does not split recovered groups')
        if 'style' in d and d['action']!='draw':raise ValueError('style is only for draw')
        if d['action']=='draw':
            style=d.get('style',o.get('style',{}))
            if oid not in analysis_objects or not isinstance(style,dict) or not style.get('shape'):
                raise ValueError('draw needs an explicit supported primitive: '+oid)
            analysis_objects[oid]['style']=deepcopy(style);analysis_objects[oid]['method']='local'
        r=records.get(oid,{})
        if d['action']=='keep' and (not r.get('candidate') or set(r.get('issues',[])) & {'empty_layer','repair_failed','explicitly_omitted'}):
            raise ValueError('Cannot keep unavailable or failed pixels: '+oid)
        ids=d.get('embedded_ids',[])
        if not isinstance(ids,list) or (ids and d['action']!='keep'):raise ValueError('Only kept pixels may own embedded content')
        for child in ids:
            c=objects.get(child,{})
            owner=c.get('embedded_in') or c.get('parent_id')
            candidate=any(x['id']==child for x in r.get('text_candidates',[]))
            if child in claims or c.get('kind') not in ['text','overlay'] or c.get('text_origin') or child==oid:
                raise ValueError('Invalid embedded member: '+str(child))
            if owner not in [None,oid] or not (owner==oid or candidate) or contains(o['bbox'],c['bbox'])<.9:
                raise ValueError('Embedded member lacks a compatible spatial/owner relation: '+child)
            claims.add(child)
    analysis_check(analysis)
    for d in decisions:
        oid=d['id'];o=objects[oid];r=records.get(oid);action=d['action']
        if r is None:
            # An existing local primitive has no extracted candidate to reject.
            o.update(method='local',style=deepcopy(analysis_objects[oid]['style']))
            continue
        for child in objects.values():
            if child.get('embedded_owner')==oid:child.pop('embedded_owner')
        for field in ['recovered','gate_local','gate_embedded_ids','reveal_warning']:
            o.pop(field,None)
        r.update(status='accepted' if action=='keep' else 'rejected',basis='visual' if action!='draw' else 'local_requested',
                 semantic_verified=action=='keep',decision=deepcopy(d),embedded_ids=d.get('embedded_ids',[]),
                 use_local=action=='draw',local_fallback=action=='draw')
        if action=='keep':
            o['recovered']=deepcopy(r['candidate']);o['gate_embedded_ids']=r['embedded_ids']
            for child in r['embedded_ids']:objects[child]['embedded_owner']=oid
        elif action=='draw':
            o.update(method='local',style=deepcopy(analysis_objects[oid]['style']),gate_local=True)
            r['local_style']=deepcopy(o['style'])
        else:o['reveal_warning']='Screen rejected extracted layer: '+d['reason']
        o['gate']={k:r[k] for k in ['status','basis','input_key']}
    for child,owners in list(scene.get('reveal_unconfirmed_text',{}).items()):
        remaining=[oid for oid in owners if objects[oid].get('recovered') and objects[child].get('embedded_owner')!=oid]
        if remaining:scene['reveal_unconfirmed_text'][child]=remaining
        else:scene['reveal_unconfirmed_text'].pop(child)
    receipt=run/'screening.json';save(receipt,plan)
    scene['screening']={'file':str(receipt.resolve()),'sha256':sha(receipt)}
    publish(run,scene,list(records.values()));save(run/'scene.json',scene)
    return render(run)
