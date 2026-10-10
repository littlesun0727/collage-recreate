"""Streaming ordinary Live windows preserves native frames and bounded memory."""
from copy import deepcopy
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from test_workbench import run
from test_motion import clip,live_run
from common import read,save,sha
from live_media import inspect_video,decode_needed,NativeFrameReader
from motion_render import execute


def test_stream_matches_png_pixels_across_vfr_loops_and_skips(tmp_path):
    path=tmp_path/'clip.mp4';clip(path)
    video=inspect_video(path);cached=decode_needed(video,set(range(4)),tmp_path/'cache')
    with NativeFrameReader(video) as reader:
        for index in [0,0,2,3,0,1,3,0]:
            actual=reader.get(index)
            with Image.open(cached[index]['file']) as expected:
                assert np.array_equal(np.asarray(actual),np.asarray(expected.convert('RGBA')))
            assert reader.image is actual
        assert reader.converted_frames==7
    assert reader.container is None and reader.image is None


def test_stream_rejects_bad_identity_and_closes(tmp_path):
    path=tmp_path/'clip.mp4';clip(path);video=inspect_video(path)
    invalid=deepcopy(video);invalid['frames'][1]['pts']+=1
    reader=NativeFrameReader(invalid)
    with pytest.raises(ValueError,match='metadata'):
        with reader:reader.get(1)
    assert reader.container is None
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='Source changed'):NativeFrameReader(video)


def test_streaming_output_matches_legacy_disk_reader_without_frame_cache(live_run,monkeypatch,tmp_path):
    import motion_render
    source_before={name:sha(live_run/name) for name in ['scene.json','result.json','final.png']}
    streamed=execute(live_run,'motion-stream-test')
    assert streamed['cached_source_frames']==0 and streamed['streamed_source_frames']==4
    assert not (live_run/'chat/motion/cache/frames').exists()
    assert streamed['timing']['stages']['decode']['skipped']
    class DiskReader:
        def __init__(self,video):
            self.files=decode_needed(video,set(range(video['frame_count'])),tmp_path/'legacy')
            self.decoded_frames=video['frame_count'];self.elapsed_seconds=0
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,index):
            with Image.open(self.files[index]['file']) as im:return im.convert('RGBA')
    monkeypatch.setattr(motion_render,'NativeFrameReader',DiskReader)
    legacy=execute(live_run,'motion-legacy-test')
    assert legacy['frame_count']==streamed['frame_count'] and legacy['duration_us']==streamed['duration_us']
    # Both feeds enter the identical encoder; timing and every encoded pixel must match.
    import av
    with av.open(streamed['video']['file']) as left,av.open(legacy['video']['file']) as right:
        a=list(left.decode(video=0));b=list(right.decode(video=0))
        assert len(a)==len(b)
        for x,y in zip(a,b):
            assert x.pts*x.time_base==y.pts*y.time_base
            assert np.array_equal(x.to_ndarray(format='rgb24'),y.to_ndarray(format='rgb24'))
    assert source_before=={name:sha(live_run/name) for name in source_before}


def test_encode_failure_closes_all_readers(live_run,monkeypatch):
    import motion_render
    readers=[]
    class TrackedReader(NativeFrameReader):
        def __init__(self,video):super().__init__(video);readers.append(self)
    def fail(*args,**kwargs):
        next(args[4]);raise RuntimeError('encoder failed')
    monkeypatch.setattr(motion_render,'NativeFrameReader',TrackedReader)
    monkeypatch.setattr(motion_render,'encode_frames',fail)
    with pytest.raises(RuntimeError,match='encoder failed'):execute(live_run,'motion-error-test')
    assert readers and all(r.container is None and r.image is None for r in readers)


def test_mixed_cutout_caches_only_matte_source(live_run,monkeypatch):
    import photos
    import motion_render
    from scene import compile_scene
    from render import render
    from PIL import ImageDraw
    model=live_run/'fake-model.onnx';model.write_bytes(b'model')
    inp=read(live_run/'input.json');inp['cutout_model']=str(model);save(live_run/'input.json',inp)
    analysis=read(live_run/'analysis.json');analysis['objects'][1].update(mode='cutout',bbox=[10,40,90,260])
    analysis['objects'].append({'id':'ordinary','kind':'photo','label':'Other video','description':'Other video','bbox':[100,40,180,260]})
    analysis['layer_order'].append('ordinary');save(live_run/'analysis.json',analysis)
    manifest=read(live_run/'media/manifest.json');bindings=read(live_run/'bindings.json')
    other=next(v for v in manifest['videos'] if v['asset_id']!=bindings['photos'][0]['asset_id'])
    bindings['photos'].append({'slot_id':'ordinary','asset_id':other['asset_id'],'reason':'Mixed source test'})
    save(live_run/'bindings.json',bindings);calls=[]
    def matte(image,source,*args):
        calls.append(source['sha256']);alpha=Image.new('L',image.size)
        ImageDraw.Draw(alpha).rectangle((20,20,100,140),fill=255)
        out=image.copy();out.putalpha(alpha);return out
    monkeypatch.setattr(photos,'cutout',matte);monkeypatch.setattr(motion_render,'cutout',matte)
    compile_scene(live_run);render(live_run);calls.clear()
    result=execute(live_run,'motion-mixed-test')
    assert result['cached_source_frames']==result['matte_frames']==len(calls)==4
    assert result['streamed_source_frames']==4
    assert not (live_run/'chat/motion/cache/frames'/other['sha256']).exists()
    assert result['unique_source_frames']==8
