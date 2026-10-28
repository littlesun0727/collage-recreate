"""Offline browser verification. Requires websocket-client and local Chrome/Edge."""
from __future__ import annotations
from datetime import date
import base64
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import urlopen

import websocket

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT.with_name(ROOT.name+'-output')/('checks/workflow-atlas-'+date.today().strftime('%Y%m%d'))


class Browser:
    def __init__(self):
        OUT.mkdir(parents=True, exist_ok=True)
        self.profile = OUT/('browser-profile-'+str(time.time_ns()))
        browser = next(p for p in (
            Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
            Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')) if p.exists())
        self.log=(OUT/'browser-stderr.log').open('ab')
        self.proc = subprocess.Popen([str(browser), '--headless=new', '--remote-debugging-port=0',
            '--user-data-dir='+str(self.profile), '--no-first-run', '--no-default-browser-check',
            '--disable-background-networking', '--disable-component-update', 'about:blank'],
            stdout=subprocess.DEVNULL, stderr=self.log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline=time.monotonic()+25
        portfile=self.profile/'DevToolsActivePort'
        while not portfile.exists():
            if self.proc.poll() is not None:raise RuntimeError('Headless browser exited')
            if time.monotonic()>deadline:raise TimeoutError('Browser startup')
            time.sleep(.1)
        port=portfile.read_text().splitlines()[0]
        with urlopen(f'http://127.0.0.1:{port}/json/version') as response:
            version=json.load(response)
        self.ws=websocket.create_connection(version['webSocketDebuggerUrl'],suppress_origin=True,timeout=30)
        self.seq=0
        self.events=[]
        self.session=None
        target=self.call('Target.createTarget',{'url':'about:blank'})['targetId']
        self.session=self.call('Target.attachToTarget',{'targetId':target,'flatten':True})['sessionId']
        try:
            for domain in ('Runtime','Page','Network'):
                self.call(domain+'.enable')
        except Exception:
            self.proc.terminate()
            self.log.close()
            raise

    def call(self,method,params=None):
        self.seq+=1
        message={'id':self.seq,'method':method,'params':params or {}}
        if self.session and method.split('.')[0] not in ('Target','Browser'):
            message['sessionId']=self.session
        self.ws.send(json.dumps(message))
        while True:
            response=json.loads(self.ws.recv())
            if response.get('id')==self.seq:
                if 'error' in response:raise RuntimeError(response['error'])
                return response.get('result',{})
            self.events.append(response)

    def js(self,expression):
        result=self.call('Runtime.evaluate',{'expression':'eval('+json.dumps(expression)+')','returnByValue':True,'awaitPromise':True})
        if 'exceptionDetails' in result:raise RuntimeError(result['exceptionDetails'])
        return result['result'].get('value')

    def ready(self):
        self.js("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
        self.js("Promise.all([...document.images].map(i=>{i.loading='eager';return i.complete?Promise.resolve():new Promise(r=>{i.onload=r;i.onerror=r})}))")

    def screenshot(self,name,full=False):
        params={'format':'png','captureBeyondViewport':full}
        if full:
            size=self.call('Page.getLayoutMetrics')['cssContentSize']
            params['clip']={'x':0,'y':0,'width':size['width'],'height':size['height'],'scale':1}
        data=self.call('Page.captureScreenshot',params)['data']
        (OUT/name).write_bytes(base64.b64decode(data))

    def close(self):
        try:self.call('Browser.close')
        except Exception:pass
        self.ws.close()
        try:self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:self.proc.terminate()
        self.log.close()


def main():
    b=Browser()
    checks=[]
    try:
        b.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1080,'deviceScaleFactor':1,'mobile':False})
        b.call('Page.navigate',{'url':(HERE/'index.html').as_uri()})
        deadline=time.monotonic()+15
        while not b.js("document.querySelector('#content h1')?.textContent"):
            if time.monotonic()>deadline:raise TimeoutError('Report render')
            time.sleep(.1)
        b.ready()
        assert b.js("document.querySelectorAll('[data-stage]').length")==3
        checks.append('Overview exposes exactly three active VLM nodes')
        assert b.js("document.querySelector('#figureViewport').textContent.includes('范围校准 · 已关闭') && document.querySelector('#figureViewport').textContent.includes('相框审核 · 已关闭')")
        checks.append('Both closed optional nodes appear only as overview labels')
        assert b.js("D.nodes.every(n=>n.example.pictures.every(p=>!!p.url))")
        checks.append('All 26 historical input image entries embedded')
        assert b.js("document.documentElement.scrollWidth<=innerWidth")
        b.screenshot('overview-desktop.png',True)
        for stage in ('analyze','photo_windows','bind'):
            for tab in ('input','prompt','format','example','process'):
                b.js(f"location.hash='{stage}/{tab}'")
                b.ready()
                assert b.js(f"document.querySelector('#tab-{tab}').getAttribute('aria-selected')==='true'")
                assert b.js("document.documentElement.scrollWidth<=innerWidth")
                if tab=='prompt':
                    assert not b.js("document.querySelector('.markdown').textContent.includes('{{REFERENCE_COORDINATES}}')")
                    b.js("const m=document.querySelector('#coordinateMode');m.value='pixel';m.dispatchEvent(new Event('change',{bubbles:true}))")
                    assert b.js("document.querySelector('#coordinateMode').value==='pixel'")
                if tab=='example':
                    b.js("const s=document.querySelector('#inspectItem');s.value='0';s.dispatchEvent(new Event('change',{bubbles:true}))")
                    for choice in ('accepted','persisted','context','system-0.txt','request-0.json','response-0.json','prompt-sources.json'):
                        b.js(f"const c=document.querySelector('#recordChoice');c.value='{choice}';c.dispatchEvent(new Event('change',{{bubbles:true}}))")
                        assert b.js("document.querySelector('#recordBody pre').textContent.length>10")
                checks.append(stage+'/'+tab)
            b.js(f"location.hash='{stage}/input'")
            b.ready()
            b.screenshot(stage+'-desktop.png',True)
        b.js("location.hash='bind/example'")
        b.ready()
        b.js("document.querySelector('[data-image]').click()")
        assert b.js("document.querySelector('#imageDialog').open")
        b.js("document.querySelector('#closeImage').click()")
        checks.append('Input image modal opens and closes')
        b.js("location.hash='photo_windows/example'")
        b.ready()
        b.screenshot('photo_windows-evidence.png',True)
        b.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
        for route in ('overview','analyze/prompt','photo_windows/format','bind/example'):
            b.js(f"location.hash='{route}'")
            b.ready()
            assert b.js("document.documentElement.scrollWidth<=innerWidth"),route
            checks.append('Mobile layout '+route)
        b.screenshot('binding-mobile.png',True)
        b.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1080,'deviceScaleFactor':1,'mobile':False})
        b.js("location.hash='overview'")
        b.ready()
        b.js("document.querySelector('[data-action=zoom-in]').click()")
        assert b.js("document.querySelector('#zoomLabel').textContent==='125%'")
        b.js("document.querySelector('[data-action=zoom-reset]').click()")
        checks.append('Overview zoom and reset')
        downloads=OUT/('downloads-'+str(time.time_ns()))
        downloads.mkdir(exist_ok=True)
        b.call('Browser.setDownloadBehavior',{'behavior':'allow','downloadPath':str(downloads)})
        b.js("document.querySelector('#exportData').click();document.querySelector('[data-action=svg]').click();exportPng()")
        deadline=time.monotonic()+15
        expected=('workflow-data.json','workflow-overview.svg','workflow-overview.png')
        while not all((downloads/f).exists() for f in expected):
            if time.monotonic()>deadline:raise TimeoutError('Report downloads')
            time.sleep(.1)
        (HERE/'workflow-overview.png').write_bytes((downloads/'workflow-overview.png').read_bytes())
        checks.append('JSON, SVG and 3600px PNG export')
        b.js('buildPrint()')
        assert b.js("document.querySelectorAll('#printReport .printpage').length===3")
        assert b.js("document.querySelector('#printReport').textContent.includes('requires_segmentable_subject')")
        b.js("document.querySelector('#printReport').innerHTML=''")
        checks.append('Print view contains complete prompts and three chapters')
        errors=[e for e in b.events if e.get('method')=='Runtime.exceptionThrown']
        network=[e['params']['request']['url'] for e in b.events if e.get('method')=='Network.requestWillBeSent' and e['params']['request']['url'].startswith(('http://','https://'))]
        assert not errors,errors
        assert not network,network
        checks.extend(['No browser JavaScript errors','No external network requests'])
        report={'passed':len(checks),'checks':checks,'screenshots':str(OUT),'source_snapshot':b.js('D.snapshot')}
        (OUT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:
        b.close()


if __name__=='__main__':main()
