"""Thin JSON front end. Bundle content never selects imports or commands."""
import argparse
import json
import sys
from .contract import CaptureRequest, InspectorError, QueryLimits, QueryRequest
from .core import query
from .storage import load_bundle, write_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pythonstan-inspector")
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("capture", help="explicitly analyze trusted local Python source")
    live.add_argument("source")
    live.add_argument("output")
    live.add_argument("--iterations", type=int, default=50000)
    live.add_argument("--trace", action="store_true")
    live.add_argument("--views", help="comma-separated capture view names; kwargs_model is opt-in")
    project = sub.add_parser("capture-project", help="analyze a declared project in a bounded worker")
    project.add_argument("config", help="local JSON ProjectConfig file")
    project.add_argument("output", help="directory for this run's report and optional bundle")
    project.add_argument("--timeout", type=float, default=60.0)
    project.add_argument("--memory-mb", type=int, default=1536)
    project.add_argument("--trace", action="store_true")
    project.add_argument("--observation-mode", choices=("off", "counters", "capture", "targeted"),
                         default="capture")
    project.add_argument("--trace-selector", help="JSON source/model selector for targeted mode")
    project.add_argument("--views", help="comma-separated capture view names; kwargs_model is opt-in")
    project.add_argument("--report-mode", choices=("inline", "reference"), default="inline")
    project.add_argument("--max-report-bytes", type=int, default=8 * 1024 * 1024)
    for kind in ("summary", "show", "events", "explain", "diff", "check", "profile"):
        command = sub.add_parser(kind)
        command.add_argument("bundle")
        command.add_argument("--args", default="{}", help="JSON object of bounded query parameters")
        command.add_argument("--claim", default="")
        command.add_argument("--cursor")
        command.add_argument("--max-rows", type=int, default=1000)
        command.add_argument("--max-visited", type=int, default=100000)
        command.add_argument("--max-bytes", type=int, default=1000000)
        command.add_argument("--max-depth", type=int, default=8)
        command.add_argument("--seconds", type=float, default=2.0)
        if kind == "diff":
            command.add_argument("--other", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            from .runner import run_source
            request = CaptureRequest(views=tuple(args.views.split(","))) if args.views is not None else None
            snapshot, trace, metrics = run_source(args.source, request=request,
                max_iterations=args.iterations, trace=args.trace)
            path = write_bundle(snapshot, args.output, trace=trace)
            result = {"bundle": str(path), "snapshot": snapshot.ref.__dict__, "metrics": metrics}
        elif args.command == "capture-project":
            from pathlib import Path
            from .project_runner import run_project, MAX_BOOTSTRAP_BYTES
            with Path(args.config).open("rb") as stream:
                encoded_configuration = stream.read(MAX_BOOTSTRAP_BYTES + 1)
            if len(encoded_configuration) > MAX_BOOTSTRAP_BYTES:
                raise InspectorError("project configuration file exceeds 1 MiB")
            configuration = json.loads(encoded_configuration)
            if type(configuration) is not dict:
                raise InspectorError("project configuration must be a JSON object")
            if args.trace_selector is not None and len(args.trace_selector) > 8192:
                raise InspectorError("trace selector exceeds bounded configuration size")
            selector = json.loads(args.trace_selector) if args.trace_selector is not None else None
            request = CaptureRequest(views=tuple(args.views.split(","))) if args.views is not None else None
            result = run_project(configuration, args.output, timeout_seconds=args.timeout,
                memory_mb=args.memory_mb, trace=args.trace, request=request,
                observation_mode=args.observation_mode, trace_selector=selector,
                report_mode=args.report_mode, max_report_bytes=args.max_report_bytes)
        else:
            parameters = json.loads(args.args)
            if type(parameters) is not dict:
                raise InspectorError("--args must be a JSON object")
            bundle = load_bundle(args.bundle)
            limits = QueryLimits(args.max_rows, args.max_visited, args.max_bytes,
                                 args.max_depth, args.seconds)
            request = QueryRequest(args.command, parameters, args.claim, limits, args.cursor)
            if args.command == "diff":
                # Both sides still use the same offline core; paths are user input only.
                from .core import diff_query
                result = diff_query(bundle, load_bundle(args.other), request)
            else:
                result = query(bundle, request)
        # Native companion reports can faithfully preserve Python strings with
        # unpaired surrogates. ASCII JSON escaping keeps the CLI transport valid
        # without changing those values or invoking arbitrary display hooks.
        print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except (InspectorError, OSError, json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
