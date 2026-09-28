"""Compile lightweight decisions into ordinary task receipts, without replanning."""
from copy import deepcopy
from .common import BuildError, fingerprint
from .content_scope import allowed_text, neighbor_candidates
from .unit_planner import validate_decisions, unit_inventory

def compile_units(raw, inputs, fonts):
    wire = deepcopy(raw)
    for unit in wire.get('units', []):
        unit.pop('unit_id', None)
    plan = validate_decisions(wire, inputs)
    inventory = unit_inventory(plan, inputs)
    layers = ['background' if l['type']=='background' else l['type']+':'+l['id'] for l in inputs['draft']['layer_order']]
    objects = []
    for unit, info in zip(plan['units'], inventory):
        members = [deepcopy(inputs['entries'][k]) for k in unit['member_keys']]
        method = unit['method']
        # Keep native text/source validation for independent text/customer assets.
        entry = deepcopy(members[0]) if len(members)==1 else {'type':'overlay','draft':{'label':unit['brief']}}
        if method in ('generate','compose') and entry['type']=='text':
            entry['type']='overlay'
        key = unit['unit_id']
        entry.update(key=key, unit_id=key, member_keys=unit['member_keys'], members=members,
                     exact_text_by_member=info['exact_text_by_member'], reference_box=info['reference_box'],
                     target_box=info['target_box'], interleaved_context_keys=info['interleaved_context_keys'],
                     layer_index=max(layers.index(k) for k in unit['member_keys']),
                     size=[info['target_box'][2]-info['target_box'][0],info['target_box'][3]-info['target_box'][1]],
                     crop='inputs/'+key+'.png', exact_text=[t for e in members for t in allowed_text(e)])
        intent={'brief':unit['brief']}
        if method=='text':
            if not fonts: raise BuildError('missing_font','No installed fonts')
            intent.update(font_id=next(iter(fonts)),layout_advice=unit['brief'])
        entry['plan']={'key':key,'method':method,'reason':unit['brief'],'intent':intent,
                       'approximations':[],'related_keys':[]}
        neighbors=neighbor_candidates(entry,inputs)
        entry['content_scope']={'version':'content-scope-v1','target_key':key,'keep':unit['brief'],
           'allowed_text':entry['exact_text'],
           'excluded_objects':[e for e in neighbors if e['key'] not in unit['member_keys']],
           'scope_source':'validated-unit-members'}
        objects.append(entry)
    # One atomic batch of executable units gives review a complete composition.
    # Unresolved units stay separate and cannot block delivery of the known ones.
    groups=[[e for e in objects if e['plan']['method']!='blocked']]
    groups += [[e] for e in objects if e['plan']['method']=='blocked']
    tasks=[]
    for group in groups:
        if not group: continue
        keys=[e['key'] for e in group]
        task={'schema_version':'build-task-v2','production_mode':'units',
          'id':'task-'+fingerprint(sorted(keys))[:12], 'owned_keys':keys,
          'member_keys':[k for e in group for k in e['member_keys']],
          'dependency_keys':[], 'objects':group,
          'neighbors':[deepcopy(e) for k,e in inputs['entries'].items() if k not in inputs['selected']],
          'canvas_size':inputs['info']['canvas_size'],'reference_size':inputs['info']['reference_size'],
          'reference':'inputs/reference.png','fonts':fonts,'photo_windows':inputs['passthrough'],
          'failure_feedback':[], 'output_requirements':{
            'format':'PNG RGBA','one_file_per_unit':True,
            'mapping':'Full raster maps to target_box; retain transparent margins. Place each unit once at layer_index.',
            'scope':'generate members together; compose parts locally; never split ownership or change planned method',
            'text':'standalone text uses reproducible metadata; compound text is checked visually against exact_text_by_member'}}
        task['revision']=fingerprint({'task':task,'input':inputs['info']})
        tasks.append(task)
    return plan,tasks
