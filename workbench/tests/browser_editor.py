"""Exercise canvas controls against a disposable synthetic task, never user tasks."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
from browser_check import Browser


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--python',default='D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    root=args.out/('fixture-'+str(time.time_ns()));root.mkdir()
    repo=Path(__file__).resolve().parents[2];flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    code="""import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'workbench/tests'))
from test_workbench import run,build,read,save
p=run.__wrapped__(Path(sys.argv[1]))
a=read(p/'analysis.json');a['objects'][-1]['photo_id']='photo'
a['objects'].append({'id':'badge','kind':'overlay','bbox':[20,100,80,160],'label':'Badge','description':'Badge','method':'local','style':{'shape':'ellipse','fill':'#3344aa'}})
a['layer_order'].append('badge');save(p/'analysis.json',a)
build(p)
"""
    subprocess.run([args.python,'-B','-c',code,str(root)],cwd=repo,check=True,stdout=subprocess.DEVNULL,creationflags=flags)
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    server=subprocess.Popen([args.python,'-B','-m','workbench','--runs',str(root/'tasks'),'--port',str(port),'--enable-editor'],cwd=repo,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
    browser=None
    try:
        for _ in range(100):
            try:
                with urllib.request.urlopen(url+'/api/chat-config',timeout=1):break
            except OSError:time.sleep(.1)
        browser=Browser(args.out);b=browser
        b.call('Emulation.setDeviceMetricsOverride',{'width':1512,'height':1080,'deviceScaleFactor':1,'mobile':False})
        b.call('Page.navigate',{'url':url});b.until('Boolean(state.data && !state.busy)')
        b.js("document.querySelector('#openEditor').click()")
        b.until("typeof editor!=='undefined' && editor.data && !editor.busy")
        assert b.js('editor.objects.length')==4
        b.js("document.querySelector('#editorObjects').value='photo';document.querySelector('#editorObjects').dispatchEvent(new Event('change'))")
        b.js("document.querySelector('#editorLayerUp').click()")
        assert b.js('editor.objects.map(o=>o.id)')==['background','badge','photo','star']
        assert b.js('editChanges().length')==0 and not b.js("document.querySelector('#editorSave').disabled")
        assert b.js("Array.from(document.querySelectorAll('#editorSvg image')).map(n=>n.dataset.object)")==['background','badge','photo','star']
        b.js("document.querySelector('#editorUndo').click()")
        assert b.js('editor.objects.map(o=>o.id)')==['background','photo','star','badge']
        b.js("document.querySelector('#editorRedo').click();document.querySelector('#editorSave').click()")
        b.until('!editor.busy && state.data.versions.length===2')
        assert b.js('editor.objects.map(o=>o.id)')==['background','badge','photo','star']
        b.js("document.querySelector('#editorObjects').value='photo';document.querySelector('#editorObjects').dispatchEvent(new Event('change'))")
        assert b.js("document.querySelector('#editorTransformScope').value")=='object'
        b.js("document.querySelector('#editorAngle').value=15;document.querySelector('#editorAngle').dispatchEvent(new Event('change'))")
        assert b.js("editor.objects.find(o=>o.id==='star').transform.rotation")==0
        assert b.js("editChanges().map(o=>[o.id,o.scope])")==[['photo','object']]
        b.js("document.querySelector('#editorPreview').click()")
        b.until('!editor.busy && !document.querySelector("#editorResult").hidden')
        assert '未完成' not in b.js("document.querySelector('#editorStatus').textContent")
        b.js("document.querySelector('#editorUndo').click()")
        b.js("document.querySelector('#editorTransformScope').value='group';document.querySelector('#editorTransformScope').dispatchEvent(new Event('change'))")
        b.js("document.querySelector('#editorAngle').value=30;document.querySelector('#editorAngle').dispatchEvent(new Event('change'));document.querySelector('#editorScale').value=75;document.querySelector('#editorScale').dispatchEvent(new Event('change'))")
        assert b.js("editor.objects.find(o=>o.id==='photo').transform.rotation")==30
        assert b.js("editor.objects.find(o=>o.id==='star').transform.scale")==.75
        assert b.js("document.querySelectorAll('.editor-handle').length")==2
        # Use real pointer dispatch to drag the resize handle.
        point=b.js("(()=>{const r=document.querySelector('[data-handle=scale]').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
        b.screenshot(args.out/'before-handle.png')
        b.call('Input.dispatchMouseEvent',{'type':'mousePressed',**point,'button':'left','clickCount':1})
        b.call('Input.dispatchMouseEvent',{'type':'mouseMoved','x':point['x']+12,'y':point['y']+12,'button':'left','buttons':1})
        b.call('Input.dispatchMouseEvent',{'type':'mouseReleased','x':point['x']+12,'y':point['y']+12,'button':'left','clickCount':1})
        assert b.js("editor.objects.find(o=>o.id==='photo').transform.scale")>.75,b.js('({point:'+json.dumps(point)+',drag:editDrag,objects:editor.objects,status:document.querySelector("#editorStatus").textContent})')
        b.js("document.querySelector('#editorDeleteGroup').click()")
        assert b.js('editor.objects.filter(o=>o.removed).length')==2
        b.js("document.querySelector('#editorUndo').click()")
        assert b.js('editor.objects.filter(o=>o.removed).length')==0
        b.js("document.querySelector('#editorObjects').value='star';document.querySelector('#editorObjects').dispatchEvent(new Event('change'));document.querySelector('#editorDelete').click()")
        assert b.js('editor.objects.filter(o=>o.removed).length')==1
        b.js("document.querySelector('#editorPreview').click()")
        b.until('!editor.busy && !document.querySelector("#editorResult").hidden')
        # Preview must retain the original layer for undo, even after deletion.
        b.js("document.querySelector('#editorUndo').click()")
        assert b.js("document.querySelector('image[data-object=star]').style.display")!='none'
        b.js("document.querySelector('#editorRedo').click()")
        b.screenshot(args.out/'canvas-desktop.png')
        b.call('Page.reload');b.until('Boolean(state.data && !state.busy)')
        b.js("document.querySelector('#openEditor').click()")
        b.until("editor.data && !editor.busy")
        assert b.js('editor.objects.filter(o=>o.removed).length')==1
        assert b.js("editor.objects.find(o=>o.id==='photo').transform.rotation")==30
        b.js("document.querySelector('#editorSave').click()")
        b.until('!editor.busy && state.data.versions.length===3')
        assert b.js('editor.objects.map(o=>o.id)')==['background','badge','photo']
        assert b.js("editor.objects.find(o=>o.id==='photo').transform.rotation")==30
        b.js("document.querySelector('#editorObjects').value='photo';document.querySelector('#editorObjects').dispatchEvent(new Event('change'));document.querySelector('#editorCrop').click()")
        b.until("document.querySelector('#editorCropDialog').open && !document.querySelector('#editorCropApply').disabled")
        b.screenshot(args.out/'crop-before-drag.png')
        point=b.js("(()=>{const r=document.querySelector('[data-crop=se]').getBoundingClientRect();return {x:r.x+r.width/2-3,y:r.y+r.height/2-3}})()")
        b.call('Input.dispatchMouseEvent',{'type':'mousePressed',**point,'button':'left','clickCount':1})
        b.call('Input.dispatchMouseEvent',{'type':'mouseMoved','x':point['x']-45,'y':point['y']-55,'button':'left','buttons':1})
        b.call('Input.dispatchMouseEvent',{'type':'mouseReleased','x':point['x']-45,'y':point['y']-55,'button':'left','clickCount':1})
        crop=b.js('photoCrop.rect');assert crop[2]<.99 and crop[3]<.99,crop
        b.screenshot(args.out/'crop-dialog.png')
        b.js("document.querySelector('#editorCropApply').click()")
        b.until("!editor.busy && !document.querySelector('#editorCropDialog').open")
        assert '未完成' not in b.js("document.querySelector('#editorStatus').textContent")
        expected=b.js("editor.objects.find(o=>o.id==='photo').source_crop")
        b.js("document.querySelector('#editorUndo').click()")
        assert b.js("editor.objects.find(o=>o.id==='photo').source_crop")==[0,0,1,1]
        b.js("document.querySelector('#editorRedo').click()")
        b.call('Page.reload');b.until('Boolean(state.data && !state.busy)')
        b.js("document.querySelector('#openEditor').click()")
        b.until("editor.data && !editor.busy && !document.querySelector('#editorResult').hidden")
        assert b.js("editor.objects.find(o=>o.id==='photo').source_crop")==expected
        b.js("document.querySelector('#editorSave').click()")
        b.until('!editor.busy && state.data.versions.length===4')
        assert b.js("editor.objects.find(o=>o.id==='photo').source_crop")==expected
        b.js("document.querySelector('#editorObjects').value='photo';document.querySelector('#editorObjects').dispatchEvent(new Event('change'));document.querySelector('#editorCrop').click()")
        b.until("document.querySelector('#editorCropDialog').open && !document.querySelector('#editorCropApply').disabled")
        b.js("document.querySelector('#editorCropReset').click()")
        assert b.js('photoCrop.rect')==[0,0,1,1]
        b.js("document.querySelector('#editorCropCancel').click()")
        assert b.js("editor.objects.find(o=>o.id==='photo').source_crop")==expected
        b.js("document.querySelector('#editorWindowCrop').click()")
        before=b.js("copyEdit(editor.objects.find(o=>o.id==='photo'))")
        point=b.js("(()=>{const r=document.querySelector('[data-window-crop=s]').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
        b.call('Input.dispatchMouseEvent',{'type':'mousePressed',**point,'button':'left','clickCount':1})
        b.call('Input.dispatchMouseEvent',{'type':'mouseMoved','x':point['x'],'y':point['y']-45,'button':'left','buttons':1})
        b.call('Input.dispatchMouseEvent',{'type':'mouseReleased','x':point['x'],'y':point['y']-45,'button':'left','clickCount':1})
        after=b.js("copyEdit(editor.objects.find(o=>o.id==='photo'))")
        assert after['window_crop'][3]<.99 and after['transform']==before['transform'] and after['source_crop']==before['source_crop']
        b.js("document.querySelector('#editorSave').click()")
        b.until('!editor.busy && state.data.versions.length===5')
        assert b.js("editor.objects.find(o=>o.id==='photo').window_crop")==after['window_crop']
        b.js("document.querySelector('#editorObjects').value='photo';document.querySelector('#editorObjects').dispatchEvent(new Event('change'));document.querySelector('#editorWindowReset').click()")
        assert b.js("editor.objects.find(o=>o.id==='photo').window_crop")==[0,0,1,1]
        b.js("document.querySelector('#editorUndo').click()")
        assert b.js("editor.objects.find(o=>o.id==='photo').window_crop")==after['window_crop']
        b.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
        b.screenshot(args.out/'canvas-mobile.png')
        assert b.js('document.documentElement.scrollWidth<=window.innerWidth+1')
        assert not b.errors,b.errors
        report={'passed':True,'fixture':str(root),'checks':['layer-order-only-save','layer-order-undo','rotation','scale','pointer-handle','linked-group','delete','undo-redo','preview-undo','draft-reload','save-version','crop-pointer','crop-preview','crop-undo-redo','crop-draft-reload','crop-save','crop-reset-cancel','mobile'],'console_errors':b.errors}
        (args.out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report))
    finally:
        if browser:browser.close()
        server.terminate();server.wait(timeout=10)


if __name__=='__main__':main()
