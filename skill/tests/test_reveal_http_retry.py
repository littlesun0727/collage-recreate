from urllib.error import HTTPError

import pytest
from PIL import Image

from common import read, save
from reveal import fetch_batch, reserve_submission


class Response:
    def __init__(self, data): self.data=data
    def raise_for_status(self): pass
    def json(self): return self.data


def inputs(tmp_path):
    reference=tmp_path/'reference.png'
    Image.new('RGB',(64,64),'white').save(reference)
    root=tmp_path/'reveal';batch=root/'batch'
    return root,batch,(batch,reference,[{'id':'deco','bbox':[0,0,32,32]}],'fake')


@pytest.mark.parametrize('code',[502,503,504])
def test_gateway_retry_recovers_and_counts_every_submit(tmp_path,code):
    root,batch,args=inputs(tmp_path);reserve_submission(batch,root)
    calls=[];waits=[]
    def post(url,**kwargs):
        calls.append(url.rsplit('/',1)[-1])
        if calls==['submit_task']:raise HTTPError(url,code,'gateway',{},None)
        return Response({'response_status':0,'task_id':'recovered'} if url.endswith('submit_task') else
                        {'status':'done','output':{}})
    fetch_batch(*args,post=post,sleep=waits.append)
    assert calls==['submit_task','submit_task','query_task'] and waits==[2]
    assert len(list(root.rglob('budget-reserved.json')))==2
    assert read(batch/'submit-history/attempt-01.json')['http_status']==code
    fetch_batch(*args,post=lambda *a,**k:pytest.fail('completed cache must be reused'))


def test_repeated_gateway_failure_stops_across_restarts(tmp_path):
    root,batch,args=inputs(tmp_path);reserve_submission(batch,root);calls=[]
    def post(url,**kwargs):
        calls.append(url);raise HTTPError(url,502,'gateway',{},None)
    with pytest.raises(HTTPError):fetch_batch(*args,post=post,sleep=lambda _:None)
    with pytest.raises(ValueError,match='exhausted'):fetch_batch(*args,post=post,sleep=lambda _:None)
    assert len(calls)==3 and len(list(root.rglob('budget-reserved.json')))==3


def test_retry_cannot_exceed_shared_run_budget(tmp_path):
    root,batch,args=inputs(tmp_path)
    for path in [root/'other-1',root/'other-2',batch]:reserve_submission(path,root)
    calls=[]
    def post(url,**kwargs):calls.append(url);raise HTTPError(url,502,'gateway',{},None)
    for _ in range(2):
        with pytest.raises(ValueError,match='Hard limit'):fetch_batch(*args,post=post,sleep=lambda _:None)
    assert len(calls)==1 and len(list(root.rglob('budget-reserved.json')))==3


@pytest.mark.parametrize('code',[401,402,403,429])
def test_auth_balance_and_rate_limit_submit_do_not_repeat(tmp_path,code):
    _,batch,args=inputs(tmp_path);calls=[]
    def post(url,**kwargs):calls.append(url);raise HTTPError(url,code,'rejected',{},None)
    with pytest.raises(HTTPError):fetch_batch(*args,post=post,sleep=lambda _:None)
    with pytest.raises(ValueError,match='non-retryable'):fetch_batch(*args,post=post,sleep=lambda _:None)
    assert len(calls)==1


def test_query_transport_failure_retries_same_task_without_submission(tmp_path):
    root,batch,args=inputs(tmp_path);reserve_submission(batch,root)
    calls=[];waits=[]
    def post(url,**kwargs):
        endpoint=url.rsplit('/',1)[-1];calls.append((endpoint,kwargs['json'].get('task_id')))
        if endpoint=='submit_task':return Response({'response_status':0,'task_id':'same-id'})
        if len(calls)==2:raise TimeoutError('temporary network interruption')
        return Response({'status':'done','output':{}})
    fetch_batch(*args,post=post,sleep=waits.append)
    assert calls==[('submit_task',None),('query_task','same-id'),('query_task','same-id')]
    assert waits==[2] and len(list(root.rglob('budget-reserved.json')))==1


def test_restart_after_gateway_error_can_resume_remaining_attempt(tmp_path):
    root,batch,args=inputs(tmp_path);reserve_submission(batch,root)
    save(batch/'submission_started.json',{'attempt':1,'http_status':502})
    calls=[]
    def post(url,**kwargs):
        calls.append(url)
        return Response({'response_status':0,'task_id':'resumed'} if url.endswith('submit_task') else
                        {'status':'done','output':{}})
    fetch_batch(*args,post=post,sleep=lambda _:None)
    assert len(calls)==2 and len(list(root.rglob('budget-reserved.json')))==2
