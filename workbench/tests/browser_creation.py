"""Opt-in real SDK/360 browser smoke test; --upload starts one paid production task."""
import argparse
import json
from pathlib import Path
import time
from browser_check import Browser


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8795')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--upload',action='store_true')
    parser.add_argument('--reference')
    parser.add_argument('--photo',action='append',default=[])
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    b=Browser(args.out)
    try:
        b.call('Emulation.setDeviceMetricsOverride',{'width':1512,'height':1080,'deviceScaleFactor':1,'mobile':False})
        b.call('Page.navigate',{'url':args.url})
        b.until('typeof live !== "undefined" && Boolean(live.config?.create_enabled)')
        if args.upload:
            assert args.reference and args.photo
            b.js('document.querySelector("#openUpload").click()')
            root=b.call('DOM.getDocument')['root']['nodeId']
            for selector,files in [('#uploadReference',[args.reference]),('#uploadMaterials',args.photo)]:
                node=b.call('DOM.querySelector',{'nodeId':root,'selector':selector})['nodeId']
                b.call('DOM.setFileInputFiles',{'nodeId':node,'files':files})
            assert b.js('document.querySelector("#uploadSubmit").textContent')=='上传并开始制作'
            b.js('document.querySelector("#uploadInstructions").value="使用上传的客户风景照片，保留参考图的文字和装饰，完成静态拼贴首版。";document.querySelector("#uploadSubmit").click()')
            for _ in range(90):
                if b.js('Boolean(state.data && !document.querySelector("#creationPanel").hidden)'):break
                if b.js('!live.upload && document.querySelector("#uploadDialog").open'):
                    raise AssertionError(b.js('document.querySelector("#uploadStatus").textContent'))
                time.sleep(.5)
            else:raise AssertionError('Upload did not start production')
            assert not b.js('document.querySelector("#uploadDialog").open')
            task=b.js('state.id')
            (args.out/'task.json').write_text(json.dumps({'task_id':task,'url':args.url}),encoding='utf8')
        else:
            task=json.loads((args.out/'task.json').read_text())['task_id']
            b.js('selectTask('+json.dumps(task)+')')
            b.until('state.data?.id==='+json.dumps(task)+' && !document.querySelector("#creationPanel").hidden')
        record=b.js('(async()=>({task:state.id,creation:await api(`/api/tasks/${state.id}/creation`),versions:state.data.versions.length,status:document.querySelector("#creationStatus").textContent}))()')
        assert record['creation']['job']['model']=='gpt-5.6-sol'
        if record['creation']['job']['status']=='completed':
            assert record['versions']>0
            b.until('Array.from(document.querySelectorAll("#canvas img")).every(i=>i.complete && i.naturalWidth>0)')
            record['download']=b.js('''(async()=>{const r=await fetch(document.querySelector('#download').href);
                const bytes=await r.arrayBuffer();const hash=await crypto.subtle.digest('SHA-256',bytes);
                return {ok:r.ok,type:r.headers.get('Content-Type'),bytes:bytes.byteLength,
                sha256:Array.from(new Uint8Array(hash)).map(x=>x.toString(16).padStart(2,'0')).join('')};})()''')
            assert record['download']['ok'] and record['download']['type']=='image/png' and record['download']['bytes']>100
            assert b.js('document.querySelector("#creationRetry").hidden')
        b.screenshot(args.out/('upload.png' if args.upload else 'result.png'))
        b.call('Page.reload');b.until('typeof live !== "undefined" && Boolean(state.data && live.config?.create_enabled)')
        b.js('selectTask('+json.dumps(task)+')');b.until('state.data?.id==='+json.dumps(task))
        record['refresh_restored']=True
        b.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
        assert b.js('document.documentElement.scrollWidth<=window.innerWidth+1')
        record['mobile_no_overflow']=True
        assert not b.errors,b.errors
        record['console_errors']=b.errors
        (args.out/('upload-report.json' if args.upload else 'result-report.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(record,ensure_ascii=False))
    finally:b.close()


if __name__=='__main__':main()
