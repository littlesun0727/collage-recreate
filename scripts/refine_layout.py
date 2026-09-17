"""Refine a rough draft once; a task permits at most one blocking correction."""
import argparse
import json
import sys
from collage_recreate.analysis_session import ISSUES, run_draft_operation
from collage_recreate.core import ToolError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, help="Shared analysis task root; keep it for this request")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True, help="New result directory")
    parser.add_argument("--reuse", help="Previous refinement directory; latest task result is reused automatically")
    parser.add_argument("--issue", choices=ISSUES, help="Only for the one blocking correction")
    parser.add_argument("--reason", help="Affected object, observed problem and intended correction")
    args = parser.parse_args(argv)
    try:
        result = run_draft_operation(args.task, args.input, args.reference, "refine",
                    output=args.output, reuse=args.reuse, issue=args.issue, reason=args.reason)
    except ToolError as exc:
        result = {"ok":False,"error":{"code":exc.code,"message":str(exc)}}
    except OSError:
        result = {"ok":False,"error":{"code":"FILE_IO","message":"Cannot read or write requested files"}}
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
