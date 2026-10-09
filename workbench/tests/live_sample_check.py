"""Opt-in real sample validation. Sequential; existing analysis/extraction is reused read-only."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'skill/scripts'))
from common import read,save,sha
from live_media import prepare_live
from motion_render import execute
from render import render
from PIL import Image


def baseline(root):
    return {str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()}


def run_case(source,destination,materials,mappings,model,report):
    started=time.monotonic()
    before=baseline(source)
    original=read(source/'scene.json');inputs=read(source/'input.json')
    imported=time.monotonic()
    prepare_live(inputs['original_reference']['file'],materials,destination,original['canvas_size'][0],
                 'Live implementation verification: reuse existing analyzed layout and extracted decorations read-only.',model)
    import_seconds=time.monotonic()-imported
    manifest=read(destination/'media/manifest.json');videos={v['name']:v for v in manifest['videos']}
    assets={a['id']:a for a in read(destination/'prepared/catalog.json')['assets']}
    scene=deepcopy(original)
    scene['reference']=read(destination/'input.json')['reference']
    scene['cutout_model']=str(model)
    for obj in scene['objects']:
        if obj['kind']!='photo':continue
        if obj['id'] in mappings:
            asset=assets[videos[mappings[obj['id']]]['asset_id']]
            obj['binding']['asset_id']=asset['id']
            obj['binding'].pop('source_crop',None)
            obj['binding'].pop('crop_center',None)
            obj['binding'].pop('mirror_x',None)
            obj['source']=asset
        else:
            assert obj['source']['id'] in assets,'Original static customer asset missing'
            obj['source']=assets[obj['source']['id']]
    save(destination/'analysis.json',read(source/'analysis.json'))
    bindings=read(source/'bindings.json')
    bindings['photos']=[o['binding'] for o in scene['objects'] if o['kind']=='photo']
    save(destination/'bindings.json',bindings)
    for name,path in [('analysis','analysis.json'),('bindings','bindings.json'),('input','input.json'),('catalog','prepared/catalog.json')]:
        scene['sources'][name]=sha(destination/path)
    if (source/'reveal-config.json').exists():
        save(destination/'reveal-config.json',read(source/'reveal-config.json'))
        scene['reveal_config_sha256']=sha(destination/'reveal-config.json')
    # New input hashes require fresh program admission; never transfer an old visual pass.
    if scene.get('asset_gate'):
        from asset_gate import inspect,publish
        previous=read(scene['asset_gate']['file']);records=[]
        by_id={o['id']:o for o in scene['objects']}
        for old in previous['records']:
            obj=by_id[old['id']]
            candidate=old.get('candidate')
            if not candidate:raise ValueError('Real sample requires an extracted candidate')
            with Image.open(candidate['file']) as image:
                record=inspect(image.convert('RGBA'),obj,scene,candidate['source'],{},
                               old.get('window_checks',[]),old.get('text_candidates',[]),[])
            record.update(candidate=candidate,reference_crop=old.get('reference_crop'),foreground=old.get('foreground'))
            if obj.get('gate_local'):
                # Preserve the existing explicitly chosen local renderer, not a rejected extraction.
                record.update(status='rejected',basis='local_requested',local_fallback=True,
                              local_style=obj['style'],use_local=True,semantic_verified=False)
            elif record['status']!='accepted':raise ValueError('New cover needs extraction review: '+obj['id'])
            obj['gate']={k:record[k] for k in ['status','basis','input_key']}
            records.append(record)
        publish(destination,scene,records)
    save(destination/'scene.json',scene)
    cover_started=time.monotonic();render(destination)
    cover_seconds=time.monotonic()-cover_started
    frozen={name:sha(destination/name) for name in ['scene.json','final.png','result.json']}
    print(json.dumps({'case':destination.name,'phase':'cover_complete','import_seconds':import_seconds,'cover_seconds':cover_seconds}),flush=True)
    output=execute(destination,'motion-real-'+destination.name.split('-')[0]+'-'+str(int(time.time())))
    assert frozen=={name:sha(destination/name) for name in frozen}
    assert baseline(source)==before,'Source task changed'
    row={'task':str(destination),'source_task':str(source),'import_seconds':round(import_seconds,3),
         'cover_seconds':round(cover_seconds,3),'total_seconds':round(time.monotonic()-started,3),
         'source_files_unchanged':len(before),'cover_unchanged':True,'live_count':len(videos),'motion':output}
    report.append(row);save(destination.parent/'live-validation.json',report)
    print(json.dumps({'case':destination.name,'phase':'completed','seconds':row['total_seconds'],'duration_us':output['duration_us'],'frames':output['frame_count'],'matte_frames':output['matte_frames']}),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--case',choices=['all','cutout'],default='all');args=parser.parse_args()
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    materials=['D:/视频素材/人像素材3.0','D:/视频素材/风景照片','D:/datas/live动图素材']
    model='D:/codes/visual-recreate-validation/models/birefnet-lite-fp32.onnx'
    report=read(root/'live-validation.json') if (root/'live-validation.json').exists() else []
    if args.case=='all':
        run_case(Path('D:/codes/collage_outputs/workbench-validation/06-拼贴3-手动画布-20261009'),root/'07-拼贴3-Live混排',materials,
                 {'photo_top_left':'20261009-105038.mp4','photo_right_mid':'20261009-105118.mp4','photo_bottom_right':'20261009-105151.mp4'},model,report)
    run_case(Path('D:/codes/collage_outputs/sdk-serial-20261008-r1/tasks/16-海边人像拼图'),root/'10-海边人像-Live抠图',materials,
             {'photo_top':'20261009-105009.mp4','photo_right':'20261009-105009.mp4','photo_cutout':'20261009-105009.mp4'},model,report)


if __name__=='__main__':main()
