"""Check that every bundled route exists and matches its recorded SHA-256."""
import hashlib
import json
from pathlib import Path


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    checked = set()
    for route, record in manifest["routes"].items():
        path = (root / record["file"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Route escapes package: " + route)
        if path in checked:
            continue
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            raise ValueError("Missing or truncated asset: " + record["file"])
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != record["sha256"]:
            raise ValueError("Checksum mismatch: " + record["file"])
        checked.add(path)
    result = {"result": "pass", "unique_files": len(checked), "routes": len(manifest["routes"]),
              "tasks": manifest["task_count"], "cover_versions": manifest["cover_version_count"],
              "video_versions": manifest["video_version_count"]}
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    verify(Path(__file__).resolve().parent)
