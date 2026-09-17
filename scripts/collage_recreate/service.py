"""One DashScope image adapter, with a durable guard against duplicate billing."""
from __future__ import annotations

import base64
from pathlib import Path
import os
import re
import time
from urllib.parse import urlparse

import httpx
from pydantic import Field, model_validator

from .core import (ToolError, digest, file_hash, load_image, parse, png_bytes, read_json,
                   relative_path, require, write_bytes, write_json)
from .models import Asset, Generation, Project, Strict

MODEL = "qwen-image-3.0-pro"
ENDPOINT_PATH = "/api/v1/services/aigc/multimodal-generation/generation"


class ServiceConfig(Strict):
    base_url: str
    api_key_env: str = "DASHSCOPE_API_KEY"
    api_key_file: str | None = Field(default=None, repr=False)
    api_key_json_pointer: str = ""
    allow_loopback_http: bool = False
    timeout_seconds: float = Field(default=180, ge=1, le=1800)
    download_hosts: list[str] = Field(default_factory=list)
    prompt_extend: bool = False
    watermark: bool = False

    @model_validator(mode="after")
    def endpoint(self):
        try:
            url = urlparse(self.base_url)
            loopback = self.allow_loopback_http and url.scheme == "http" and url.hostname in ("127.0.0.1", "::1")
            valid = (url.scheme == "https" or loopback) and url.hostname and not (
                url.username or url.password or url.query or url.fragment or any(c.isspace() for c in self.base_url)
            )
            valid = valid and (url.port is None or 1 <= url.port <= 65535)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Use HTTPS or explicitly allowed loopback HTTP, without embedded credentials")
        if self.api_key_json_pointer and not (self.api_key_file and self.api_key_json_pointer.startswith("/")):
            raise ValueError("A credential pointer needs a file and a leading slash")
        return self

    def credential(self):
        if self.api_key_file:
            try:
                value = Path(self.api_key_file).read_text(encoding="utf-8-sig")
                if self.api_key_json_pointer:
                    import json
                    value = json.loads(value)
                    for part in self.api_key_json_pointer[1:].split("/"):
                        part = part.replace("~1", "/").replace("~0", "~")
                        value = value[int(part)] if isinstance(value, list) else value[part]
            except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError):
                raise ToolError("CREDENTIAL_MISSING", "Cannot read configured credential") from None
        else:
            value = os.environ.get(self.api_key_env)
        require(isinstance(value, str) and bool(value.strip()), "CREDENTIAL_MISSING", "Configure the image-service credential")
        return value.strip()

    @property
    def endpoint_url(self):
        return self.base_url.rstrip("/") + ENDPOINT_PATH


def load_config(path):
    data = read_json(path)
    config = parse(ServiceConfig, data)
    if config.api_key_file:
        credential = Path(config.api_key_file)
        if not credential.is_absolute():
            config.api_key_file = str((Path(path).resolve().parent / credential).resolve())
    return config


def ledger(task):
    path = relative_path(task.root, ".state/requests.json", exists=False)
    return read_json(path) if path.is_file() else {"image_requests": 0, "per_asset": {}, "requests": {}}


def save_ledger(task, data):
    write_json(relative_path(task.root, ".state/requests.json", exists=False), data)


def safe_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value) else None


def build_payload(task, project, spec, config):
    require(len(spec.references) <= 3, "INPUT_LIMIT", "The configured DashScope interface accepts at most 3 references")
    content = []
    hashes = []
    for ref in spec.references:
        require(ref.asset in project.permissions.upload_asset_ids, "UPLOAD_NOT_AUTHORIZED", f"Upload not allowed for {ref.asset}")
        require(ref.asset in project.assets and project.assets[ref.asset].kind == "image", "IMAGE_MISSING", "Reference image not found")
        asset = project.assets[ref.asset]
        normalized = png_bytes(load_image(task.asset_path(asset)))
        require(len(normalized) <= 10 * 1024 * 1024, "INPUT_LIMIT", "Reference exceeds 10 MiB after normalization")
        content.append({"image": "data:image/png;base64," + base64.b64encode(normalized).decode("ascii")})
        hashes.append({"sha256": asset.sha256, "role": ref.role})
    width, height = map(int, spec.size.split("x"))
    require(512 ** 2 <= width * height <= 2048 ** 2, "SIZE_INVALID", "Image size must contain 512²–2048² pixels")
    roles = "\n".join(f"Image {i + 1}: {ref.role}" for i, ref in enumerate(spec.references))
    content.append({"text": (roles + "\n" + spec.prompt).strip()})
    parameters = {"n": 1, "size": f"{width}*{height}", "prompt_extend": config.prompt_extend, "watermark": config.watermark}
    payload = {"model": MODEL, "input": {"messages": [{"role": "user", "content": content}]}, "parameters": parameters}
    key = digest({"endpoint": digest(config.endpoint_url), "model": MODEL, "parameters": parameters,
                  "inputs": hashes, "prompt": spec.prompt})
    return key, payload, hashes


