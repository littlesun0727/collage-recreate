"""Verify all portable pages offline and render the two new flowcharts."""
from pathlib import Path
import base64
import json
import time
import xml.etree.ElementTree as ET
from verify_atlas import Browser, OUT

HERE=Path(__file__).resolve().parent


def main(root=HERE):
    root=Path(root).resolve()
    b=Browser()
    checks=[]
    try:
        b.call('Network.emulateNetworkConditions',{'offline':True,'latency':0,'downloadThroughput':0,'uploadThroughput':0})
        b.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1080,'deviceScaleFactor':1,'mobile':False})
        for name in ('start','collection','decomposition','index'):
            b.call('Page.navigate',{'url':(root/(name+'.html')).as_uri()})
            deadline=time.monotonic()+20
            while not b.js("document.querySelector('h1')?.textContent"):
                if time.monotonic()>deadline:raise TimeoutError(name)
                time.sleep(.1)
            b.ready()
            assert b.js('document.documentElement.scrollWidth<=innerWidth'),name+' desktop overflow'
            broken=b.js("[...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')).filter(x=>x&&!x.startsWith('#')&&!x.startsWith('blob:'))")
            for target in broken:
                assert '://' not in target,(name,target)
                assert (root/target.split('#')[0]).exists(),(name,target)
            if name in ('collection','decomposition'):
                assert b.js("document.querySelectorAll('.viewport svg a').length")==6
                b.js("document.querySelector('[data-zoom=\".25\"]').click()")
                assert b.js("document.querySelector('#zoom').textContent")=='125%'
                b.js("document.querySelector('[data-zoom=\"reset\"]').click()")
                for ident in b.js("[...document.querySelectorAll('.viewport svg a')].map(x=>x.getAttribute('href').slice(1))"):
                    assert b.js(f"!!document.getElementById({json.dumps(ident)})")
                b.js("document.querySelector('.viewport svg a').dispatchEvent(new MouseEvent('click',{bubbles:true}))")
                b.ready()
                assert b.js('location.hash') in ('#search','#input')
                b.js("location.hash='';window.scrollTo(0,0)")
                b.ready()
            if name=='index':
                assert b.js("document.querySelectorAll('[data-stage]').length")==3
                for section in ('analyze','photo_windows','bind'):
                    b.js(f"location.hash='{section}'")
                    b.ready()
                    assert b.js("document.querySelector('#content h1')?.textContent")
                b.js("location.hash='overview'")
                b.ready()
            b.screenshot('portable-'+name+'.png')
            b.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':False})
            b.ready()
            assert b.js('document.documentElement.scrollWidth<=innerWidth'),name+' mobile overflow'
            b.call('Emulation.setDeviceMetricsOverride',{'width':1440,'height':1080,'deviceScaleFactor':1,'mobile':False})
            checks.append(name+': file:// offline, links, desktop/mobile layout'+(', zoom and node navigation' if name in ('collection','decomposition') else ''))
        for name in ('collection','decomposition'):
            svg=root/(name+'-workflow.svg')
            tree=ET.parse(svg)
            assert len(tree.findall('.//{http://www.w3.org/2000/svg}a'))==6
            b.call('Emulation.setDeviceMetricsOverride',{'width':1800,'height':1100,'deviceScaleFactor':2,'mobile':False})
            b.call('Page.navigate',{'url':svg.as_uri()})
            deadline=time.monotonic()+15
            while not b.js("document.documentElement.tagName==='svg'"):
                if time.monotonic()>deadline:raise TimeoutError(name+' svg')
                time.sleep(.1)
            b.ready()
            shot=b.call('Page.captureScreenshot',{'format':'png','clip':{'x':0,'y':0,'width':1800,'height':1100,'scale':1}})
            (root/(name+'-workflow.png')).write_bytes(base64.b64decode(shot['data']))
            checks.append(name+': SVG parsed and PNG rendered at 3600×2200')
        remote=[x['params']['request']['url'] for x in b.events if x['method']=='Network.requestWillBeSent' and x['params']['request']['url'].startswith(('http:','https:'))]
        assert not remote,remote
        errors=[x for x in b.events if x['method']=='Runtime.exceptionThrown']
        assert not errors,errors
        checks.append('No remote resource requests; no JavaScript exceptions')
        report={'root':str(root),'checks':checks,'screenshots':str(OUT)}
        (root/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    finally:b.close()


if __name__=='__main__':
    import sys
    # Create placeholders so navigation checks can precede image rendering on first build.
    root=Path(sys.argv[1]) if len(sys.argv)>1 else HERE
    for name in ('collection','decomposition'):
        p=root/(name+'-workflow.png')
        if not p.exists():
            from PIL import Image
            Image.new('RGB',(1,1),'white').save(p)
    main(root)
