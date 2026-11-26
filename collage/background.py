"""Generate a clean canvas using the reference and its existing description."""
import base64
import io
import json
import os
from pathlib import Path
from urllib.request import Request
from .timings import log, urlopen

from PIL import Image

from .remote_retry import with_retries


PROMPT = Path(__file__).resolve().parents[1] / "prompts/reference_clean.txt"
ENDPOINT = "https://az.gptplus5.com/v1/images/edits"


def clean(run, background, size):
    prompt = PROMPT.read_text(encoding="utf-8-sig").replace("{背景描述}", background["description"])
    reference = (run / "prepared/reference.png").read_bytes()
    encoded = base64.b64encode(reference).decode("ascii")
    model = os.getenv("COLLAGE_CLEAN_MODEL", "gpt-image-2")
    body = {"model": model, "prompt": prompt, "n": 1, "size": "auto",
            "images": [{"image_url": "data:image/png;base64," + encoded}]}
    request = Request(os.getenv("COLLAGE_CLEAN_URL", ENDPOINT), data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + os.environ["XUNXING_API_KEY"]})

    def generate():
        log(f"{model} 请求清底")
        with urlopen(request, timeout=600) as response:
            return json.load(response)["data"][0]

    result = with_retries(generate)
    if result.get("b64_json"):
        pixels = base64.b64decode(result["b64_json"], validate=True)
    else:
        def download():
            log("下载清底结果")
            with urlopen(result["url"], timeout=120) as response:
                return response.read()
        pixels = with_retries(download)

    path = run / "reference_clean.png"
    log(f"解码并调整清底图至 {size}，写入 {path.name}")
    with Image.open(io.BytesIO(pixels)) as image:
        image.convert("RGB").resize(tuple(size), Image.Resampling.LANCZOS).save(path)
    return path
