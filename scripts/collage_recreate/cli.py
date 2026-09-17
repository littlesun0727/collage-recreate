"""Small JSON-returning command interface for the host agent."""
import argparse
import json
import sys
import time

from filelock import Timeout

from .core import Task, ToolError, apply_patch, initialize, read_json, require, summary


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ToolError("ARGUMENT_INVALID", message)


def parser():
    root = Parser(description="Create and edit a static layered collage")
    commands = root.add_subparsers(dest="command", required=True, parser_class=Parser)
    for name in ("init", "apply", "inspect", "check", "render", "generate", "resume", "export", "demo"):
        command = commands.add_parser(name)
        command.add_argument("--task", required=True)
        if name in ("init", "apply", "generate"):
            command.add_argument("--input", required=True)
        if name in ("check", "render", "apply", "export"):
            command.add_argument("--font", action="append", default=[], metavar="ID=PATH")
        if name in ("generate", "resume"):
            command.add_argument("--config", required=True)
        if name == "generate":
            command.add_argument("--retry-failed", action="store_true")
        if name == "resume":
            command.add_argument("--request", required=True)
            command.add_argument("--response")
            command.add_argument("--evidence")
        if name == "export":
            command.add_argument("--output", required=True)
    return root


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    started = time.monotonic()
    try:
        args = parser().parse_args(argv)
        task = Task(args.task)
        fonts = {}
        for value in getattr(args, "font", []):
            require("=" in value, "ARGUMENT_INVALID", "Use --font ID=PATH")
            key, path = value.split("=", 1)
            require(key and path and key not in fonts, "ARGUMENT_INVALID", "Font IDs must be unique and have paths")
            fonts[key] = path
        if args.command == "init":
            data = initialize(task, args.input)
        elif args.command == "apply":
            data = apply_patch(task, args.input, fonts)
        elif args.command == "inspect":
            from .service import ledger
            data = summary(task)
            state = ledger(task)
            data["image_requests"] = state["image_requests"]
            data["requests"] = [{"key": k, "status": r["status"], "asset_id": r["asset_id"]}
                                for k, r in state["requests"].items()]
        elif args.command in ("render", "check"):
            from .render import render
            data = render(task, font_overrides=fonts, check_only=args.command == "check")
        elif args.command in ("generate", "resume"):
            from .service import generate, load_config, resume
            config = load_config(args.config)
            if args.command == "generate":
                data = generate(task, read_json(args.input), config, retry_failed=args.retry_failed)
            else:
                data = resume(task, args.request, config, response_path=args.response, evidence_path=args.evidence)
        elif args.command == "export":
            from .export import export_task
            data = export_task(task, args.output, font_overrides=fonts)
        else:
            from .demo import demo
            data = demo(task)
        output = {"ok": True, **data}
        status = 0
    except ToolError as exc:
        output = {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        status = 1
    except Timeout:
        output = {"ok": False, "error": {"code": "TASK_BUSY", "message": "Another operation holds the task lock"}}
        status = 1
    except OSError as exc:
        output = {"ok": False, "error": {"code": "FILE_IO", "message": f"File operation failed (errno={exc.errno})"}}
        status = 1
    except Exception as exc:
        output = {"ok": False, "error": {"code": "INTERNAL_ERROR", "message": f"Unexpected {type(exc).__name__}; inspect with the offline tests"}}
        status = 1
    output["elapsed_seconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))
    return status
