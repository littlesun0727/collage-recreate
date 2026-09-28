"""Prepare/reuse a customer image catalog; an agent supplies visual descriptions."""
import argparse
from datetime import datetime, timezone
import html
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from binding_contract import read_json,read_model_json,digest
from prompt_sections import description_hash,compatible_description_hashes
from matching_step import index_assets,make_sheet,save

VERSION='asset-catalog-v1'
FIELDS={'asset_id','description','subject_type','shot'}


def validate_descriptions(value,expected):
    if not isinstance(value,dict) or set(value)!={'descriptions'} or not isinstance(value['descriptions'],list):
        raise ValueError('Expected {"descriptions": [...]}')
    result={}
    for row in value['descriptions']:
        if not isinstance(row,dict) or set(row)!=FIELDS:raise ValueError('Description fields must be asset_id, description, subject_type, shot')
        aid=row['asset_id']
        if not isinstance(aid,str) or aid not in expected or aid in result:raise ValueError(f'Unexpected/duplicate asset description: {aid}')
        if not isinstance(row['description'],str) or not row['description'].strip():raise ValueError('Nonempty description required')
        if row['subject_type'] not in ('portrait','landscape','object','mixed','unknown'):raise ValueError('Invalid subject_type')
        if row['shot'] not in ('close_up','half_body','full_body','wide','unknown'):raise ValueError('Invalid shot')
        result[aid]=dict(row)
    if set(result)!=set(expected):raise ValueError('Every pending asset must have exactly one description')
    return result


def catalog_rows(path,assets):
    value=read_json(path)
    if value.get('schema_version')!=VERSION:raise ValueError('Unsupported catalog version')
    rows=value.get('assets')
    if not isinstance(rows,list):raise ValueError('Catalog assets must be an array')
    data={'descriptions':[{k:r[k] for k in FIELDS} for r in rows]}
    indexed=validate_descriptions(data,{r['asset_id'] for r in rows})
    hashes={r['asset_id']:r['sha256'] for r in rows}
    for asset in assets:
        aid=asset['asset_id']
        if aid not in indexed or hashes[aid]!=asset['sha256']:raise ValueError(f'Catalog missing or stale for {aid}; prepare catalog first')
    return {a['asset_id']:indexed[a['asset_id']] for a in assets}


def prepare_catalog(materials,output,cache=None):
    output=Path(output).resolve()
    if any(output.is_relative_to(Path(p).resolve()) for p in materials):raise ValueError('Output must be outside materials')
    output.mkdir(parents=True,exist_ok=False);(output/'preview').mkdir()
    assets,warnings=index_assets(materials,output)
    prompt_hash=description_hash()
    cached={}
    if cache:
        old=read_json(cache)
        if old.get('schema_version')==VERSION and old.get('description_prompt_sha256') in compatible_description_hashes():
            old_rows=old.get('assets',[])
            valid=validate_descriptions({'descriptions':[{k:r[k] for k in FIELDS} for r in old_rows]}, {r['asset_id'] for r in old_rows})
            cached={r['sha256']:valid[r['asset_id']] for r in old_rows}
    reused={a['asset_id']:{**cached[a['sha256']],'asset_id':a['asset_id']} for a in assets if a['sha256'] in cached}
    pending=[a for a in assets if a['asset_id'] not in reused]
    sheets=[]
    for start in range(0,len(pending),12):
        path=output/'preview'/f'pending_{start//12+1}.jpg'
        make_sheet([(a['asset_id'],output/a['thumbnail']) for a in pending[start:start+12]],path)
        sheets.append(str(path))
    save(output/'catalog-input.json',{'assets':assets,'reused_descriptions':reused,'description_prompt_sha256':prompt_hash,'warnings':warnings})
    save(output/'pending.json',{'pending':[{k:a[k] for k in ('asset_id','path','thumbnail','size')} for a in pending],
                                'contact_sheets':sheets,'prompt':str(ROOT/'binding.md'),'prompt_section':'第一步：建立客户素材目录'})
    save(output/'result.json',{'status':'awaiting_descriptions','prepared_at':datetime.now(timezone.utc).isoformat(),
                             'assets':len(assets),'pending':len(pending),'reused':len(reused),'model_called':False})
    return {'workspace':str(output),'pending':len(pending),'reused':len(reused),'pending_file':str(output/'pending.json')}


def complete_catalog(workspace,descriptions=None):
    workspace=Path(workspace).resolve()
    if (workspace/'catalog.json').exists():raise FileExistsError('Catalog already completed; prepare a new workspace to refresh')
    inputs=read_json(workspace/'catalog-input.json');assets=inputs['assets'];reused=inputs['reused_descriptions']
    pending={a['asset_id'] for a in assets}-set(reused)
    selected=validate_descriptions(read_model_json(descriptions) if descriptions else {'descriptions':[]},pending)
    for a in assets:
        if digest(a['path'])!=a['sha256']:raise ValueError(f'Customer file changed: {a["asset_id"]}')
    rows=[{**a,**(selected.get(a['asset_id']) or reused[a['asset_id']])} for a in assets]
    doc={'schema_version':VERSION,'description_prompt_sha256':inputs['description_prompt_sha256'],'assets':rows,'warnings':inputs['warnings']}
    save(workspace/'catalog.json',doc)
    esc=lambda x:html.escape(str(x),quote=True)
    cards=''.join(f'<article><img src="{esc(a["thumbnail"])}"><p>{esc(a["asset_id"])}</p><p>{esc(a["description"])}</p><small>{esc(a["subject_type"])} / {esc(a["shot"])}</small></article>' for a in rows)
    (workspace/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>客户素材目录</title><style>body{font:16px sans-serif;margin:30px}main{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}img{width:100%;height:230px;object-fit:contain}article{padding:12px;background:#f4f4f4}</style><h1>客户素材目录</h1><main>'+cards+'</main>',encoding='utf-8')
    result={**read_json(workspace/'result.json'),'status':'completed','completed_at':datetime.now(timezone.utc).isoformat(),'catalog':str(workspace/'catalog.json')}
    call_path=workspace/'request/call.json'
    if call_path.is_file():
        call=read_json(call_path)
        result.update(model_called=call.get('http_dispatches',0)>0,
                      business_http_requests=call.get('http_dispatches',0))
    save(workspace/'result.json',result)
    return result


def main():
    p=argparse.ArgumentParser(description='Customer catalog: prepare inputs, then validate agent descriptions. No SDK calls.')
    sub=p.add_subparsers(dest='command',required=True)
    start=sub.add_parser('prepare');start.add_argument('--materials',nargs='+',type=Path,required=True);start.add_argument('--output',type=Path,required=True);start.add_argument('--cache',type=Path)
    finish=sub.add_parser('complete');finish.add_argument('--workspace',type=Path,required=True);finish.add_argument('--descriptions',type=Path)
    a=p.parse_args()
    try:
        result=prepare_catalog(a.materials,a.output,a.cache) if a.command=='prepare' else complete_catalog(a.workspace,a.descriptions)
        import json;print(json.dumps(result,ensure_ascii=False));return 0
    except (OSError,ValueError,KeyError,TypeError) as exc:
        import json;print(json.dumps({'status':'failed','error':str(exc)},ensure_ascii=False));return 2

if __name__=='__main__':raise SystemExit(main())
