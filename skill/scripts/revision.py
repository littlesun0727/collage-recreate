"""Prepare reviewed edits in a private copy, then publish against an exact base."""
from pathlib import Path
import shutil
import re
from common import read, save, sha, fingerprint, event
from scene import apply_review, load_scene
from render import render, accept_review


def folder(run, request_id):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',request_id):raise ValueError('Invalid request ID')
    return Path(run)/'chat/requests'/request_id


def prepare_revision(run, request_id, plan):
    run=Path(run);root=folder(run,request_id);candidate=root/'candidate'
    current=read(run/'result.json');base=plan['render_id']
    if current.get('render_id')!=base:raise ValueError('Base version changed; review the latest image first')
    load_scene(run)
    if candidate.exists():
        saved=read(root/'candidate.json') if (root/'candidate.json').exists() else {}
        if saved.get('plan_sha256')==fingerprint(plan) and saved.get('base_render_id')==base:
            return saved
        if saved:raise ValueError('Candidate belongs to a different plan')
        if not candidate.resolve().is_relative_to(root.resolve()) or candidate.is_symlink():raise ValueError('Unsafe candidate path')
        shutil.rmtree(candidate)
    root.mkdir(parents=True,exist_ok=True)
    # Source references remain immutable reads from the original task/customer catalog.
    shutil.copytree(run,candidate,ignore=shutil.ignore_patterns('chat','observability','.write.lock','events.jsonl','sdk-timing.json'))
    save(root/'plan.json',plan)
    apply_review(candidate,root/'plan.json')
    result=render(candidate,publish_result=False)
    value={'base_render_id':base,'plan_sha256':fingerprint(plan),'render_id':result['render_id'],
           'candidate':str(candidate),'final_sha256':sha(candidate/'final.png'),'scene_sha256':sha(candidate/'scene.json')}
    save(root/'candidate.json',value)
    return value


def rebase(value, before, after):
    if isinstance(value,str):
        for a,b in [(str(before),str(after)),(before.as_posix(),after.as_posix())]:
            if value==a or value.startswith(a+'/') or value.startswith(a+'\\'):return b+value[len(a):]
        return value
    if isinstance(value,list):return [rebase(v,before,after) for v in value]
    if isinstance(value,dict):return {k:rebase(v,before,after) for k,v in value.items()}
    return value


def recover_commit(run):
    """Called under the task writer lock before retry; never guesses a new base."""
    run=Path(run);marker=run/'chat/commit.json'
    if not marker.exists():return
    state=read(marker)
    if state['status'] in ['done','rolled_back']:return
    backup=folder(run,state['request_id'])/'rollback'
    for rel,existed in state['files'].items():
        target=run/rel
        if existed:
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(backup/rel,target)
        else:target.unlink(missing_ok=True)
    state['status']='rolled_back';save(marker,state)


def commit_revision(run,request_id,review):
    run=Path(run);root=folder(run,request_id)
    if (root/'committed.json').exists():
        outcome=read(root/'committed.json')
        if read(run/'result.json').get('render_id')==outcome['render_id']:publish_revision(run,root,outcome)
        return outcome
    marker=read(run/'chat/commit.json') if (run/'chat/commit.json').exists() else {}
    if marker.get('request_id')==request_id and marker.get('status')=='done' and marker.get('outcome'):
        save(root/'committed.json',marker['outcome']);publish_revision(run,root,marker['outcome']);return marker['outcome']
    recover_commit(run)
    saved=read(root/'candidate.json');candidate=root/'candidate'
    if read(run/'result.json').get('render_id')!=saved['base_render_id']:raise ValueError('Base version changed')
    if sha(candidate/'scene.json')!=saved['scene_sha256'] or sha(candidate/'final.png')!=saved['final_sha256']:
        raise ValueError('Candidate changed since rendering')
    save(root/'review.json',review);accept_review(candidate,root/'review.json')
    # Only generated outputs are promoted. Analysis, catalog and original references stay intact.
    files=[p for directory in ['assets','previews','reviews'] for p in (candidate/directory).rglob('*') if p.is_file()]
    files += [candidate/n for n in ['scene.json','result.json','final.png','review.json']]
    backup=root/'rollback';backup.mkdir(exist_ok=True)
    mapping={p.relative_to(candidate).as_posix():(run/p.relative_to(candidate)).exists() for p in files}
    for rel,existed in mapping.items():
        if existed:
            dest=backup/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(run/rel,dest)
    marker={'request_id':request_id,'status':'promoting','files':mapping};save(run/'chat/commit.json',marker)
    try:
        for p in files:
            dest=run/p.relative_to(candidate);dest.parent.mkdir(parents=True,exist_ok=True)
            if p.suffix=='.json':save(dest,rebase(read(p),candidate,run))
            else:shutil.copy2(p,dest)
        result=read(run/'result.json');result['scene_sha256']=sha(run/'scene.json')
        result['render_id']=fingerprint([result['scene_sha256'],result['final_sha256']])[:16]
        final_review=read(run/'review.json')
        final_review.update(render_id=result['render_id'],scene_sha256=result['scene_sha256'],final_sha256=result['final_sha256'])
        result['visual_review']=final_review;save(run/'result.json',result);save(run/'review.json',final_review)
        accept_review(run,run/'review.json')
        load_scene(run)
    except Exception:
        recover_commit(run);raise
    outcome={'render_id':result['render_id'],'base_render_id':saved['base_render_id'],'request_id':request_id}
    marker.update(status='done',outcome=outcome);save(run/'chat/commit.json',marker)
    save(root/'committed.json',outcome)
    publish_revision(run,root,outcome)
    return outcome


def publish_revision(run,root,outcome):
    result=read(run/'result.json');final_review=read(run/'review.json')
    from observation import evidence,publish,operation
    with operation(run,'revision-commit'):
        evidence(run,root/'plan.json');publish(run)
        event(run,'review_registered',render_id=result['render_id'],review=final_review)
        event(run,'chat_revision_published',**outcome)
    manifests=list((run/'observability/versions').glob('*/manifest.json'))
    if not any(read(p).get('render_id')==outcome['render_id'] for p in manifests):
        raise ValueError('The reviewed image is saved but its version snapshot needs a retry')
