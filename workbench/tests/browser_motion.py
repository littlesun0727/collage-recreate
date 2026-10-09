"""Real browser playback/seek, progress, version association and optional mixed upload."""
import argparse
import json
from pathlib import Path
import time
from browser_check import Browser


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8793')
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--upload',action='store_true')
    parser.add_argument('--reference');parser.add_argument('--video');parser.add_argument('--photo')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    b=Browser(args.out);checks=[]
    try:
        b.call('Network.enable')
        b.call('Emulation.setDeviceMetricsOverride',{'width':1512,'height':1080,'deviceScaleFactor':1,'mobile':False})
        b.call('Page.navigate',{'url':args.url});b.until('Boolean(state.data&&!state.busy&&live.config)')
        assert b.js('!document.querySelector("#openUpload").hidden')
        task=b.js('state.tasks.find(t=>t.name.startsWith("07-"))?.id');assert task
        b.js('selectTask('+json.dumps(task)+')')
        b.until('live.task==='+json.dumps(task)+' && live.data?.versions.length>0')
        b.until('document.querySelector("#motionVideo").readyState>=2')
        assert b.js('document.querySelectorAll(".stage").length')==6
        assert b.js('document.querySelectorAll("#liveMaterials video").length')==18
        assert b.js('document.querySelector("#motionVideo").duration')<=3.000001
        playback=b.js('''(async()=>{const v=document.querySelector('#motionVideo');v.loop=false;v.currentTime=0;await v.play();await new Promise(r=>setTimeout(r,900));v.pause();return {time:v.currentTime,frames:v.getVideoPlaybackQuality().totalVideoFrames,error:v.error};})()''')
        assert playback['time']>.3 and playback['frames']>3 and playback['error'] is None,playback
        b.js('document.querySelector("#motionVideo").currentTime=2.7')
        b.until('Math.abs(document.querySelector("#motionVideo").currentTime-2.7)<.05 && !document.querySelector("#motionVideo").seeking')
        b.js('refresh()');time.sleep(2.5)
        assert abs(b.js('document.querySelector("#motionVideo").currentTime')-2.7)<.05
        assert b.js('document.querySelector("#motionCaption").textContent.includes("封面第 1 版")')
        b.js('document.querySelector("#motionPanel").scrollIntoView()');b.screenshot(args.out/'01-live-playback.png')
        checks.extend(['native-MP4-playback','seek-and-range','poll-preserves-playhead','cover-association','18-source-videos','six-original-stages'])
        active=b.js('state.tasks.find(t=>t.name.startsWith("10-"))?.id')
        if active:
            b.js('selectTask('+json.dumps(active)+')');b.until('live.task==='+json.dumps(active)+' && live.data?.jobs.length>0')
            b.js('document.querySelector("#motionPanel").scrollIntoView()');b.screenshot(args.out/'02-live-cutout-progress.png')
            checks.append('native-matte-count-and-timing-visible')
        if args.upload:
            b.js('document.querySelector("#openUpload").click()')
            document=b.call('DOM.getDocument')['root']['nodeId']
            reference=b.call('DOM.querySelector',{'nodeId':document,'selector':'#uploadReference'})['nodeId']
            materials=b.call('DOM.querySelector',{'nodeId':document,'selector':'#uploadMaterials'})['nodeId']
            reference_file=args.reference or 'D:/datas/图片排版样图_去水印/拼贴3.jpg'
            photo=args.photo or str(next(Path('D:/视频素材/风景照片').rglob('*.jpg')))
            video=args.video or 'D:/datas/live动图素材/20261009-105038.mp4'
            b.call('DOM.setFileInputFiles',{'nodeId':reference,'files':[reference_file]})
            b.call('DOM.setFileInputFiles',{'nodeId':materials,'files':[photo,video]})
            b.js('document.querySelector("#uploadSubmit").click()')
            for _ in range(90):
                if b.js('!live.upload && !document.querySelector("#uploadHandoff").hidden'):break
                if b.js('!live.upload && document.querySelector("#uploadStatus").textContent.length>0'):
                    raise AssertionError({'status':b.js('document.querySelector("#uploadStatus").textContent'),'network':b.network_failures})
                time.sleep(1)
            else:raise AssertionError(b.js('document.querySelector("#uploadStatus").textContent'))
            assert b.js('document.querySelector("#uploadHandoffText").value.includes("prepare-live")')
            b.screenshot(args.out/'03-upload-complete.png')
            b.js('document.querySelector("#uploadOpenTask").click()');b.until('state.data?.name.startsWith("Live-")')
            assert b.js('state.data.versions.length')==0
            checks.append('real-browser-mixed-upload-and-honest-agent-handoff')
        b.call('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':True})
        assert b.js('document.documentElement.scrollWidth<=window.innerWidth+1')
        b.screenshot(args.out/'04-mobile.png')
        checks.append('mobile-no-overflow')
        assert not b.errors,b.errors
        report={'passed':True,'checks':checks,'playback':playback,'console_errors':b.errors}
        (args.out/'browser-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report))
    finally:b.close()


if __name__=='__main__':main()
