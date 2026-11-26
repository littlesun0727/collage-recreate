"""Local Harmony integration server. Start with python -B server.py."""
import json
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from collage import pipeline
from collage.storage import read, save

ROOT = Path(os.environ.get("COLLAGE_RUNS", Path(__file__).resolve().parent.parent/"collage-recreate-v11-output"/"jobs"))
WORKERS = ThreadPoolExecutor(max_workers=4)
MAX_BODY = 96 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/jobs":
            return self.reply(404, {"error": "unknown route"})
        run = ROOT/uuid4().hex
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                return self.reply(413, {"error": "expected JSON body of at most 96 MiB"})
            request = json.loads(self.rfile.read(size))
            if not isinstance(request["assets"], list):
                raise ValueError("assets must be an array")
            analysis = pipeline.first(run, request)
        except Exception as exc:
            return self.reply(422, {"error": str(exc)[:1200]})
        save(run/"status.json", {"status": "processing"})
        WORKERS.submit(pipeline.finish, run)
        self.reply(202, {"job_id": run.name, "analysis": analysis, "analysis_url": f"/jobs/{run.name}/analysis.json",
                         "status_url": f"/jobs/{run.name}", "bundle_url": f"/jobs/{run.name}/delivery.zip"})

    def do_GET(self):
        match = re.fullmatch(r"/jobs/([a-f0-9]{32})(?:/(analysis\.json|delivery\.zip))?", self.path)
        if not match:
            return self.reply(404, {"error": "unknown route"})
        ident, resource = match.groups()
        run = ROOT/ident
        if not (run/"status.json").is_file():
            return self.reply(404, {"error": "job not found"})
        status = read(run/"status.json")
        if not resource:
            return self.reply(200, status)
        if resource == "analysis.json":
            return self.reply(200, read(run/resource))
        if status["status"] != "ready":
            return self.reply(409, status)
        with (run/resource).open("rb") as source:
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str((run/resource).stat().st_size))
            self.end_headers()
            shutil.copyfileobj(source, self.wfile)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8811)
    parser.add_argument("--api-config", type=Path)
    args = parser.parse_args()
    from collage.api_config import configure
    configure(args.api_config)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
