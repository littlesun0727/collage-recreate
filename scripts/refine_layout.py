"""Refine rough draft coordinates locally; uncertain candidates stay pending."""
import argparse
import json
import sys
from collage_recreate.core import ToolError

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True, help="New result directory")
    parser.add_argument("--reuse", help="Previous refinement directory for unchanged inputs")
    args = parser.parse_args(argv)
    try:
        from collage_recreate.localize import refine_layout
        result = refine_layout(args.input, args.reference, args.output, reuse=args.reuse)
    except ImportError:
        result = {"ok":False,"error":{"code":"DEPENDENCY_MISSING","message":"Install requirements-localization.txt in this Python environment"}}
    except ToolError as exc:
        result = {"ok":False,"error":{"code":exc.code,"message":str(exc)}}
    except OSError:
        result = {"ok":False,"error":{"code":"FILE_IO","message":"Cannot read or write requested files"}}
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result["ok"] else 1

if __name__ == "__main__":
    sys.exit(main())
