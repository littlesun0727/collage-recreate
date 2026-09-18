"""Render one checked basic_shape overlay as a transparent PNG."""
import argparse
import json
import sys

from collage_recreate.core import ToolError
from collage_recreate.draw_overlay import render_basic_shape


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--overlay-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result = render_basic_shape(args.draft, args.reference, args.overlay_id, args.output)
    except ToolError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except OSError:
        result = {"ok": False, "error": {"code": "FILE_IO", "message": "Cannot write overlay output"}}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
