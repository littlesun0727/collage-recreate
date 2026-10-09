"""Contract tests for immutable evidence, truthful progress and read-only serving."""
import hashlib
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'skill/scripts'))
from common import read, save, sha, timed, locked, event
from prepare import prepare, boxes
from scene import compile_scene, apply_review
from render import render, accept_review
from observation import checkpoint, evidence, publish
from workbench.server.store import Store, events
from workbench.server.http import create_server


@pytest.fixture
def run(tmp_path):
    inputs = tmp_path/'inputs'; inputs.mkdir()
    Image.new('RGB', (200, 300), '#aecead').save(inputs/'customer.png')
    Image.new('RGB', (200, 300), '#f4ead8').save(tmp_path/'reference.png')
    run = tmp_path/'tasks'/'fixture'; prepare(tmp_path/'reference.png', [inputs], run, width=200)
    analysis = {'schema_version':'collage-analysis-v1','reference_size':[200,300], 'objects':[
        {'id':'background','kind':'background','bbox':[0,0,200,300],'label':'底色','description':'纸底','style':{'fill':'#F4EAD8'}},
        {'id':'photo','kind':'photo','bbox':[20,40,180,260],'label':'客户照片','description':'照片窗口'},
        {'id':'star','kind':'overlay','bbox':[150,20,190,60],'label':'装饰','description':'圆形','method':'local','style':{'shape':'ellipse','fill':'#F1C45A'}}],
        'layer_order':['background','photo','star']}
    save(run/'analysis.json', analysis)
    save(run/'bindings.json', {'schema_version':'collage-bindings-v1','photos':[{'slot_id':'photo','asset_id':read(run/'prepared/catalog.json')['assets'][0]['id'],'reason':'test'}],'texts':[]})
    boxes(run, analysis)
    checkpoint(run, 2, 'complete', '已检查叠框')
    return run


def build(run):
    with locked(run), timed(run, 'build'):
        compile_scene(run); render(run)


def document(run):
    store = Store([run]); key = next(iter(store.tasks()))
    return store, store.document(key)


def adjust(run):
    r=read(run/'result.json')
    plan={'schema_version':'collage-review-v1','render_id':r['render_id'],'verdict':'needs_changes','summary':'装饰向左移','checked_entire_composition':True,'items':[{'id':'star','action':'adjust','reason':'移开照片边缘','changes':{'bbox':[140,20,180,60]}}]}
    save(run/'adjustment.json',plan)
    with locked(run),timed(run,'apply'):
        evidence(run,run/'adjustment.json');apply_review(run,run/'adjustment.json');render(run)


def review(run):
    payload={'schema_version':'collage-review-v1','render_id':read(run/'result.json')['render_id'],'checked_entire_composition':True,'verdict':'pass','summary':'测试图检查完成','items':[]}
    save(run/'review.json',payload)
    with locked(run),timed(run,'review'):
        accept_review(run,run/'review.json')


def test_version_chain_preserves_pixels_and_intent(run):
    build(run); _, first=document(run)
    first_path=next((run/'observability/versions').glob('*/final.png')); digest=sha(first_path)
    adjust(run);store,d=document(run)
    assert len(d['versions'])==2
    assert d['versions'][1]['parent_id']==d['versions'][0]['id']
    assert sha(first_path)==digest and sha(run/'final.png')!=digest
    change=next(c for c in d['versions'][1]['changes'] if c['id']=='star')
    assert change['fields']['bbox']['before']==[150,20,190,60]
    assert change['fields']['bbox']['after']==[140,20,180,60]
    assert d['versions'][1]['intent']['items'][0]['reason']=='移开照片边缘'
    old=d['versions'][0]['materials'][-1]['images']['final'].split('/')[-1]
    assert store.artifact(d['id'],old)[1]=='image/png'


def test_review_does_not_carry_to_new_pixels(run):
    build(run);review(run);_,d=document(run)
    assert d['versions'][0]['review']['verdict']=='pass'
    adjust(run);_,d=document(run)
    assert d['versions'][0]['review']['verdict']=='pass'
    assert d['versions'][1]['review'] is None
    assert d['stages'][4]['status']=='waiting'
    with pytest.raises(ValueError):
        checkpoint(run,6,'complete','未经复核的新图')


def test_download_error_is_visible_without_discarding_successful_material(run):
    a=read(run/'analysis.json')
    a['objects'][-1]['method']='extract';save(run/'analysis.json',a)
    build(run)
    event(run,'reveal_group_finished',task='batch',requested_ids=['star','photo'],
          available_ids=['photo'],missing_ids=['star'],error='HTTPError HTTP 502')
    _,d=document(run)
    cards={m['id']:m for m in d['materials']}
    assert cards['star']['status']=='failed' and '502' in cards['star']['note']
    assert cards['photo']['status']=='complete'
    assert d['groups'][0]['missing_ids']==['star'] and '502' in d['groups'][0]['error']


