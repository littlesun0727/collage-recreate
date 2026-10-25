"""Isolated, bounded research worker using the pinned MediaCrawler client.

Upstream is subject to NON-COMMERCIAL LEARNING LICENSE 1.1; see its LICENSE.
This adapter does not modify upstream files or retry restricted requests.
"""
import argparse
import asyncio
import json
import logging
import inspect
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType
from paths import DATA_ROOT

BASE = Path(__file__).resolve().parent
REPO = BASE/'vendor'/'MediaCrawler'


class StopAccess(Exception):
    pass


def classify_exception(error):
    error = underlying_exception(error)
    # Never serialize arbitrary exception bodies (may contain signed URLs/cookies).
    text = str(error)
    kind = type(error).__name__
    if kind == 'IPBlockError' or '300012' in text:
        return 'blocked','300012: platform IP/network restriction'
    if kind == 'PlatformAccessError':
        return 'blocked','Platform access restriction'
    if 'CAPTCHA' in text or 'Verifytype' in text:
        return 'blocked','Platform requested verification'
    if isinstance(error,StopAccess):
        return 'blocked',text
    return 'error',kind


def underlying_exception(error):
    """Unwrap retry containers without exposing request data in exception messages."""
    seen=set()
    while id(error) not in seen:
        seen.add(id(error))
        attempt=getattr(error,'last_attempt',None)
        inner=attempt.exception() if attempt is not None else None
        if not isinstance(inner,BaseException):break
        error=inner
    return error


