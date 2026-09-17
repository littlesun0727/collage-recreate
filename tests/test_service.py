import base64
from io import BytesIO
import json
from pathlib import Path

import httpx
from PIL import Image
import pytest

from collage_recreate.core import ToolError, read_json, write_json
from collage_recreate.service import (MODEL, ServiceConfig, generate, ledger, resume)


@pytest.fixture
def service_task(task_factory, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-secret-never-echo")
    return task_factory(permissions={"image_generation": True, "max_requests": 4,
                                    "max_requests_per_asset": 2, "upload_asset_ids": ["reference"]})


def config(**kwargs):
    return ServiceConfig(base_url="https://image.example.test", **kwargs)


def spec(**kwargs):
    return {"asset_id": "decor", "prompt": "Make one separate paper texture",
            "references": [{"asset": "reference", "role": "reference_style"}], "size": "1024x1024", **kwargs}


def response_body(*, model=MODEL, url=None):
    buffer = BytesIO()
    Image.new("RGB", (64, 64), "#ffeedd").save(buffer, format="PNG")
    image = url or "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    body = {"request_id": "req-fixture-1", "output": {"choices": [
        {"finish_reason": "stop", "message": {"content": [{"image": image}]}}
    ]}}
    if model is not None:
        body["model"] = model
    return body


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_model_one_candidate_and_cache_are_enforced(service_task):
    calls = []
    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["model"] == MODEL
        assert body["parameters"]["n"] == 1
        assert body["parameters"]["size"] == "1024*1024"
        assert body["input"]["messages"][0]["content"][0]["image"].startswith("data:image/png;base64,")
        assert request.headers["Authorization"] == "Bearer fixture-secret-never-echo"
        return httpx.Response(200, json=response_body())
    with client(handler) as transport:
        first = generate(service_task, spec(), config(), client=transport)
        second = generate(service_task, spec(), config(), client=transport)
    assert len(calls) == 1
    assert first["alpha_range"] == [255, 255]  # RGB white background remains opaque.
    assert second["cache_hit"] and first["sha256"] == second["sha256"]
    assert ledger(service_task)["image_requests"] == 1
    assert "fixture-secret-never-echo" not in (service_task.root / ".state/requests.json").read_text()


def test_timeout_blocks_new_prompt_asset_and_endpoint(service_task):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("untrusted upstream detail", request=request)
    with client(handler) as transport:
        with pytest.raises(ToolError) as first:
            generate(service_task, spec(), config(), client=transport)
        assert first.value.code == "RESULT_UNKNOWN"
        for changed in (spec(), spec(prompt="Different"), spec(asset_id="renamed")):
            with pytest.raises(ToolError):
                generate(service_task, changed, config(), client=transport)
        with pytest.raises(ToolError):
            generate(service_task, spec(), ServiceConfig(base_url="https://other.example.test"), client=transport)
    assert len(calls) == 1
    assert next(iter(ledger(service_task)["requests"].values()))["status"] == "unknown"


@pytest.mark.parametrize("code,expected", [(401, "failed"), (429, "failed"), (500, "unknown"), (302, "unknown")])
def test_failure_states_are_durable_and_sanitized(service_task, code, expected):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(code, json={"error": "secret user content"}, headers={"Location": "https://elsewhere.test"})
    with client(handler) as transport:
        with pytest.raises(ToolError) as error:
            generate(service_task, spec(), config(), client=transport)
        assert "secret user content" not in str(error.value)
        with pytest.raises(ToolError):
            generate(service_task, spec(), config(), client=transport)
    assert len(calls) == 1
    assert next(iter(ledger(service_task)["requests"].values()))["status"] == expected


def test_success_response_download_can_resume_without_post(service_task):
    posts = []
    body = response_body(url="https://assets.example.test/image.png?signature=private")
    with client(lambda request: (posts.append(request), httpx.Response(200, json=body))[1]) as transport:
        with pytest.raises(ToolError) as error:
            generate(service_task, spec(), config(), client=transport)
    assert error.value.code == "DOWNLOAD_HOST_BLOCKED"
    key = next(iter(ledger(service_task)["requests"]))
    assert ledger(service_task)["requests"][key]["status"] == "response_saved"
    binary = base64.b64decode(response_body()["output"]["choices"][0]["message"]["content"][0]["image"].split(",")[1])
    def download(request):
        assert request.method == "GET"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=binary)
    with client(download) as transport:
        result = resume(service_task, key, config(download_hosts=["assets.example.test"]), client=transport)
    assert len(posts) == 1 and result["remote_status"] == "completed"
    assert "signature=private" not in (service_task.root / ".state/requests.json").read_text()