def _artifact(task, key, body, config, client):
    require(isinstance(body, dict), "RESPONSE_INVALID", "Expected a JSON image response")
    reported = body.get("model")
    require(reported in (None, MODEL), "MODEL_MISMATCH", "The service reported a different model")
    try:
        choices = body["output"]["choices"]
        require(len(choices) == 1, "RESPONSE_INVALID", "Expected exactly one image choice")
        require(choices[0].get("finish_reason") in (None, "stop"), "RESPONSE_INVALID", "Image response was incomplete")
        items = choices[0]["message"]["content"]
        images = [item["image"] for item in items if isinstance(item, dict) and item.get("image")]
        require(len(images) == 1 and isinstance(images[0], str), "RESPONSE_INVALID", "Expected exactly one image")
        url = images[0]
    except (KeyError, TypeError, IndexError, AttributeError):
        raise ToolError("RESPONSE_INVALID", "Invalid DashScope image response") from None
    if url.startswith("data:image/"):
        try:
            header, encoded = url.split(",", 1)
            require(header.endswith(";base64"), "RESPONSE_INVALID", "Expected base64 image data")
            require(len(encoded) <= 90 * 1024 * 1024, "IMAGE_LIMIT", "Generated image exceeds download limit")
            data = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise ToolError("RESPONSE_INVALID", "Invalid image encoding") from None
    else:
        parsed = urlparse(url)
        require(parsed.scheme == "https" and parsed.hostname in config.download_hosts
                and not parsed.username and not parsed.password,
                "DOWNLOAD_HOST_BLOCKED", "Allow the actual output host in config, then resume; do not regenerate")
        chunks = []
        count = 0
        try:
            # No authorization is attached; redirects cannot receive the API key.
            with client.stream("GET", url, follow_redirects=False, timeout=config.timeout_seconds) as response:
                require(response.status_code == 200, "DOWNLOAD_FAILED", "Download failed; resume the saved response")
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    require(count <= 64 * 1024 * 1024, "IMAGE_LIMIT", "Generated image exceeds 64 MiB")
                    chunks.append(chunk)
        except httpx.TransportError:
            raise ToolError("DOWNLOAD_FAILED", "Download interrupted; resume the saved response") from None
        data = b"".join(chunks)
    require(len(data) <= 64 * 1024 * 1024, "IMAGE_LIMIT", "Generated image exceeds 64 MiB")
    raw = relative_path(task.root, f".state/remote/{key}.download", exists=False)
    write_bytes(raw, data)
    image = load_image(raw)
    normalized = png_bytes(image)
    import hashlib
    sha = hashlib.sha256(normalized).hexdigest()
    relative = f"assets/{sha}.png"
    write_bytes(relative_path(task.root, relative, exists=False), normalized)
    return {"path": relative, "sha256": sha, "alpha_range": list(image.getchannel("A").getextrema()),
            "reported_model": reported, "width": image.width, "height": image.height}


def bind(task, project, asset_id, artifact):
    asset = Asset(kind="image", path=artifact["path"], sha256=artifact["sha256"], alpha_range=artifact["alpha_range"])
    if project.assets.get(asset_id) != asset:
        data = project.model_dump(mode="json")
        data["assets"][asset_id] = asset.model_dump()
        data["revision"] += 1
        task.save(parse(Project, data))


def result(task, key, record, *, cached=False):
    artifact = record["artifact"]
    return {"request_key": key, "remote_status": record["status"], "requested_model": MODEL,
            "reported_model": artifact.get("reported_model"), "request_id": record.get("request_id"),
            "service_seconds": record.get("service_seconds"), "cache_hit": cached,
            "output": str(relative_path(task.root, artifact["path"])), "sha256": artifact["sha256"],
            "alpha_range": artifact["alpha_range"], "visual_status": "unreviewed"}


def _finish(task, project, key, state, config, client, asset_id):
    record = state["requests"][key]
    body = read_json(relative_path(task.root, record["response"]))
    artifact = _artifact(task, key, body, config, client)
    record.update(status="completed", artifact=artifact)
    save_ledger(task, state)  # Bind can be replayed safely if the process ends here.
    bind(task, project, asset_id, artifact)
    return result(task, key, record)


