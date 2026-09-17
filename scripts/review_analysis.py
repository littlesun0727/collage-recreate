"""Check or preview a six-field collage draft; no model calls."""
import argparse
import json
import sys
import time

from collage_recreate.analysis import check_draft, preview_draft
from collage_recreate.core import ToolError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("check", "preview"):
        command = commands.add_parser(name)
        command.add_argument("--reference", required=True, help="Actual original reference, read only")
        command.add_argument("--input", required=True, help="draft.json")
        if name == "preview":
            command.add_argument("--output", required=True, help="New preview directory")
    args = parser.parse_args(argv)
    start = time.monotonic()
    try:
        if args.operation == "check":
            result = check_draft(args.input, args.reference)
        else:
            result = preview_draft(args.input, args.reference, args.output)
    except ToolError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except OSError:
        result = {"ok": False, "error": {"code": "FILE_IO", "message": "Cannot read or write the requested file"}}
    result["elapsed_seconds"] = round(time.monotonic() - start, 3)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
