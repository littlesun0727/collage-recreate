"""Budgeted draft checks and a terminal review decision; no model calls."""
import argparse
import json
import sys
import time

from collage_recreate.analysis_session import DECISIONS, ISSUES, finish_draft, run_draft_operation
from collage_recreate.core import ToolError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("check", "preview", "finish"):
        command = commands.add_parser(name)
        command.add_argument("--task", required=True, help="The same analysis task root as refine")
        command.add_argument("--reference", required=True)
        if name == "finish":
            command.add_argument("--decision", required=True, choices=DECISIONS)
            command.add_argument("--note", required=True, help="Actual visual review and remaining issues")
        else:
            command.add_argument("--input", required=True)
            command.add_argument("--issue", choices=ISSUES)
            command.add_argument("--reason")
            if name == "preview":
                command.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    start = time.monotonic()
    try:
        if args.operation == "finish":
            result = finish_draft(args.task, args.reference, args.decision, args.note)
        else:
            result = run_draft_operation(args.task, args.input, args.reference, args.operation,
                        output=getattr(args, "output", None), issue=args.issue, reason=args.reason)
    except ToolError as exc:
        result = {"ok":False, "error":{"code":exc.code, "message":str(exc)}}
    except OSError:
        result = {"ok":False, "error":{"code":"FILE_IO", "message":"Cannot read or write requested files"}}
    result["elapsed_seconds"] = round(time.monotonic()-start, 3)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