def generate(task, spec, config, *, client=None, retry_failed=False):
    spec = parse(Generation, spec)
    own_client = client is None
    client = client or httpx.Client(timeout=config.timeout_seconds, follow_redirects=False, trust_env=False)
    try:
        with task.lock():
            project = task.load()
            require(spec.asset_id != project.reference, "REFERENCE_RESERVED", "Generate into a new decoration ID, not the reference")
            require(spec.asset_id not in project.assets or project.assets[spec.asset_id].kind == "image",
                    "ASSET_KIND_CONFLICT", "Cannot replace a font with a generated image")
            key, payload, hashes = build_payload(task, project, spec, config)
            state = ledger(task)
            previous = state["requests"].get(key)
            if previous:
                status = previous["status"]
                if status == "completed":
                    artifact = previous["artifact"]
                    path = relative_path(task.root, artifact["path"])
                    require(file_hash(path) == artifact["sha256"], "CACHE_DAMAGED", "Recover the saved response; do not regenerate")
                    bind(task, project, spec.asset_id, artifact)
                    return result(task, key, previous, cached=True)
                if status == "response_saved":
                    return _finish(task, project, key, state, config, client, spec.asset_id)
                require(status == "failed" and retry_failed, "REQUEST_NOT_RETRYABLE",
                        "Previous request is unresolved or failed; inspect its record before an explicit retry")
            require(not any(r["status"] in ("submitted", "unknown", "response_saved") for r in state["requests"].values()),
                    "RESULT_UNKNOWN", "Resolve the existing request before any new billed attempt")
            require(project.permissions.image_generation, "SERVICE_NOT_AUTHORIZED", "Image generation is disabled for this task")
            require(state["image_requests"] < project.permissions.max_requests, "BUDGET_EXHAUSTED", "Task image request budget exhausted")
            attempts = state["per_asset"].get(spec.asset_id, 0)
            require(attempts < project.permissions.max_requests_per_asset, "BUDGET_EXHAUSTED", "Asset image request budget exhausted")
            key_value = config.credential()
            record = {"status": "submitted", "asset_id": spec.asset_id, "requested_model": MODEL,
                      "input_hashes": hashes, "submitted_at": time.time(), "attempt": attempts + 1}
            if previous:
                record["history"] = previous.get("history", []) + [
                    {k: v for k, v in previous.items() if k not in ("history", "artifact")}
                ]
            state["requests"][key] = record
            state["image_requests"] += 1
            state["per_asset"][spec.asset_id] = attempts + 1
            save_ledger(task, state)
            started = time.monotonic()
            try:
                response = client.post(config.endpoint_url, json=payload, headers={"Authorization": "Bearer " + key_value})
            except httpx.TransportError:
                record.update(status="unknown", service_seconds=round(time.monotonic() - started, 3))
                save_ledger(task, state)
                raise ToolError("RESULT_UNKNOWN", "Submission may have been accepted; automatic resubmission is disabled") from None
            record.update(http_status=response.status_code, service_seconds=round(time.monotonic() - started, 3),
                          request_id=safe_id(response.headers.get("x-request-id")))
            if response.status_code != 200:
                record["status"] = "failed" if 400 <= response.status_code < 500 else "unknown"
                save_ledger(task, state)
                raise ToolError("SERVICE_REJECTED" if record["status"] == "failed" else "RESULT_UNKNOWN",
                                f"Image service HTTP {response.status_code}; inspect the saved request record")
            try:
                body = response.json()
            except ValueError:
                record["status"] = "unknown"
                save_ledger(task, state)
                raise ToolError("RESULT_UNKNOWN", "Successful HTTP response was not valid JSON; reconcile before retry") from None
            saved = f".state/remote/{key}.response.json"
            write_json(relative_path(task.root, saved, exists=False), body)
            record.update(status="response_saved", response=saved)
            if isinstance(body, dict):
                record["request_id"] = safe_id(body.get("request_id")) or record["request_id"]
            save_ledger(task, state)
            return _finish(task, project, key, state, config, client, spec.asset_id)
    finally:
        if own_client:
            client.close()


def resume(task, key, config, *, response_path=None, evidence_path=None, client=None):
    own_client = client is None
    client = client or httpx.Client(timeout=config.timeout_seconds, follow_redirects=False, trust_env=False)
    try:
        with task.lock():
            state = ledger(task)
            require(key in state["requests"], "REQUEST_MISSING", "Unknown request key")
            record = state["requests"][key]
            if response_path is not None:
                require(evidence_path is not None and Path(evidence_path).is_file(), "EVIDENCE_REQUIRED",
                        "Recovered responses require a local service/operator evidence file")
                body = read_json(response_path)
                require(isinstance(body, dict), "RESPONSE_INVALID", "Expected a recovered response object")
                if record.get("request_id"):
                    require(body.get("request_id") == record["request_id"], "REQUEST_MISMATCH", "Recovered response has a different request ID")
                saved = f".state/remote/{key}.response.json"
                write_json(relative_path(task.root, saved, exists=False), body)
                evidence = Path(evidence_path).read_bytes()
                evidence_relative = f".state/remote/{key}.evidence"
                write_bytes(relative_path(task.root, evidence_relative, exists=False), evidence)
                record.update(status="response_saved", response=saved,
                              recovery={"evidence": evidence_relative, "sha256": file_hash(evidence_path), "source": "operator"})
                save_ledger(task, state)
            require(record.get("response") is not None, "RESULT_UNKNOWN", "No response saved; recover the existing request from the service")
            return _finish(task, task.load(), key, state, config, client, record["asset_id"])
    finally:
        if own_client:
            client.close()
