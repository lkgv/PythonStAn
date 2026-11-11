"""Command line adapters for the public API; stdout contains only selected data."""
from __future__ import annotations

import argparse
import json
import sys

from .api import analyze
from .inputs import Source, File, Project, InputError, snapshot_target
from .results import OutputSpec, ValueQuery
from .config import resolve_config, catalog, ConfigurationError


def _target_options(parser, required=True):
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("--code")
    group.add_argument("--stdin", action="store_true")
    group.add_argument("--file")
    group.add_argument("--project")
    parser.add_argument("--filename", default="snippet.py")
    parser.add_argument("--module", default=None)
    parser.add_argument("--entry", action="append", default=[])
    parser.add_argument("--config")
    parser.add_argument("--set", dest="sets", action="append", default=[])


def _target(args):
    if args.code is not None or args.stdin:
        code = sys.stdin.read() if args.stdin else args.code
        module = args.module or __import__("pathlib").Path(args.filename).stem
        return Source(code, filename=args.filename, module=module)
    if args.file: return File(args.file, module=args.module)
    if args.project: return Project(args.project, entries=tuple(args.entry))
    return Source("")


def _overrides(args):
    result = {}
    for item in args.sets:
        if "=" not in item: raise ConfigurationError("--set requires path=value")
        key, text = item.split("=", 1)
        if key in result: raise ConfigurationError(f"Duplicate override: {key}")
        try: value = json.loads(text)
        except ValueError:
            import yaml
            value = yaml.safe_load(text)
        result[key] = value
    if args.entry and not args.project: result["inputs.entries"] = args.entry
    return result


def _query(value):
    if value.startswith("value:"):
        parts = value.split(":")
        if len(parts) not in (3, 4): raise ConfigurationError("Value query is value:module:symbol[:point]")
        try: return ValueQuery(parts[1], parts[2], parts[3] if len(parts) == 4 else "module_exit")
        except (ValueError, TypeError) as exc: raise ConfigurationError(str(exc)) from exc
    return value


def build_parser():
    parser = argparse.ArgumentParser(prog="pythonstan", description="Static Python analysis with explicit source and entry contracts")
    commands = parser.add_subparsers(dest="command", required=True)
    analysis = commands.add_parser("analyze")
    _target_options(analysis)
    analysis.add_argument("--query", action="append", default=None)
    analysis.add_argument("--view", action="append", default=None)
    analysis.add_argument("--format", choices=("json", "text"))
    analysis.add_argument("--output")
    analysis.add_argument("--require-converged", action="store_true")
    analysis.add_argument("--require-covered", action="store_true")
    analysis.add_argument("--require-verified", action="store_true")
    config = commands.add_parser("config")
    sub = config.add_subparsers(dest="config_command", required=True)
    _target_options(sub.add_parser("show-effective"), required=False)
    sub.add_parser("list-options")
    plan = commands.add_parser("plan")
    _target_options(plan)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "config" and args.config_command == "list-options":
            sys.stdout.write(json.dumps(catalog(), ensure_ascii=False, indent=2) + "\n"); return 0
        target = _target(args); overrides = _overrides(args)
        if args.command == "config":
            config = resolve_config(target, args.config, overrides)
            sys.stdout.write(json.dumps({"effective_config": config.to_dict(), "provenance": config.provenance,
                                         "config_file": config.config_file}, ensure_ascii=False, indent=2) + "\n"); return 0
        if args.command == "plan":
            from .analysis.composition.plan import build_plan
            config = resolve_config(target, args.config, overrides)
            snapshot = snapshot_target(target, config)
            plan = build_plan(snapshot, config.to_dict())
            data = plan.to_dict() if hasattr(plan, "to_dict") else plan
            sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n"); return 0
        queries = tuple(_query(q) for q in args.query) if args.query is not None else None
        if args.view is not None: overrides["output.views"] = args.view
        elif args.query is not None:
            overrides["output.views"] = list(dict.fromkeys(["summary"] + [q for q in args.query if not q.startswith(("value:", "property:"))] +
                                                           (["proof"] if any(q.startswith("property:") for q in args.query) else [])))
        if args.format: overrides["output.format"] = args.format
        result = analyze(target, config=args.config, overrides=overrides, queries=queries)
        format = args.format or result.output.format
        if args.output: result.write(args.output, format=format)
        else: sys.stdout.write(result.serialize(format=format))
        if args.require_converged and result.solver_status != "converged": return 4
        if args.require_covered and result.coverage_status != "closed_under_profile": return 4
        if args.require_verified and result.proof_status != "verified": return 4
        return 0
    except (InputError, ConfigurationError, OSError) as exc:
        sys.stderr.write(f"pythonstan: {exc}\n"); return 2
    except KeyboardInterrupt:
        sys.stderr.write("pythonstan: cancelled\n"); return 130
    except Exception as exc:
        sys.stderr.write(f"pythonstan internal failure: {type(exc).__name__}: {exc}\n"); return 3


if __name__ == "__main__":
    raise SystemExit(main())
