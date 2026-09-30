"""Offline ablation of retained layers and explicit recovery; originals are read-only."""
import argparse
import hashlib
import html
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def worker(args):
    import socket
    def blocked(*a,**kw):raise RuntimeError('Network disabled for offline recovery evaluation')
    socket.socket.connect=blocked;socket.socket.connect_ex=blocked;socket.create_connection=blocked
    deps=Path(__file__).resolve().parents[1]/('.deps314' if sys.version_info[:2]==(3,14) else '.deps')
    if deps.is_dir():sys.path.insert(0,str(deps))
    sys.path.insert(0,str(Path(args.skill)/'scripts'))
    import reveal
    reveal.http=blocked;reveal.fetch_batch=blocked;reveal.api_key=blocked
    from scene import compile_scene
    from render import render
    source=Path(args.source);run=Path(args.run)
    if args.resume:
        from recovery import recover
        result=recover(run,args.plan)
        save(run/'repair-result.json',result)
        print(json.dumps({'status':'repaired','run':str(run)},ensure_ascii=True));return
    run.mkdir(parents=True,exist_ok=False)
    for name in ['input.json','analysis.json','bindings.json']:shutil.copy2(source/name,run/name)
    shutil.copytree(source/'prepared',run/'prepared')
    cache=source/'assets/photos/.cache'
    if cache.exists():shutil.copytree(cache,run/'assets/photos/.cache')
    config={**read(source/'reveal-config.json'),'remote':False,'cache':str(source/'assets/reveal')}
    started=time.monotonic();compile_scene(run,config);result=render(run)
    save(run/'evaluation.json',{'seconds':round(time.monotonic()-started,3),'network_disabled':True,'result':result})
    print(json.dumps({'status':'rendered','run':str(run),'seconds':round(time.monotonic()-started,3)},ensure_ascii=True))


def inventory(root):
    return {str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()}