def test_repeat_identical_render_does_not_duplicate_version_or_keep_acceptance(run):
    build(run);review(run)
    with locked(run),timed(run,'render'):
        render(run)
    _,d=document(run)
    assert len(d['versions'])==1 and d['versions'][0]['review'] is None


def test_partial_snapshot_never_replaces_published_output(run):
    build(run)
    incomplete=run/'observability/versions/.pending-broken';incomplete.mkdir()
    save(incomplete/'manifest.json',{'version_id':'broken'})
    save(run/'result.json',{'status':'rendering','exported':False})
    _,d=document(run)
    assert len(d['versions'])==1 and d['current_image']==d['versions'][0]['image']


def test_parallel_events_and_truncated_record_recovery(run):
    threads=[threading.Thread(target=lambda n=n:[event(run,'probe',number=n*20+i) for i in range(20)]) for n in range(5)]
    for t in threads:t.start()
    for t in threads:t.join()
    with (run/'events.jsonl').open('ab') as f:f.write(b'{"unfinished":"\xe4\xb8')
    event(run,'after_interruption')
    rows,broken=events(run)
    assert len([r for r in rows if r['event']=='probe'])==100
    assert rows[-1]['event']=='after_interruption' and broken==1


def test_new_attempt_cannot_count_previous_materials(run):
    build(run)
    with timed(run,'build'):
        _,d=document(run)
        assert all(m['status']=='pending' and m['images']['final'] is None for m in d['materials'])
        assert d['live']
    _,d=document(run)
    assert not d['live']


def test_material_visible_before_final_and_failed_count_is_distinct(run):
    with timed(run,'build'):
        event(run,'materials_started',material_attempt='a',ids=['photo','star'])
        p=run/'assets/one.png';p.parent.mkdir();Image.new('RGBA',(20,20),'red').save(p)
        event(run,'material_finished',material_attempt='a',object_id='photo',quality='ready',file=str(p),sha256=sha(p))
        event(run,'material_finished',material_attempt='a',object_id='star',quality='unresolved',file=str(p),sha256=sha(p))
        _,d=document(run)
        assert d['current_image'] is None
        assert next(m for m in d['materials'] if m['id']=='photo')['images']['final']
        assert next(m for m in d['materials'] if m['id']=='star')['status']=='unresolved'


def test_agent_completion_requires_analysis_artifacts(run):
    (run/'bindings.json').unlink()
    with pytest.raises(FileNotFoundError):checkpoint(run,2,'complete','done')


def test_extracted_process_image_survives_final_render(run):
    build(run)
    processed=run/'processed-overlay.png'
    Image.new('RGBA',(20,20),'red').save(processed)
    event(run,'material_extracted',object_id='star',raw_file=str(processed),processed_file=str(processed))
    store,d=document(run)
    card=next(m for m in d['materials'] if m['id']=='star')
    assert card['images']['final'] and card['images']['processed']
    token=card['images']['processed'].split('/')[-1]
    assert store.artifact(d['id'],token)[0]==processed.read_bytes()


def test_analysis_only_and_skipped_steps(run):
    checkpoint(run,2,'complete','仅分析交付',analysis_only=True)
    _,d=document(run)
    assert all(s['status']=='skipped' for s in d['stages'][2:5])
    assert d['stages'][5]['status']=='complete'


def test_legacy_does_not_invent_history(run):
    (run/'events.jsonl').unlink()
    _,d=document(run)
    assert d['historical'] and d['history']==[] and d['versions']==[]
    assert d['stages'][1]['status']=='saved'


def test_observer_can_be_disabled_without_blocking_render(run,monkeypatch):
    monkeypatch.setenv('COLLAGE_OBSERVE','0')
    build(run)
    assert (run/'final.png').exists() and not (run/'observability/versions').exists()


def test_http_denies_arbitrary_files_and_writes_and_restores_saved_state(run):
    build(run);server=create_server([run],0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(base+'/api/tasks') as r:tasks=json.load(r)
        with urllib.request.urlopen(base+'/api/tasks/'+tasks[0]['id']) as r:d=json.load(r)
        serialized=json.dumps(d)
        assert str(run).replace('\\','\\\\') not in serialized
        with urllib.request.urlopen(base+d['current_image']) as r:assert r.headers['Content-Type']=='image/png'
        for path in ['/api/tasks/'+tasks[0]['id']+'/images/../../input.json','/../skill/SKILL.md']:
            with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(base+path)
            assert exc.value.code==404
        req=urllib.request.Request(base+'/api/tasks',data=b'{}',method='POST')
        with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(req)
        assert exc.value.code==501
        req=urllib.request.Request(base+'/api/tasks',headers={'Host':'evil.example'})
        with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(req)
        assert exc.value.code==403
        restored=Store([run]);key=next(iter(restored.tasks()))
        assert restored.document(key)['versions'][0]['id']==d['versions'][0]['id']
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
