"""Local BiRefNet general/base inference (rembg general epoch 244 ONNX export)."""
from functools import lru_cache
import os
from pathlib import Path
from threading import Lock
from filelock import FileLock
from tempfile import gettempdir

import numpy as np
from PIL import Image

from .timings import log

_inference = Lock()


@lru_cache(maxsize=1)
def session(path):
    import onnxruntime as ort
    if not path.is_file():
        raise FileNotFoundError(f"BiRefNet-base weights missing: {path}; set COLLAGE_BIREFNET_MODEL")
    options = ort.SessionOptions()
    options.intra_op_num_threads = min(6, os.cpu_count() or 1)
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def alpha_mask(im):
    path = Path(os.getenv("COLLAGE_BIREFNET_MODEL", Path.home()/".u2net/birefnet-general.onnx"))
    # One resident full-size model across concurrent benchmark workers.
    with _inference, FileLock(Path(gettempdir())/"collage-birefnet-base.lock"):
        engine = session(path)
        rgb = im.convert("RGB").resize((1024,1024), Image.Resampling.LANCZOS)
        x = np.asarray(rgb, dtype=np.float32)
        x /= max(float(x.max()),1)
        x -= np.array([.485,.456,.406], np.float32)
        x /= np.array([.229,.224,.225], np.float32)
        logits = engine.run(None,{engine.get_inputs()[0].name:np.ascontiguousarray(x.transpose(2,0,1)[None])})[0].squeeze()
    mask = 1 / (1 + np.exp(-np.clip(logits,-50,50)))
    span = float(mask.max()-mask.min())
    if span < .001:
        raise ValueError("BiRefNet-base returned a constant mask")
    mask = np.uint8(255*(mask-mask.min())/span)
    return Image.fromarray(mask).resize(im.size, Image.Resampling.LANCZOS)


def subject(im, folder, asset_id):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(asset_id+"-mask.png")
    if path.exists():
        with Image.open(path) as cached:
            alpha = cached.convert("L")
        if alpha.size != im.size:
            raise ValueError(f"Cached cutout dimensions changed for {asset_id}")
    else:
        keep_alpha = im.getchannel("A").getextrema()[0] < 255
        log(f"{asset_id} cutout: {'source alpha' if keep_alpha else 'BiRefNet-base'}")
        alpha = im.getchannel("A") if keep_alpha else alpha_mask(im)
        if not alpha.point(lambda value: 255 if value > 16 else 0).getbbox():
            raise ValueError(f"Empty cutout for {asset_id}")
        alpha.save(path,compress_level=1)
        preview = im.convert("RGB")
        preview.thumbnail((640,640))
        preview.save(folder/(asset_id+"-source.jpg"),quality=87)
    im.putalpha(alpha)
    preview = im.copy()
    preview.thumbnail((640,640),Image.Resampling.LANCZOS)
    preview.save(folder/(asset_id+"-cutout.png"))
    return im