def safe_exception_detail(error):
    import traceback
    root=underlying_exception(error)
    return {'outer_type':type(error).__name__,'root_type':type(root).__name__,
            'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                      for f in traceback.extract_tb(root.__traceback__)]}


async def collect(args, report, out):
    # Resolve upstream's `store`, `config`, `tools` in its own interpreter.
    sys.path.insert(0,str(REPO))
    os.chdir(REPO)
    os.environ['MPLCONFIGDIR'] = str(DATA_ROOT/'mediacrawler-cache'/'matplotlib')
    import config
    config.ENABLE_IP_PROXY = False
    config.ENABLE_GET_COMMENTS = False
    config.ENABLE_GET_SUB_COMMENTS = False
    config.ENABLE_GET_MEDIA = False
    config.ENABLE_GET_WORDCLOUD = False
    from media_platform.xhs.client import XiaoHongShuClient
    from media_platform.xhs import client as client_module
    from media_platform.xhs.help import get_search_id
    from media_platform.xhs.field import SearchNoteType
    from playwright.async_api import async_playwright
    from tools import utils
    utils.logger.disabled = True  # Upstream logs can contain response data and tokens.
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    # Record protocol status only, never raw headers, cookies, URLs or response bodies.
    original_client_factory=client_module.make_async_client
    async def response_summary(response):
        await response.aread()
        entry={'http_status':response.status_code,
               'verification_requested':response.status_code in (461,471)}
        try:
            body=response.json()
            if isinstance(body,dict):
                code=body.get('code')
                if isinstance(code,int) or (isinstance(code,str) and re.fullmatch(r'-?\d{1,12}',code)):
                    entry['application_code']=code
                if type(body.get('success')) is bool:entry['success']=body['success']
        except (ValueError,TypeError):
            entry['json_response']=False
        report.setdefault('http_responses',[]).append(entry)
    def diagnostic_client(**kwargs):
        hooks=kwargs.setdefault('event_hooks',{})
        hooks.setdefault('response',[]).append(response_summary)
        return original_client_factory(**kwargs)
    client_module.make_async_client=diagnostic_client
    report['upstream_imported'] = True
    report['stage'] = 'browser_preflight'
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(args.profile),channel='chrome',headless=False,
            args=[] if args.wait_for_login else ['--start-minimized'],viewport={'width':1440,'height':1000})
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            response = await page.goto('https://www.xiaohongshu.com/',wait_until='domcontentloaded',timeout=45000)
            await page.wait_for_timeout(5000)
            body = await page.locator('body').inner_text()
            title = await page.title()
            if '/website-login/error' in page.url or title == '安全限制' or 'IP存在风险' in body:
                await page.screenshot(path=str(out/'access-stopped.png'))
                code = re.search(r'\b30001[12]\b',body)
                raise StopAccess('Browser security restriction'+(': '+code.group() if code else ''))
            if response and response.status in (401,403,429):
                raise StopAccess('Browser HTTP '+str(response.status))
            async def verification_visible():
                for selector in ('iframe[src*="captcha"]','[class*="captcha"]','.login-container'):
                    if await page.locator(selector).first.is_visible():
                        return True
                return False
            if await verification_visible():
                await page.screenshot(path=str(out/'access-stopped.png'))
                if not args.wait_for_login:
                    raise StopAccess('Login or verification required; existing session is not usable')
                print('Waiting for manual identity verification in the browser; collection resumes automatically.',flush=True)
                report['stage']='manual_verification'
                deadline=asyncio.get_running_loop().time()+args.wait_for_login
                while await verification_visible():
                    if asyncio.get_running_loop().time()>=deadline:
                        raise StopAccess('Manual identity verification timed out')
                    await page.wait_for_timeout(2000)
                    if '/website-login/error' in page.url or await page.title()=='安全限制':
                        raise StopAccess('Browser security restriction after verification')
                await page.wait_for_timeout(2000)
            cookies = await context.cookies(['https://www.xiaohongshu.com/','https://edith.xiaohongshu.com/'])
            cookie_dict = {c['name']:c['value'] for c in cookies}
            if not cookie_dict.get('web_session'):
                raise StopAccess('No existing login session; use run.py login before retrying')
            headers = {'Accept':'application/json, text/plain, */*',
                       'Content-Type':'application/json;charset=UTF-8',
                       'Origin':'https://www.xiaohongshu.com',
                       'Referer':'https://www.xiaohongshu.com/',
                       'User-Agent':await page.evaluate('navigator.userAgent'),
                       'Cookie':'; '.join(f'{k}={v}' for k,v in cookie_dict.items())}
            client = XiaoHongShuClient(timeout=30,headers=headers,
                playwright_page=page,cookie_dict=cookie_dict,proxy=None)
            # Use upstream request implementation, removing its general retry wrapper.
            client.request = MethodType(inspect.unwrap(XiaoHongShuClient.request),client)
            report['stage'] = 'search'
            report['search_requests'] += 1
            result = await client.get_note_by_keyword(keyword=args.keyword,search_id=get_search_id(),page=args.page,
                                                       note_type=SearchNoteType.IMAGE)
            items = [x for x in result.get('items',[]) if
                re.fullmatch(r'[a-fA-F0-9]{24}',str(x.get('id',''))) and x.get('xsec_token')]
            report['search_results'] = len(items)
            report['has_more'] = result.get('has_more', result.get('hasMore', bool(items)))
            if getattr(args,'search_only',False):
                report.update(status='success',stage='finished',search_only=True)
                return
            if not items:
                report.update(status='empty',reason='Search returned no usable note links')
                return
            with (out/'notes.jsonl').open('w',encoding='utf-8') as file:
                for item in items[args.offset:args.offset+args.notes]:
                    await asyncio.sleep(args.delay)
                    report['stage'] = 'details'
                    report['detail_requests'] += 1
                    note = await client.get_note_by_id(note_id=item['id'],
                        xsec_source=item.get('xsec_source') or 'pc_search',xsec_token=item['xsec_token'])
                    if not note:
                        report['empty_details'] += 1
                        continue
                    # Persist only fields needed for template collection.
                    record = {k:note.get(k) for k in ('note_id','title','type','image_list')}
                    record['note_id'] = record['note_id'] or item['id']
                    record['source_keyword'] = args.keyword
                    file.write(json.dumps(record,ensure_ascii=False)+'\n')
                    file.flush()
                    report['fetched_notes'] += 1
                    report['gallery_images'] += len(record.get('image_list') or [])
                    print(f"Fetched {report['fetched_notes']} note(s), {report['gallery_images']} gallery image(s)",flush=True)
            report['status'] = 'success' if report['gallery_images'] else 'empty'
            report['stage'] = 'finished'
        finally:
            await context.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--keyword',default='醒图拼贴模板')
    parser.add_argument('--notes',type=int,default=1)
    parser.add_argument('--page',type=int,default=1)
    parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--delay',type=float,default=15)
    parser.add_argument('--search-only',action='store_true',help='One diagnostic search, no note details or downloads')
    parser.add_argument('--wait-for-login',type=int,default=0,help='Wait up to 300 seconds for manual verification in the same browser')
    parser.add_argument('--profile',type=Path,default=DATA_ROOT/'browser-profile')
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if args.page < 1 or args.offset < 0:
        parser.error('page >=1 and offset >=0 required')
    if not 1 <= args.notes <= 5 or args.delay < 10:
        parser.error('Research PoC supports 1–5 notes with >=10s interval')
    if not 0 <= args.wait_for_login <= 300:
        parser.error('wait-for-login must be between 0 and 300 seconds')
    out = args.output.resolve()
    out.mkdir(parents=True,exist_ok=True)
    args.profile = args.profile.resolve()
    manifest = json.loads((BASE/'mediacrawler.lock.json').read_text(encoding='utf-8'))
    revision = subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip()
    if revision != manifest['commit']:
        parser.error('Upstream commit differs from mediacrawler.lock.json')
    report = dict(started_at=datetime.now(timezone.utc).isoformat(),commit=revision,
                  keyword=args.keyword,note_limit=args.notes,status='running',stage='imports',
                  upstream_imported=False,search_requests=0,search_results=0,
                  detail_requests=0,fetched_notes=0,gallery_images=0,empty_details=0)
    try:
        asyncio.run(asyncio.wait_for(collect(args,report,out),timeout=300+args.wait_for_login))
    except KeyboardInterrupt:
        report.update(status='interrupted',reason='User interrupted')
    except Exception as exc:
        status,reason=classify_exception(exc)
        report.update(status=status,reason=reason,error_detail=safe_exception_detail(exc))
    finally:
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        (out/'collection-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['status']=='success' else 2


if __name__=='__main__':
    raise SystemExit(main())
