"""Load CLI settings before starting case workers. Keys never enter artifacts."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {
    "vlm": {"url": "COLLAGE_VLM_URL", "analysis_model": "COLLAGE_VLM_MODEL",
            "binding_model": "COLLAGE_BIND_MODEL", "api_key": "COLLAGE_VLM_KEY"},
    "analysis": {"url": "COLLAGE_ANALYSIS_URL", "model": "COLLAGE_VLM_MODEL",
                 "api_key": "COLLAGE_ANALYSIS_KEY", "reasoning_effort": "COLLAGE_ANALYSIS_EFFORT"},
    "binding": {"url": "COLLAGE_BIND_URL", "model": "COLLAGE_BIND_MODEL",
                "api_key": "COLLAGE_BIND_KEY", "reasoning_effort": "COLLAGE_BIND_EFFORT"},
    "cutout": {"mode": "COLLAGE_CUTOUT_MODE", "url": "COLLAGE_CUTOUT_URL",
               "model": "COLLAGE_CUTOUT_MODEL", "api_key": "COLLAGE_CUTOUT_KEY"},
    "reference_clean": {"url": "COLLAGE_CLEAN_URL", "model": "COLLAGE_CLEAN_MODEL",
                        "api_key": "XUNXING_API_KEY"},
    "reveal": {"base_url": "COLLAGE_REVEAL_URL", "model": "COLLAGE_REVEAL_MODEL",
               "api_key": "360_API_KEY"},
}


def configure(path=None):
    file = Path(path) if path is not None else ROOT/"api-config.json"
    overrides = {}
    if path is not None or file.exists():
        config = json.loads(file.read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict) or config.keys() - FIELDS.keys():
            raise ValueError(f"API 配置顶层支持 {', '.join(FIELDS)}")
        # Load the legacy shared block first, regardless of JSON field order.
        for section in FIELDS:
            if section not in config:
                continue
            values = config[section]
            fields = FIELDS[section]
            if not isinstance(values, dict) or values.keys() - fields.keys():
                raise ValueError(f"API 配置 {section} 格式错误，支持字段 {', '.join(fields)}")
            for field, value in values.items():
                if not isinstance(value, str):
                    raise ValueError(f"{section}.{field} 必须是字符串")
                value = value.strip()
                if not value:
                    continue
                if section == "cutout" and field == "mode" and value not in {"api", "birefnet", "birefnet-light"}:
                    raise ValueError("cutout.mode must be api, birefnet or birefnet-light")
                if field in {"url", "base_url"}:
                    url = urlsplit(value)
                    if url.scheme not in {"http","https"} or not url.hostname or url.username or url.password:
                        raise ValueError(f"{section}.{field} 需要包含 http(s) 协议和主机")
                overrides[fields[field]] = value
    for env_file in (ROOT/".env", ROOT.parent/".env"):
        if env_file.is_file():
            for key, value in dotenv_values(env_file).items():
                if value:
                    os.environ.setdefault(key, value)
    if os.getenv("YIBU_API_KEY"):
        os.environ.setdefault("COLLAGE_VLM_KEY", os.environ["YIBU_API_KEY"])
    os.environ.setdefault("COLLAGE_VLM_MODEL", "gpt-6.2-sol")
    os.environ.setdefault("COLLAGE_BIND_MODEL", "qwen3.8-flash")
    os.environ.update(overrides)
