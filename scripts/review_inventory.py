"""Check and register visual inventory without calling a model."""
import argparse
import json
import sys
from collage_recreate.analysis_session import run_inventory_check
from collage_recreate.core import ToolError

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--pipeline", choices=["inventory_v1"], default="inventory_v1")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    try:
        result = run_inventory_check(args.task, args.input, args.reference, reason=args.reason,
                                     pipeline=args.pipeline)
    except ToolError as exc:
        result = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except OSError:
        result = {"ok": False, "error": {"code": "FILE_IO", "message": "Cannot read/write task files"}}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1

if __name__ == "__main__":
    sys.exit(main())
