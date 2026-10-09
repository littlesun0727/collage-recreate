import io
from pathlib import Path
from urllib.error import HTTPError
import pytest
from PIL import Image
from test_pipeline import task
from common import read, save, sha
from reveal import fetch_batch
from reveal_grouped import acquire


@pytest.mark.parametrize('failures',[1,99])
def test_failure_retains_other_files_and_resume_never_posts(task,failures):
    batch=task/'download-retry';calls=[];posts=[]
    buf=io.BytesIO();Image.new('RGBA',(100,150),'blue').save(buf,format='PNG')
    class Response:
        content=buf.getvalue()
        def __init__(self,data=None):self.data=data
        def raise_for_status(self):pass
        def json(self):return self.data
    def post(url,**kwargs):
        posts.append(url)
        return Response({'response_status':0,'task_id':'same-task'} if url.endswith('submit_task') else
                        {'status':'done','output':{'boxes_mapping_index':[0,1],'layers_base':['bg','first','second']}})
    def get(url,**kwargs):
        calls.append(url)
        if url=='first' and calls.count(url)<=failures:raise HTTPError(url,502,'Bad Gateway',{},None)
        return Response()
    args=(batch,task/'prepared/reference.png',[{'id':'a','bbox':[0,0,40,40]},{'id':'b','bbox':[40,0,80,40]}],'fake')
    if failures==1:fetch_batch(*args,post=post,get=get,sleep=lambda _:None)
    else:
        with pytest.raises(RuntimeError,match='HTTP 502'):fetch_batch(*args,post=post,get=get,sleep=lambda _:None)
        assert not (batch/'layers_base_01.png').exists()
    assert (batch/'layers_base_02.png').exists() and len(posts)==2
    assert calls.count('first')==(2 if failures==1 else 3)
    downloaded=[]
    def resumed(url,**kwargs):downloaded.append(url);return Response()
    fetch_batch(*args,post=lambda *a,**k:pytest.fail('Must reuse completed task'),get=resumed)
    assert downloaded==([] if failures==1 else ['first'])
    assert read(batch/'download-report.json')['complete']


@pytest.mark.parametrize('broken',['missing','corrupt'])
def test_partial_layers_restore_even_offline_with_valid_identity(tmp_path,monkeypatch,broken):
    import reveal
    s={'reference_size':[200,200],'objects':[
        {'id':'a','kind':'overlay','bbox':[10,10,40,40]},
        {'id':'b','kind':'overlay','bbox':[45,10,75,40]}]}
    ref=tmp_path/'reference.png';Image.new('RGB',(200,200)).save(ref);s['reference']={'file':str(ref)}
    def partial(task,reference,objects,key,timeout):
        save(task/'request_meta.json',{'reference_sha256':sha(reference),'objects':objects})
        save(task/'query_response.json',{'status':'done','task_id':'original','output':{'boxes_mapping_index':[0,1]}})
        with Image.open(reference) as im:size=im.size
        Image.new('RGBA',size,'red').save(task/'layers_base_01.png')
        if broken=='corrupt':(task/'layers_base_02.png').write_bytes(b'broken png')
        raise RuntimeError('HTTP 502 during download')
    monkeypatch.setattr(reveal,'api_key',lambda _:'fake')
    monkeypatch.setattr(reveal,'fetch_batch',partial)
    run=tmp_path/'run'
    assets,receipts=acquire(run,s,s['objects'],{'remote':True})
    assert set(assets)=={'a'} and receipts[0]['missing_ids']==['b'] and receipts[0]['available_ids']==['a']
    assert receipts[0]['status']=='done' and '502' in receipts[0]['error']
    monkeypatch.setattr(reveal,'fetch_batch',lambda *a,**k:pytest.fail('offline'))
    assets,receipts=acquire(run,s,s['objects'],{'remote':False})
    assert set(assets)=={'a'} and receipts[0]['missing_ids']==['b']
    task=Path(receipts[0]['task']);meta=read(task/'request_meta.json');meta['reference_sha256']='wrong';save(task/'request_meta.json',meta)
    assets,receipts=acquire(run,s,s['objects'],{'remote':False})
    assert not assets and 'metadata changed' in receipts[0]['error']
