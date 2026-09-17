import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

from PIL import Image
import pytest

from collage_recreate.core import Task, ToolError, load_image, relative_path, write_json
from collage_recreate.export import export_task
from collage_recreate.render import compose, render
from conftest import ROOT, apply_data
from test_render import picture, title
from test_service import response_body, service_task


def run_cli(*args, script=None, cwd=None):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, str(script or ROOT / "scripts/run.py"), *map(str, args)],
                            cwd=cwd or ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
    return result, json.loads(result.stdout)


@pytest.mark.parametrize("value", ["../outside.png", "C:/secret.png", "/tmp/secret", "assets/../x", "https://example.test/image", "C:relative.png"])
def test_resource_paths_cannot_escape(task, value):
    with pytest.raises(ToolError) as error:
        relative_path(task.root, value, exists=False)
    assert error.value.code == "PATH_INVALID"


def test_palette_alpha_and_exif_orientation_survive(tmp_path):
    palette = Image.new("P", (4, 4))
    palette.putpalette([255, 0, 0, 0, 255, 0] + [0, 0, 0] * 254)
    palette.putpixel((2, 2), 1)
    palette.info["transparency"] = 0
    path = tmp_path / "palette.png"
    palette.save(path)
    image = load_image(path)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((2, 2))[3] == 255
    photo = Image.new("RGB", (20, 40), "red")
    exif = Image.Exif()
    exif[274] = 6
    photo.save(tmp_path / "rotated.jpg", exif=exif)
    assert load_image(tmp_path / "rotated.jpg").size == (40, 20)


def test_animated_and_corrupt_images_rejected(tmp_path):
    path = tmp_path / "animated.gif"
    Image.new("RGB", (8, 8), "red").save(path, save_all=True, append_images=[Image.new("RGB", (8, 8), "blue")])
    with pytest.raises(ToolError) as error:
        load_image(path)
    assert error.value.code == "STATIC_ONLY"
    path.write_bytes(b"not an image")
    with pytest.raises(ToolError) as error:
        load_image(path)
    assert error.value.code == "IMAGE_INVALID"


def test_export_relocates_and_runs_its_own_code(task, tmp_path):
    apply_data(task, {"elements": [picture(), title()]})
    original = render(task)
    private = task.root / ".state" / "private-response.json"
    write_json(private, {"token": "never-export-fixture"})
    exported = tmp_path / "first-export"
    export_task(task, exported)
    relocated = tmp_path / "relocated export"
    shutil.copytree(exported, relocated)
    # Move only this test's task after checking both absolute targets.
    source = task.root.resolve()
    hidden = (tmp_path / "original-task-unavailable").resolve()
    assert source.is_relative_to(tmp_path.resolve()) and hidden.is_relative_to(tmp_path.resolve())
    source.rename(hidden)
    process, payload = run_cli("render", "--task", relocated,
                               script=relocated / "runtime/run.py", cwd=relocated)
    assert process.returncode == 0, payload
    assert payload["sha256"] == original["sha256"]
    assert not (relocated / "private").exists()
    assert not (relocated / ".state/private-response.json").exists()
    assert not (relocated / "config.local.json").exists()
    assert "reference" not in Task(relocated).load().assets
    assert str(task.root) not in (relocated / "project.json").read_text(encoding="utf-8")
    assert list((relocated / "assets/licenses").glob("*.txt"))
    for path in relocated.rglob("*.json"):
        assert "never-export-fixture" not in path.read_text(encoding="utf-8")


def test_nonredistributable_font_is_explicit_dependency(task, tmp_path):
    apply_data(task, {"elements": [title()]})
    original = render(task)
    project = task.load()
    font_path = task.asset_path(project.assets["font"])
    project.assets["font"].redistributable = False
    task.save(project)
    destination = tmp_path / "font-export"
    result = export_task(task, destination)
    assert result["font_requirements"] == ["font"]
    assert not (destination / project.assets["font"].path).exists()
    process, payload = run_cli("render", "--task", destination, script=destination / "runtime/run.py")
    assert process.returncode == 1 and payload["error"]["code"] == "FILE_MISSING"
    process, payload = run_cli("render", "--task", destination, "--font", f"font={font_path}",
                               script=destination / "runtime/run.py")
    assert process.returncode == 0 and payload["sha256"] == original["sha256"]


def test_empty_missing_and_argument_errors_are_json(task):
    for args, expected in [
        (["render", "--task", task.root], "EMPTY_LAYOUT"),
        (["render", "--task", task.root / "missing"], "TASK_MISSING"),
        (["apply", "--task", task.root, "--input", task.root / "missing.json"], "FILE_MISSING"),
        (["render"], "ARGUMENT_INVALID"),
    ]:
        process, data = run_cli(*args)
        assert process.returncode == 1
        assert data["ok"] is False and data["error"]["code"] == expected
        assert "Traceback" not in process.stderr


def test_cli_exclusive_lock_rejects_second_operation(task):
    apply_data(task, {"elements": [picture()]})
    with task.lock():
        process, data = run_cli("render", "--task", task.root)
    assert process.returncode == 1 and data["error"]["code"] == "TASK_BUSY"


def test_public_generation_resume_and_inspect_on_local_fake_service(service_task, tmp_path):
    # Real CLI and HTTP transport, confined to a fake loopback service; no paid API.
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            encoded = json.dumps(response_body()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = tmp_path / "fake-config.json"
        write_json(cfg, {"base_url": f"http://127.0.0.1:{server.server_port}", "allow_loopback_http": True})
        request = tmp_path / "generation.json"
        write_json(request, {"asset_id": "generated", "prompt": "Offline fixture",
                             "references": [{"asset": "reference", "role": "reference_style"}]})
        process, generated = run_cli("generate", "--task", service_task.root, "--input", request, "--config", cfg)
        assert process.returncode == 0, generated
        process, resumed = run_cli("resume", "--task", service_task.root, "--request", generated["request_key"], "--config", cfg)
        assert process.returncode == 0 and resumed["sha256"] == generated["sha256"]
        process, inspected = run_cli("inspect", "--task", service_task.root)
        assert process.returncode == 0 and inspected["image_requests"] == 1
        assert len(calls) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