def test_interrupted_download_stays_resumable(service_task):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=response_body(url="https://assets.example.test/file"))
        raise httpx.ReadTimeout("private signed url", request=request)
    with client(handler) as transport:
        with pytest.raises(ToolError) as error:
            generate(service_task, spec(), config(download_hosts=["assets.example.test"]), client=transport)
    assert error.value.code == "DOWNLOAD_FAILED"
    assert next(iter(ledger(service_task)["requests"].values()))["status"] == "response_saved"


def test_model_mismatch_and_corrupt_image_never_bind(service_task):
    with client(lambda request: httpx.Response(200, json=response_body(model="unexpected-model"))) as transport:
        with pytest.raises(ToolError) as error:
            generate(service_task, spec(), config(), client=transport)
    assert error.value.code == "MODEL_MISMATCH"
    assert "decor" not in service_task.load().assets


def test_missing_reported_model_is_not_invented(service_task):
    with client(lambda request: httpx.Response(200, json=response_body(model=None))) as transport:
        result = generate(service_task, spec(), config(), client=transport)
    assert result["requested_model"] == MODEL
    assert result["reported_model"] is None


def test_task_and_asset_budgets_hold_across_spec_changes(service_task):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_body())
    with client(handler) as transport:
        generate(service_task, spec(), config(), client=transport)
        generate(service_task, spec(prompt="Second candidate"), config(), client=transport)
        with pytest.raises(ToolError) as error:
            generate(service_task, spec(prompt="Third candidate"), config(), client=transport)
        assert error.value.code == "BUDGET_EXHAUSTED"
        generate(service_task, spec(asset_id="other", prompt="Other candidate"), config(), client=transport)
        generate(service_task, spec(asset_id="other", prompt="Other next candidate"), config(), client=transport)
        with pytest.raises(ToolError) as error:
            generate(service_task, spec(asset_id="third", prompt="More"), config(), client=transport)
        assert error.value.code == "BUDGET_EXHAUSTED"
    assert len(calls) == 4


def test_denied_permissions_and_missing_credentials_do_not_submit(task, monkeypatch):
    calls = []
    with client(lambda req: calls.append(req)) as transport:
        with pytest.raises(ToolError):
            generate(task, spec(), config(), client=transport)
    assert not calls and ledger(task)["image_requests"] == 0


def test_damaged_cache_recovers_response_without_generation(service_task):
    with client(lambda req: httpx.Response(200, json=response_body())) as transport:
        result = generate(service_task, spec(), config(), client=transport)
    Path(result["output"]).write_bytes(b"broken")
    with client(lambda req: pytest.fail("No network allowed for saved inline image")) as transport:
        with pytest.raises(ToolError) as error:
            generate(service_task, spec(), config(), client=transport)
        assert error.value.code == "CACHE_DAMAGED"
        restored = resume(service_task, result["request_key"], config(), client=transport)
    assert restored["sha256"] == result["sha256"]
    assert ledger(service_task)["image_requests"] == 1


def test_invalid_success_body_is_unknown(service_task):
    with client(lambda req: httpx.Response(200, content=b"not-json")) as transport:
        with pytest.raises(ToolError) as error:
            generate(service_task, spec(), config(), client=transport)
    assert error.value.code == "RESULT_UNKNOWN"


def test_explicit_retry_only_after_definite_failure(service_task):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(429, json={}) if len(calls) == 1 else httpx.Response(200, json=response_body())
    with client(handler) as transport:
        with pytest.raises(ToolError):
            generate(service_task, spec(), config(), client=transport)
        generate(service_task, spec(), config(), client=transport, retry_failed=True)
    assert len(calls) == 2
    record = next(iter(ledger(service_task)["requests"].values()))
    assert record["history"][0]["status"] == "failed"


@pytest.mark.parametrize("base", ["http://example.test", "https://user:secret@example.test", "https://example.test?key=secret"])
def test_unsafe_service_endpoints_are_rejected(base):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        ServiceConfig(base_url=base)

@pytest.mark.parametrize("asset_id,error_code", [("font", "ASSET_KIND_CONFLICT"), ("reference", "REFERENCE_RESERVED")])
def test_generation_cannot_replace_reference_or_font(service_task, asset_id, error_code):
    before = service_task.project_path.read_bytes()
    with client(lambda request: pytest.fail("Invalid target must fail before any HTTP request")) as transport:
        with pytest.raises(ToolError) as error:
            generate(service_task, spec(asset_id=asset_id), config(), client=transport)
    assert error.value.code == error_code
    assert service_task.project_path.read_bytes() == before
    assert ledger(service_task)["image_requests"] == 0
