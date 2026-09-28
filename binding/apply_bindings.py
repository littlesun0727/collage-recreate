"""Accept an agent's selection into an existing prepared binding workspace."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from binding_contract import VERSION,read_json,digest,load_slots,validate_selection,assemble_bindings
from matching_step import save,write_preview


def apply(workspace,selection):
    root=Path(workspace).resolve()
    if (root/'bindings.json').exists():raise FileExistsError('Bindings already exist; prepare a new workspace for revisions')
    if read_json(root/'result.json')['status']!='prepared':raise ValueError('Expected a prepared matching workspace')
    state=read_json(root/'prepared.json')
    for name,key in [('input.json','input_sha256'),('assets.json','assets_sha256'),('overrides.json','overrides_sha256')]:
        if digest(root/name)!=state[key]:raise ValueError(f'Prepared input was changed: {name}')
    info=read_json(root/'input.json');assets=read_json(root/'assets.json');overrides=read_json(root/'overrides.json')
    for path,key in [(info['draft_path'],'draft_sha256'),(info['reference_path'],'reference_sha256')]:
        if digest(path)!=info[key]:raise ValueError('Original input changed during matching')
    if digest(root/'draft.json')!=info['draft_sha256']:raise ValueError('Draft snapshot changed')
    if info.get('catalog_path') and digest(info['catalog_path'])!=info['catalog_sha256']:raise ValueError('Catalog changed during matching')
    for asset in assets:
        if digest(asset['path'])!=asset['sha256']:raise ValueError('Customer image changed during matching')
    slots=load_slots(read_json(root/'draft.json'),info['draft_contract_version'])
    expected={s['id'] for s in slots if 'source_slot_id' not in s and s['id'] not in overrides}
    selected=validate_selection(read_json(selection),expected,assets)
    rows,warnings=assemble_bindings(slots,assets,selected,overrides)
    status='completed' if all(row['asset_id'] for row in rows) else 'partial'
    warnings=state['warnings']+warnings
    write_preview(root,slots,assets,rows,warnings)
    save(root/'bindings.json',{'schema_version':VERSION,'status':status,'input':info,'assets':assets,'bindings':rows,'warnings':warnings,
                              'selection':{'path':str(Path(selection).resolve()),'sha256':digest(selection),'source':'agent'}})
    result={'status':status,'bindings':str(root/'bindings.json'),'preview':str(root/'index.html'),'total_slots':len(rows),
            'bound_slots':sum(bool(r['asset_id']) for r in rows),'fallback_slots':sum(r['method']=='fallback' for r in rows),'visual_status':'unreviewed','model_called_by_script':False,
            'completed_at':datetime.now(timezone.utc).isoformat()}
    save(root/'result.json',result);return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--selection',type=Path,required=True);a=p.parse_args()
    try:
        import json;result=apply(a.workspace,a.selection);print(json.dumps(result,ensure_ascii=False));return 0 if result['status']=='completed' else 2
    except (OSError,ValueError,KeyError,TypeError) as exc:
        import json;print(json.dumps({'status':'failed','error':str(exc)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
