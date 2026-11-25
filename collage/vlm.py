"""One logical VLM request with bounded gateway retries and local JSON cleanup."""
import base64
import json
import os
import re
import time
from urllib.error import HTTPError
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from jsonschema import Draft202012Validator
from . import binding, contracts
from .layout.analysis import compile_inventory, normalize
from .layout.inventory import schema


YIBU_ENDPOINT = "https://yibuapi.com/v1/chat/completions"
DIRECT_HTTP = build_opener(ProxyHandler({}))


def endpoint():
    url = os.environ.get("COLLAGE_VLM_URL") or YIBU_ENDPOINT
    if url.rstrip("/") in ("http://127.0.0.1:17860/v1/chat/completions",
                           "http://localhost:17860/v1/chat/completions"):
        return YIBU_ENDPOINT
    return url


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
    for attempt in range(3):
        try:
            with DIRECT_HTTP.open(req, timeout=180) as response:
                return json.load(response)["choices"][0]
        except HTTPError as error:
            if error.code not in (502, 503, 504) or attempt == 2:
                raise
            error.close()
            time.sleep(2 ** (attempt + 1))


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
        encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        image_url = {"url": "data:image/png;base64," + encoded}
        if pixel_analysis and os.environ["COLLAGE_VLM_MODEL"].split("/")[-1].startswith("gpt-"):
            image_url["detail"] = "original"
        parts.extend([{"type": "text", "text": label},
                      {"type": "image_url", "image_url": image_url}])
    payload = {"model": os.environ["COLLAGE_VLM_MODEL"], "messages": [{"role": "user", "content": parts}],
               "response_format": {"type": "json_object"}}
    req = Request(endpoint(), data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["COLLAGE_VLM_KEY"]})
    choice = request_choice(req)
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
    value = normalize(result)
    Draft202012Validator(contract).validate(value)
    return compile_inventory(value, size, mode)


def bind(run, analysis, catalog):
    context = binding.context_for({"reference_size": analysis["reference_size"], "catalog": catalog}, analysis)
    if not context["photo_slots"]:
        return []
    contract = binding.schema_for(context, analysis)
    images = [("reference", run/"prepared/reference.png")]
    images.extend((a["id"], run/"prepared"/(a["id"] + ".png")) for a in catalog)
    answer = ask("bind", context, images, contract)
    return binding.validate(answer, context, analysis, catalog)["photos"]
