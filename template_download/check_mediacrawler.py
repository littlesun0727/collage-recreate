"""Offline smoke test against the actual pinned upstream request implementation."""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import patch

BASE=Path(__file__).resolve().parent
from paths import DATA_ROOT
os.environ['MPLCONFIGDIR']=str(DATA_ROOT/'mediacrawler-cache'/'matplotlib')
sys.path.insert(0,str(BASE/'vendor'/'MediaCrawler'))

import httpx
from media_platform.xhs.client import XiaoHongShuClient
from media_platform.xhs.exception import IPBlockError, PlatformAccessError


async def main():
    cases=[(200,{'success':True,'data':{'items':[]}},None),
           (200,{'success':False,'code':300012},IPBlockError),
           (429,{},PlatformAccessError)]
    for status,body,expected in cases:
        count=0
        def handler(request):
            nonlocal count
            count+=1
            return httpx.Response(status,json=body)
        client=XiaoHongShuClient(headers={},playwright_page=None,cookie_dict={})
        with patch('media_platform.xhs.client.make_async_client',
                   side_effect=lambda **kwargs:httpx.AsyncClient(transport=httpx.MockTransport(handler))):
            try:
                result=await XiaoHongShuClient.request.__wrapped__(client,'GET','https://example.com/test')
                assert expected is None and result=={'items':[]}
            except Exception as exc:
                assert expected is not None and isinstance(exc,expected),type(exc).__name__
        assert count==1, 'Each case must make exactly one request'
    print('Pinned MediaCrawler import and 3 offline HTTP scenarios passed; no platform requests sent.')


if __name__=='__main__':
    asyncio.run(main())
