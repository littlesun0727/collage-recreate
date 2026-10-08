"""Observe gated real renderer transitions in a browser with synthetic pictures."""
import argparse
import json
import subprocess
import time
from pathlib import Path
from browser_check import Browser


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--python',default='D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe');args=p.parse_args()
    args.out.mkdir(parents=True,exist_ok=True)
    runroot=args.out/('fixture-'+str(time.time_ns()))
    repo=Path(__file__).resolve().parents[2]
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    server=subprocess.Popen([args.python,'-B','-m','workbench','--runs',str(runroot/'tasks'),'--port','8791'],cwd=repo,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
    driver=subprocess.Popen([args.python,'-B',str(Path(__file__).with_name('live_driver.py')),str(runroot)],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,creationflags=flags)
    browser=None
    def phase(name):
        for _ in range(150):
            try:
                if json.loads((runroot/'phase.json').read_text())['phase']==name:return
            except (OSError,ValueError):pass
            if driver.poll() is not None:raise RuntimeError(driver.stderr.read())
            time.sleep(.1)
        raise AssertionError('Driver stalled at '+name)
    def release():driver.stdin.write('continue\n');driver.stdin.flush()
    try:
        phase('photo');browser=Browser(args.out)
        browser.call('Emulation.setDeviceMetricsOverride',{'width':1512,'height':1080,'deviceScaleFactor':1,'mobile':False})
        browser.call('Page.navigate',{'url':'http://127.0.0.1:8791'})
        browser.until('Boolean(state.data && state.data.live)')
        assert browser.js('state.data.versions.length')==0
        assert browser.js('state.data.materials.find(m=>m.id==="background").status')=='complete'
        assert browser.js('state.data.materials.find(m=>m.id==="photo").status')=='running'
        release();phase('star')
        browser.until('state.data.materials.find(m=>m.id==="photo").status==="complete"')
        assert browser.js('state.data.versions.length')==0
        browser.js('document.querySelector("#materials").scrollIntoView({block:"center"});document.querySelector(".material details").open=true')
        browser.screenshot(args.out/'01-material-before-final.png')
        browser.js('document.querySelector(\'[data-stage="2"]\').click()')
        release();phase('first')
        browser.until('state.data.versions.length===1')
        assert browser.js('state.stage')==2
        browser.js('document.querySelector("#follow").click();document.querySelector(".version").click()')
        old=browser.js('state.version')
        release();phase('finished')
        browser.until('state.data.versions.length===2 && state.data.current_stage===6')
        assert browser.js('state.version')==old
        assert browser.js('state.data.versions[0].review') is None
        assert browser.js('state.data.versions[1].review.verdict')=='pass'
        browser.js('document.querySelector("#follow").click();document.querySelector("[data-mode=before]").click();window.scrollTo(0,0)')
        browser.until('Array.from(document.querySelectorAll("#canvas img")).every(i=>i.complete && i.naturalWidth>0)')
        browser.screenshot(args.out/'02-modification-chain.png')
        assert not browser.errors,browser.errors
        report={'passed':True,'fixture':True,'checks':['material-before-final','live-heartbeat','current-attempt-count','historical-step-during-build','pinned-version-during-new-render','review-bound-to-current-version','before-after'],'console_errors':browser.errors}
        (args.out/'live-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    finally:
        if browser:browser.close()
        for proc in (driver,server):
            if proc.poll() is None:proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill()


if __name__=='__main__':main()
