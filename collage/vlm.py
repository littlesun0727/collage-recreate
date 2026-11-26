"""One logical VLM request with bounded gateway retries and local JSON cleanup."""
import base64
import io
import json
import os
import re
from .remote_retry import with_retries
from pathlib import Path
from urllib.request import Request
from .timings import log, urlopen

from jsonschema import Draft202012Validator
from PIL import Image
from . import binding, contracts
from .layout.analysis import compile_inventory, normalize
from .layout.inventory import schema


YIBU_ENDPOINT = "http://142.252.101.106/v1/chat/completions"


def endpoint():
    return os.environ.get("COLLAGE_VLM_URL") or YIBU_ENDPOINT


# Match the v10 auto policy. These Qwen variants use a 1000-unit reference canvas.
NORMALIZED_MODELS = {"qwen/qwen3.8-flash", "qwen/qwen3.8-max-0902", "qwen3.8-flash",
                     "qwen3.8-max-0902", "qwen3.8-max"}


def parse_content(content):
    """Repair punctuation only. Leave truncated strings and missing values as errors."""
    if not isinstance(content, str):
        raise ValueError("VLM returned no text")
    text = content.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S | re.I)
    if fence:
        text = fence[1]
    for attempt in range(5):
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            if attempt == 4:
                raise
            pos = error.pos
            left, right = text[:pos], text[pos:]
            if error.msg == "Expecting ',' delimiter" and right.startswith(':'):
                # An object closed before its last named field: ...}, "fade": ...
                match = re.search(r'([}])\s*,\s*"(?:[^"\\]|\\.)*"\s*$', left)
                if not match:
                    raise
                text = text[:match.start()] + text[match.start()+1:]
            elif error.msg == "Expecting ',' delimiter" and right.startswith(('"', '{', '[')):
                text = left + ',' + right
            elif right.startswith(',') and right[1:].lstrip().startswith(('}', ']')):
                text = left + right[1:]
            elif right.startswith(('}', ']')) and left.rstrip().endswith(','):
                text = left.rstrip()[:-1] + right
            else:
                raise


def request_choice(req):
    def send():
        with urlopen(req, timeout=300) as response:
            return json.load(response)["choices"][0]

    return with_retries(send)


def binding_preview(path):
    """Encode a proportional preview without changing source pixels or crop coordinates."""
    with Image.open(path) as image:
        with io.BytesIO() as buffer:
            save_binding_jpeg(image, buffer)
            return buffer.getvalue()


def save_binding_jpeg(image, destination):
    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    rgba = image.convert("RGBA")
    preview = Image.new("RGB", rgba.size, "white")
    preview.paste(rgba, mask=rgba.getchannel("A"))
    preview.save(destination, format="JPEG", quality=80)


def ask(stage, context, images, output_schema):
    prompt = (contracts.ROOT / "prompts" / (stage + ".md")).read_text(encoding="utf-8")
    pixel_analysis = stage == "analyze" and context["coordinate_space"] == "pixel"
    if pixel_analysis:
        w, h = context["reference_size"]
        instructions = f"参考图宽 {w} 像素、高 {h} 像素。所有整图位置均使用原图像素，x 在 0～{w}，y 在 0～{h}。"
    else:
        instructions = "整图坐标使用 0～1000，分别按宽、高归一化。"
    prompt = prompt.replace("{{BBOX_COORDINATES}}", instructions)
    prompt = prompt.replace("{{REFERENCE_COORDINATES}}", "参考图坐标单位为像素；source_crop 相对客户原图归一化。")
    parts = [{"type": "text", "text": prompt + "\n" + json.dumps(context, ensure_ascii=False)
              + "\n输出 JSON Schema\n" + json.dumps(output_schema, ensure_ascii=False)}]
    for label, path in images:
        log(f"编码请求图片 {label}，格式 {'JPEG 缩略图' if stage == 'bind' else 'PNG'}")
        if stage == "bind":
            mime = "image/jpeg"
            data = path.read_bytes() if path.suffix == ".jpg" else binding_preview(path)
        else:
            mime, data = "image/png", Path(path).read_bytes()
        encoded = base64.b64encode(data).decode("ascii")
        image_url = {"url": f"data:{mime};base64," + encoded}
        if pixel_analysis and os.environ["COLLAGE_VLM_MODEL"].split("/")[-1].startswith("gpt-"):
            image_url["detail"] = "original"
        parts.extend([{"type": "text", "text": label},
                      {"type": "image_url", "image_url": image_url}])
    model = os.environ["COLLAGE_VLM_MODEL"]
    if stage == "bind":
        model = os.getenv("COLLAGE_BIND_MODEL") or model
    payload = {"model": model, "messages": [{"role": "user", "content": parts}],
               "response_format": {"type": "json_object"}}
    if payload["model"].split("/")[-1] in {"gpt-6.1-sol", "gpt-6.2-sol"}:
        payload["reasoning_effort"] = "medium"
    req = Request(endpoint(), data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["COLLAGE_VLM_KEY"]})
    log(f"模型 {model}，图片 {len(images)} 张，请求体 {len(req.data)/1048576:.2f} MiB")
    choice = request_choice(req)
    log("已读取模型回复，开始解析 JSON")
    if choice.get("finish_reason") not in ("stop", None):
        raise ValueError(f"{stage} returned an incomplete response")
    return parse_content(choice["message"]["content"])


def analyze(run, size):
    mode = "normalized_1000" if os.environ.get("COLLAGE_VLM_MODEL") in NORMALIZED_MODELS else "pixel"
    context = {"coordinate_space": mode}
    if mode == "pixel":
        context["reference_size"] = size
    contract = schema(size, mode)
    result = ask("analyze", context, [("reference", run/"prepared/reference.png")], contract)
    log(f"校验分析结构并生成图层顺序，坐标模式 {mode}")
    value = normalize(result)
    Draft202012Validator(contract).validate(value)
    return compile_inventory(value, size, mode)


def bind(run, analysis, catalog):
    context = binding.context_for({"reference_size": analysis["reference_size"], "catalog": catalog}, analysis)
    if not context["photo_slots"]:
        log("照片槽为 0，跳过选片请求")
        return []
    contract = binding.schema_for(context, analysis)
    images = [("reference", run/"prepared/reference.png")]
    images.extend((a["id"], run/"prepared"/a.get("binding_preview", a["id"] + ".png")) for a in catalog)
    answer = ask("bind", context, images, contract)
    log("校验照片槽、素材编号和裁切参数")
    return binding.validate(answer, context, analysis, catalog)["photos"]
