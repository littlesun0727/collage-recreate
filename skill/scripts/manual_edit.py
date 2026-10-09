"""Deterministic manual editor commands. Never invokes agents or extraction."""
import argparse
import json
from pathlib import Path
import shutil
from common import read,save,sha,fingerprint,locked
from scene import load_scene
from editor_scene import apply_edits,capabilities,offset
from render import render
from revision import folder,commit_revision,recover_commit


def copy_run(run,candidate):
    shutil.copytree(run,candidate,ignore=shutil.ignore_patterns('chat','observability','.write.lock','events.jsonl','sdk-timing.json','editor_layers'))


def editor_document(candidate):
    scene=read(candidate/'scene.json');result=read(candidate/'result.json');caps=capabilities(scene)
    layers={r['id']:r for r in result['editor_layers']};objects=[]
    for identifier in scene['layer_order']:
        if identifier not in layers:continue
        obj=next(o for o in scene['objects'] if o['id']==identifier)
        x,y=offset(obj)
        objects.append({'id':identifier,'label':obj.get('label',identifier),'kind':obj['kind'],
                        'bbox':obj['bbox'],'transform':{'x':x,'y':y},'text':obj.get('text',''),
                        'capabilities':caps[identifier],'file':layers[identifier]['file']})
    return {'reference_size':scene['reference_size'],'canvas_size':scene['canvas_size'],
            'objects':objects,'final_file':str(candidate/'final.png')}


def execute(run,action,request):
    run=Path(run);root=folder(run,request['request_id']);candidate=root/'candidate'
    if action not in ['export','preview','save']:raise ValueError('Unknown editor action')
    if action=='save' and (root/'committed.json').exists():
        return commit_revision(run,request['request_id'],None,manual=True)
    marker=read(run/'chat/commit.json') if (run/'chat/commit.json').exists() else {}
    if marker.get('status')=='promoting':
        if marker.get('request_id')!=request['request_id']:raise ValueError('另一轮保存需要先恢复')
        recover_commit(run)
    if action=='save' and marker.get('request_id')==request['request_id'] and marker.get('status')=='done':
        return commit_revision(run,request['request_id'],None,manual=True)
    result=read(run/'result.json');scene=load_scene(run)
    if result.get('render_id')!=request['base_render_id'] or sha(run/'scene.json')!=request['base_scene_sha256']:
        raise ValueError('基础版本已变化，请保留草稿并重新载入最新成图')
    saved=read(root/'candidate.json') if (root/'candidate.json').exists() else {}
    if saved and saved.get('plan_sha256')!=fingerprint(request):raise ValueError('请求编号已被其他修改使用')
    if not saved:
        edited=apply_edits(scene,request['changes']) if action!='export' else scene
        if candidate.exists():
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root.resolve()):raise ValueError('Unsafe editor candidate')
            shutil.rmtree(candidate)
        root.mkdir(parents=True,exist_ok=True);copy_run(run,candidate)
        save(candidate/'scene.json',edited);save(root/'plan.json',request)
        render(candidate,publish_result=False,capture_layers=action!='save')
        saved={'base_render_id':result['render_id'],'plan_sha256':fingerprint(request),'origin':'manual',
               'final_sha256':sha(candidate/'final.png'),'scene_sha256':sha(candidate/'scene.json')}
        save(root/'candidate.json',saved)
    if action=='save':return commit_revision(run,request['request_id'],None,manual=True)
    return editor_document(candidate)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);parser.add_argument('--action',required=True)
    parser.add_argument('--request',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    with locked(Path(args.run)):
        result=execute(Path(args.run),args.action,read(args.request))
        save(Path(args.output),result)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'error':str(exc)},ensure_ascii=True));raise SystemExit(1)
