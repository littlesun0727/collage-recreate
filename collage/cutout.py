"""Local ONNX inference for BiRefNet General and General Lite."""
from functools import lru_cache
import os
from pathlib import Path
from threading import Lock
from filelock import FileLock
from tempfile import gettempdir

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from .timings import log

_inference = Lock()


MODEL_DIR = Path(__file__).resolve().parents[1] / "models"


def model_path():
    selection = os.getenv("COLLAGE_BIREFNET_MODEL") or "birefnet-general-lite"
    if selection == "birefnet-general-light":
        selection = "birefnet-general-lite"
    if selection in ("birefnet-general", "birefnet-general-lite"):
        return MODEL_DIR / (selection + ".onnx")
    return Path(selection).expanduser().resolve()


@lru_cache(maxsize=1)
def session(path):
    import onnxruntime as ort
    if not path.is_file():
        raise FileNotFoundError(f"BiRefNet weights missing: {path}; set COLLAGE_BIREFNET_MODEL")
    options = ort.SessionOptions()
    options.intra_op_num_threads = min(6, os.cpu_count() or 1)
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def alpha_mask(im, path=None):
    path = path or model_path()
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
        raise ValueError(f"{path.stem} returned a constant mask")
    mask = np.uint8(255*(mask-mask.min())/span)
    return Image.fromarray(mask).resize(im.size, Image.Resampling.LANCZOS)


def subject(im, folder, asset_id):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(asset_id+"-mask.png")
    keep_alpha = im.getchannel("A").getextrema()[0] < 255
    weights = None if keep_alpha else model_path()
    source = "source alpha" if keep_alpha else str(weights)
    alpha = None
    if path.exists():
        with Image.open(path) as cached:
            if cached.info.get("cutout_source") == source:
                alpha = cached.convert("L")
        if alpha is not None and alpha.size != im.size:
            raise ValueError(f"Cached cutout dimensions changed for {asset_id}")
    if alpha is None:
        log(f"{asset_id} cutout: {source}")
        alpha = im.getchannel("A") if keep_alpha else alpha_mask(im, weights)
        if not alpha.point(lambda value: 255 if value > 16 else 0).getbbox():
            raise ValueError(f"Empty cutout for {asset_id}")
        metadata = PngInfo()
        metadata.add_itxt("cutout_source", source)
        alpha.save(path, compress_level=1, pnginfo=metadata)
        preview = im.convert("RGB")
        preview.thumbnail((640,640))
        preview.save(folder/(asset_id+"-source.jpg"),quality=87)
    im.putalpha(alpha)
    preview = im.copy()
    preview.thumbnail((640,640),Image.Resampling.LANCZOS)
    preview.save(folder/(asset_id+"-cutout.png"))
    return im
