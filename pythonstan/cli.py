"""Command-line adapters use the same resolved request and result contracts."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from .analysis.contracts.json_data import (
    JsonObject,
    JsonValue,
    require_array,
    require_bool,
    require_string,
    validate_json,
    validate_json_object,
)
from .api import analyze
from .config import ConfigurationError, catalog
from .inputs import File, InputError, Project, Source
from .inputs.targets import Target
from .request import resolve_request
from .results.queries import QueryInput, ValueQuery
from .session import AnalysisSession


def _optional_string(value: JsonValue) -> str | None:
    return None if value is None else require_string(value)


def _optional_strings(value: JsonValue) -> tuple[str, ...] | None:
    return None if value is None else tuple(require_string(item) for item in require_array(value))


@dataclass(frozen=True, kw_only=True)
class CliArguments:
    command: str
    config_command: str | None
    code: str | None
    stdin: bool
    file: str | None
    project: str | None
    filename: str
    module: str | None
    entries: tuple[str, ...]
    config: str | None
    sets: tuple[str, ...]
    queries: tuple[str, ...] | None
    views: tuple[str, ...] | None
    format: str | None
    output: str | None
    require_converged: bool
    require_covered: bool
    require_verified: bool

    @classmethod
    def from_namespace(cls, namespace: argparse.Namespace) -> CliArguments:
        """Validate the external parser's dynamic namespace at one boundary."""
        data = validate_json_object(vars(namespace))
        return cls(
            command=require_string(data["command"]),
            config_command=_optional_string(data.get("config_command")),
            code=_optional_string(data.get("code")),
            stdin=require_bool(data.get("stdin", False)),
            file=_optional_string(data.get("file")),
            project=_optional_string(data.get("project")),
            filename=require_string(data.get("filename", "snippet.py")),
            module=_optional_string(data.get("module")),
            entries=_optional_strings(data.get("entry", [])) or (),
            config=_optional_string(data.get("config")),
            sets=_optional_strings(data.get("sets", [])) or (),
            queries=_optional_strings(data.get("query")),
            views=_optional_strings(data.get("view")),
            format=_optional_string(data.get("format")),
            output=_optional_string(data.get("output")),
            require_converged=require_bool(data.get("require_converged", False)),
            require_covered=require_bool(data.get("require_covered", False)),
            require_verified=require_bool(data.get("require_verified", False)),
        )


def _target_options(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
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


def _target(args: CliArguments) -> Target:
    if args.code is not None or args.stdin:
        code = sys.stdin.read() if args.stdin else args.code
        if code is None:
            raise InputError("Memory source requires code or stdin")
        return Source(code, filename=args.filename, module=args.module or Path(args.filename).stem)
    if args.file is not None:
        return File(args.file, module=args.module)
    if args.project is not None:
        return Project(args.project, entries=args.entries)
    return Source("")


def _override_value(text: str) -> JsonValue:
    try:
        value: object = json.loads(text)
    except json.JSONDecodeError:
        try:
            value = yaml.safe_load(text)
        except yaml.YAMLError as error:
            raise ConfigurationError("Invalid --set value") from error
    try:
        return validate_json(value)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(str(error)) from error


def _overrides(args: CliArguments) -> JsonObject:
    result: JsonObject = {}
    for item in args.sets:
        if "=" not in item:
            raise ConfigurationError("--set requires path=value")
        key, text = item.split("=", 1)
        if key in result:
            raise ConfigurationError(f"Duplicate override: {key}")
        result[key] = _override_value(text)
    if args.entries and args.project is None:
        result["inputs.entries"] = list(args.entries)
    return result


def _query(value: str) -> QueryInput:
    if value.startswith("value:"):
        parts = value.split(":")
        if len(parts) not in {3, 4}:
            raise ConfigurationError("Value query is value:module:symbol[:point]")
        return ValueQuery(parts[1], parts[2], parts[3] if len(parts) == 4 else "module_exit")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pythonstan",
        description="Static Python analysis with explicit source and entry contracts",
    )
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
    subcommands = config.add_subparsers(dest="config_command", required=True)
    _target_options(subcommands.add_parser("show-effective"), required=False)
    subcommands.add_parser("list-options")
    plan = commands.add_parser("plan")
    _target_options(plan)
    return parser


def _analyze(args: CliArguments, target: Target, overrides: JsonObject) -> int:
    queries = tuple(_query(query) for query in args.queries) if args.queries is not None else None
    if args.views is not None:
        overrides["output.views"] = list(args.views)
    elif args.queries is not None:
        views: list[JsonValue] = ["summary"]
        for query in args.queries:
            if not query.startswith(("value:", "property:")) and query not in views:
                views.append(query)
        if any(query.startswith("property:") for query in args.queries):
            views.append("proof")
        overrides["output.views"] = views
    if args.format is not None:
        overrides["output.format"] = args.format
    result = analyze(target, config=args.config, overrides=overrides, queries=queries)
    selected_format = args.format or result.output.format
    if args.output is not None:
        result.write(args.output, format=selected_format)
    else:
        sys.stdout.write(result.serialize(format=selected_format))
    if args.require_converged and result.solver_status != "converged":
        return 4
    if args.require_covered and result.coverage_status != "closed_under_profile":
        return 4
    if args.require_verified and result.proof_status != "verified":
        return 4
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parsed = parser.parse_args(argv)
    try:
        args = CliArguments.from_namespace(parsed)
        if args.command == "config" and args.config_command == "list-options":
            sys.stdout.write(json.dumps(catalog(), ensure_ascii=False, indent=2) + "\n")
            return 0
        target, overrides = _target(args), _overrides(args)
        if args.command == "config":
            request = resolve_request(target, config=args.config, overrides=overrides)
            config = request.config
            sys.stdout.write(
                json.dumps(
                    {
                        "effective_config": config.to_dict(),
                        "provenance": config.provenance,
                        "config_file": config.config_file,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
            return 0
        if args.command == "plan":
            request = resolve_request(
                target, config=args.config, overrides=overrides, queries=["plan"]
            )
            with AnalysisSession(request) as session:
                plan = session.prepare().plan
                if plan is None:
                    raise RuntimeError("Plan query did not produce an execution plan")
                data = plan.to_dict()
            sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            return 0
        return _analyze(args, target, overrides)
    except (InputError, ConfigurationError, OSError) as error:
        sys.stderr.write(f"pythonstan: {error}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("pythonstan: cancelled\n")
        return 130
    except Exception as error:
        sys.stderr.write(f"pythonstan internal failure: {type(error).__name__}: {error}\n")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