def main(args):
    root=Path(args.output);skill=Path(args.skill);source=Path(args.source)
    cases=['20260908-192922','拼贴2','拼贴7']
    before={case:inventory(source/case/'run') for case in cases}
    save(root/'source-inventory-before.json',before)
    variants={}
    for key in ['no-clear-only','no-reject-only']:
        variant=root/key/'skill';shutil.copytree(root/'baseline-skill',variant,dirs_exist_ok=True)
        p=variant/'scripts/reveal_assets.py';code=p.read_text(encoding='utf-8')
        if key=='no-clear-only':
            old="image.putalpha(Image.fromarray(alpha));mp=folder/(oid+'-window.png');mask.save(mp)"
            new="mp=folder/(oid+'-window.png');mask.save(mp)"
        else:old='if broad or stripe:';new='if False:  # Isolated evaluation: disable area-based rejection only.'
        assert code.count(old)==1
        p.write_text(code.replace(old,new),encoding='utf-8');variants[key]=variant
    variants['preserve']=skill
    rows=[]
    for case in cases:
        for name,code_root in variants.items():
            run=root/case/name
            cmd=[sys.executable,__file__,'--worker','--skill',str(code_root),'--source',str(source/case/'run'),'--run',str(run)]
            proc=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if proc.returncode:raise RuntimeError(proc.stdout+'\n'+proc.stderr)
            print(proc.stdout.strip(),flush=True)
        run=root/case/'preserve'
        shutil.copy2(run/'final.png',root/case/'preserve.png')
        save(root/case/'preserve-result.json',read(run/'result.json'))
        reference=read(run/'input.json')['reference']['sha256']
        plan={'schema_version':'collage-recovery-v1','reference_sha256':reference,'groups':[],'windows':[]}
        if case=='20260908-192922':
            plan['groups']=[{'primary':'cat_head_sticker',
                'member_ids':['paper_bottom_left','blue_dotted_star','croissant_arrows','cat_head_sticker'],
                'parts':[
                    {'source_id':'paper_bottom_left','bbox':[40,1645,405,1920]},
                    {'source_id':'blue_dotted_star','bbox':[20,1745,500,2230]},
                    {'source_id':'paper_bottom_left','bbox':[170,1880,600,2306]},
                    {'source_id':'croissant_arrows','bbox':[0,1695,95,1810]},
                    {'source_id':'croissant_arrows','bbox':[140,1615,270,1698]},
                    {'source_id':'croissant_arrows','bbox':[335,1700,435,1815]}],
                'reason':'原始06层包含猫脸和可颂，03层是重复橙色底形；按实际内容恢复左下贴纸组，箭头取自04层，排除零散污染。'}]
        elif case=='拼贴7':
            plan['windows']=[{'overlay_id':oid,'photo_id':pid,'padding':0,'reason':'原始返回层窗口仍可见旧照片，按关联窗口局部清理。'} for oid,pid in [
                ('rear_right_polaroid','rear_right_photo'),('rear_left_polaroid','rear_left_photo'),
                ('heart_collage','heart_photo_3'),('heart_collage','heart_photo_4'),('heart_collage','heart_photo_5')]]
            plan['omit']=[{'id':'cutout_torn_border','reason':'返回撕纸边框包含原人物；现有规则窗口无法分离，隐藏该层并保留缺口。'}]
        plan_path=root/case/'repair.json';save(plan_path,plan)
        cmd=[sys.executable,__file__,'--worker','--resume','--skill',str(skill),'--source',str(source/case/'run'),'--run',str(run),'--plan',str(plan_path)]
        proc=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if proc.returncode:raise RuntimeError(proc.stdout+'\n'+proc.stderr)
        print(proc.stdout.strip(),flush=True)
        result=read(run/'result.json')
        rows.append({'case':case,'recovered_groups':len(plan['groups']),'repaired_windows':len(plan['windows']),
                     'omitted_layers':[o['id'] for o in plan.get('omit',[])],
                     'incomplete_objects':result['incomplete_objects'],'photo_visible_fractions':result['photo_visible_fractions'],
                     'renders_verified':False,'preserve_seconds':read(run/'evaluation.json')['seconds']})
    after={case:inventory(source/case/'run') for case in cases}
    verification={'network_disabled':True,'remote_submissions':0,'original_files_unchanged':before==after,
                  'analysis_bindings_unchanged':all(sha(root/c/'preserve'/f)==sha(source/c/'run'/f) for c in cases for f in ['analysis.json','bindings.json']),
                  'first_preserved':all(sha(root/c/'preserve/previews/first.png')==sha(root/c/'preserve.png') for c in cases)}
    save(root/'verification.json',verification);save(root/'summary.json',rows)
    page=['<!doctype html><meta charset="utf-8"><title>素材恢复离线对照</title>',
          '<style>body{font-family:system-ui;margin:24px;background:#f2f3f5;color:#20252c}.row{display:grid;grid-template-columns:repeat(6,minmax(210px,1fr));gap:12px;overflow:auto}figure{margin:0;background:white;padding:10px}img{width:100%;height:auto}figcaption{font-weight:600;margin-bottom:8px}p{line-height:1.6}a{color:#1768ae}</style>',
          '<h1>素材恢复与后处理：离线对照</h1><p>使用既有360缓存，0次远端请求。前两项消融仅改变旧实现的一处操作；新流程同时保留原照片几何。右侧为局部修复结果，所有版本仍需视觉复核，不代表成品验收通过。点击图片查看原图。</p>']
    def url(p):
        # Relative paths remain usable when this page is opened locally.
        import os
        return Path(os.path.relpath(p,root)).as_posix()
    for row in rows:
        case=row['case'];run=root/case/'preserve'
        page.append('<h2>'+html.escape(case)+'</h2><div class="row">')
        entries=[('参考',source/case/'run/prepared/reference.png'),('原流程',source/case/'run/final.png'),
                 ('仅关闭强制清窗',root/case/'no-clear-only/final.png'),('仅关闭面积回退',root/case/'no-reject-only/final.png'),
                 ('新流程：原样保留',root/case/'preserve.png'),('新流程：按需恢复',run/'final.png')]
        for title,p in entries:
            u=html.escape(url(p));page.append(f'<figure><figcaption>{title}</figcaption><a href="{u}"><img loading="lazy" src="{u}"></a></figure>')
        page.append('</div><p><a href="'+html.escape(url(root/case/'repair.json'))+'">局部修复配置</a> · <a href="'+html.escape(url(run/'assets/reveal/index.json'))+'">素材处理记录</a></p>')
    (root/'index.html').write_text('\n'.join(page),encoding='utf-8')
    print(json.dumps(verification),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--worker',action='store_true');p.add_argument('--resume',action='store_true')
    p.add_argument('--skill',required=True);p.add_argument('--source',required=True);p.add_argument('--output');p.add_argument('--run');p.add_argument('--plan')
    args=p.parse_args()
    if args.worker:worker(args)
    else:main(args)
