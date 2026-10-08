"""Verify the shipped UI in headless Chromium; requires websocket-client for tests only."""
import argparse
import base64
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket


class Browser:
    def __init__(self, output):
        profile=output/('browser-'+str(time.time_ns()))
        executable=os.environ.get('COLLAGE_BROWSER','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')
        self.process=subprocess.Popen([executable,'--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--remote-debugging-port=0','--user-data-dir='+str(profile),'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        for _ in range(150):
            try:
                port=int((profile/'DevToolsActivePort').read_text().splitlines()[0]);break
            except (OSError,ValueError):time.sleep(.1)
        else:
            self.process.terminate();raise RuntimeError('Browser startup failed')
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json',timeout=5) as r:
            target=next(t for t in json.load(r) if t['type']=='page')
        self.socket=websocket.create_connection(target['webSocketDebuggerUrl'],timeout=20,suppress_origin=True)
        self.counter=0;self.errors=[]
        try:
            self.call('Runtime.enable');self.call('Page.enable')
        except Exception:
            self.close();raise

    def call(self,method,params=None):
        self.counter+=1
        self.socket.send(json.dumps({'id':self.counter,'method':method,'params':params or {}}))
        while True:
            message=json.loads(self.socket.recv())
            if message.get('method')=='Runtime.exceptionThrown':self.errors.append(message['params']['exceptionDetails'])
            if message.get('id')==self.counter:
                if 'error' in message:raise RuntimeError(message['error'])
                return message.get('result',{})

    def js(self,expression):
        result=self.call('Runtime.evaluate',{'expression':expression,'returnByValue':True,'awaitPromise':True})
        if result.get('exceptionDetails'):raise RuntimeError(result['exceptionDetails'])
        return result.get('result',{}).get('value')

    def until(self,expression):
        for _ in range(100):
            if self.js('typeof state !== "undefined" && ('+expression+')'):return
            time.sleep(.15)
        raise AssertionError('Timed out: '+expression)

    def screenshot(self,path):
        result=self.call('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})
        path.write_bytes(base64.b64decode(result['data']))

    def close(self):
        self.socket.close();self.process.terminate()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:self.process.kill()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8790');parser.add_argument('--out',type=Path,required=True);parser.add_argument('--final',action='store_true')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    b=Browser(args.out)
    try:
        b.call('Emulation.setDeviceMetricsOverride',{'width':1512,'height':1080,'deviceScaleFactor':1,'mobile':False})
        b.call('Page.navigate',{'url':args.url});b.until('Boolean(state.data && !state.busy)')
        assert b.js('document.querySelectorAll(".stage").length')==6
        records=[]
        for task in b.js('state.tasks.map(t=>t.id)'):
            b.js('selectTask('+json.dumps(task)+')');b.until('state.data?.id === '+json.dumps(task)+' && !state.busy')
            records.append(b.js('({id:state.data.id,name:state.data.name,versions:state.data.versions.length,stage:state.data.current_stage})'))
            assert b.js('document.querySelector("#sdkTiming").hidden === !state.data.sdk_timing?.started_at')
            if b.js('Boolean(state.data.sdk_timing?.started_at)'):
                assert b.js('document.querySelector("#sdkTiming").textContent.includes(state.data.sdk_timing.model)')
            assert b.js('document.querySelectorAll(".material").length')==b.js('state.data.materials.length')
            b.js('document.querySelector(\'[data-stage="2"]\').click();refresh()');b.until('!state.busy')
            assert b.js('state.stage')==2
            b.js('document.querySelector("#follow").click()');assert b.js('state.stage') is None
        target=max(records,key=lambda task:task['versions'])['id']
        b.js('selectTask('+json.dumps(target)+')');b.until('state.data?.id === '+json.dumps(target)+' && !state.busy')
        b.until('Array.from(document.querySelectorAll("#canvas img")).every(i=>i.complete && i.naturalWidth>0)')
        b.screenshot(args.out/'01-workbench.png')
        if b.js('state.data.versions.length'):
            b.js('document.querySelector("[data-mode=wipe]").click()');b.until('Boolean(document.querySelector(".wipe-slider"))')
            b.js('document.querySelector(".wipe-slider").value=70;document.querySelector(".wipe-slider").dispatchEvent(new Event("input",{bubbles:true}))')
            assert '30%' in b.js('document.querySelector(".wipe-top").style.clipPath')
            b.js('document.querySelector("#zoomIn").click()');assert b.js('state.zoom')==1.25
            b.js('document.querySelector("#resetZoom").click();document.querySelector(".version").click()')
            pinned=b.js('state.version');b.js('refresh()');b.until('!state.busy');assert b.js('state.version')==pinned
            b.screenshot(args.out/'02-version-history.png')
            b.js('document.querySelector("#original").click();refresh()');b.until('!state.busy')
            assert b.js('document.querySelector("#imageDialog").open')
            b.js('document.querySelector("#closeImage").click();document.querySelector("#follow").click();document.querySelector("[data-mode=side]").click()')
            b.js('document.querySelector(".material details").open=true;refresh()');b.until('!state.busy')
            assert b.js('document.querySelector(".material details").open')
            b.js('document.querySelector(".material-title button").click()');assert b.js('document.querySelectorAll(".highlight").length')>0
            b.js('document.querySelector("#resetZoom").click();window.scrollTo(0,0)')
        if args.final:
            assert len(records)>=3 and all(r['versions']>0 and r['stage']==6 for r in records)
            assert any(r['versions']>1 for r in records)
        b.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
        b.screenshot(args.out/'03-mobile.png')
        assert b.js('document.documentElement.scrollWidth <= window.innerWidth+1'),'Mobile overflow'
        b.call('Page.reload');b.until('Boolean(state.data && !state.busy)')
        assert not b.errors,b.errors
        report={'passed':True,'final':args.final,'tasks':records,'checks':['six-stages','all-tasks','sdk-timing','history-pinning','wipe','zoom','version-pinning','modal-persistence','material-details','object-focus','responsive','reload'],'console_errors':b.errors}
        (args.out/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':True,'out':str(args.out)}))
    finally:b.close()


if __name__=='__main__':main()
