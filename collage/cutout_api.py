"""Image editing providers for subject masks."""
import base64
import io
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request

from PIL import Image
from urllib3.filepost import encode_multipart_formdata

from .remote_retry import with_retries
from .timings import urlopen

PROMPT = "移除照片背景，将主要拍摄对象单一主体抠成一张透明底素材。"
DEFAULT_URL = "https://az.gptplus5.com/v1/images/generations"
DEFAULT_MODEL = "doubao-seedream-5-5-261028"


def settings():
    return (os.getenv("COLLAGE_CUTOUT_URL", DEFAULT_URL),
            os.getenv("COLLAGE_CUTOUT_MODEL", DEFAULT_MODEL))


def mask(image):
    url, model = settings()
    key = os.getenv("COLLAGE_CUTOUT_KEY")
    if not key and urlsplit(url).hostname == "az.gptplus5.com":
        key = os.getenv("XUNXING_API_KEY")
    if not key:
        raise ValueError("Missing cutout.api_key or COLLAGE_CUTOUT_KEY")
    preview = image.convert("RGB")
    preview.thumbnail((900, 700), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    preview.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()
    fields = dict(model=model, prompt=PROMPT, background="transparent", output_format="png")
    if urlsplit(url).path.rstrip("/").endswith("/images/edits"):
        fields.update(image=("source.jpg", data, "image/jpeg"), n="1")
        body, content_type = encode_multipart_formdata(fields)
    else:
        fields.update(image="data:image/jpeg;base64," + base64.b64encode(data).decode(),
                      n=1, size="2K", watermark=False)
        body, content_type = json.dumps(fields).encode(), "application/json"
    request = Request(url, data=body, headers={"Authorization": "Bearer " + key,
                                               "Content-Type": content_type})

    def submit():
        with urlopen(request, timeout=300) as response:
            return json.load(response)["data"][0]

    result = with_retries(submit)
    if result.get("b64_json"):
        pixels = base64.b64decode(result["b64_json"], validate=True)
    else:
        def download():
            link = result["url"]
            if urlsplit(link).scheme not in ("https", "http"):
                raise ValueError("Expected an HTTP image URL")
            with urlopen(link, timeout=120) as response:
                return response.read()
        pixels = with_retries(download)
    with Image.open(io.BytesIO(pixels)) as output:
        if "A" not in output.getbands() and "transparency" not in output.info:
            raise ValueError("Cutout API returned an image without transparency")
        alpha = output.convert("RGBA").getchannel("A")
        low, high = alpha.getextrema()
        if low > 16 or high < 128:
            raise ValueError("Cutout API returned an unusable alpha mask")
        if abs((output.width / output.height) / (preview.width / preview.height) - 1) > .02:
            raise ValueError("Cutout API changed the canvas aspect ratio; mask cannot be aligned")
        return alpha.resize(image.size, Image.Resampling.LANCZOS)
