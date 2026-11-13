"""Child-only Native analysis with a checked JSON process boundary."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import logging
import math
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeGuard, override

from pythonstan.analysis.contracts.cost_model import COST_MODEL_DIGEST, COST_MODEL_VERSION
from pythonstan.analysis.contracts.identity import EntryScenario, SourceModule
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    JsonScalar,
    JsonValue,
    freeze_json,
    require_array,
    require_bool,
    require_int,
    require_number,
    require_object,
    require_string,
    validate_json,
    validate_json_object,
)
from pythonstan.analysis.contracts.wire import thaw_snapshot, value_snapshot

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject

type ProgressPublisher = Callable[[JsonObject], None]


@dataclass(frozen=True, slots=True)
class WorkerHeader:
    document: JsonObject
    config: JsonObject
    engine: str
    max_work: int
    max_memory_mb: float

    @classmethod
    def from_wire(cls, payload: object) -> WorkerHeader:
        document = validate_json_object(payload)
        model = document.get("cost_model")
        if model is not None and model != {
            "version": COST_MODEL_VERSION,
            "digest": COST_MODEL_DIGEST,
        }:
            raise ValueError("Native worker cost model disagrees with its supervisor")
        config = require_object(document["config"])
        resources = require_object(config["resources"])
        return cls(
            document,
            config,
            require_string(config["engine"]),
            require_int(resources["max_work"]),
            require_number(resources.get("max_memory_mb", 0)),
        )


def _optional_string(value: JsonValue) -> str | None:
    return None if value is None else require_string(value)


def _source_module(value: JsonValue) -> SourceModule:
    document = require_object(value)
    allowed = {
        "module",
        "filename",
        "source",
        "digest",
        "is_package",
        "original_bytes_digest",
        "encoding",
        "import_root_digest",
    }
    if unknown := document.keys() - allowed:
        raise TypeError(f"Unknown SourceModule fields: {sorted(unknown)}")
    return SourceModule(
        module=require_string(document["module"]),
        filename=require_string(document["filename"]),
        source=require_string(document["source"]),
        digest=require_string(document.get("digest", "")),
        is_package=require_bool(document.get("is_package", False)),
        original_bytes_digest=_optional_string(document.get("original_bytes_digest")),
        encoding=require_string(document.get("encoding", "utf-8")),
        import_root_digest=require_string(document.get("import_root_digest", "")),
    )


@dataclass(frozen=True, slots=True)
class WorkerRequest:
    header: WorkerHeader
    modules: Mapping[str, SourceModule]
    scenarios: tuple[EntryScenario, ...]
    native_options: JsonObject
    input_bindings: JsonObject
    value_symbols: Mapping[str, tuple[str, ...]]

    @classmethod
    def from_header(cls, header: WorkerHeader) -> WorkerRequest:
        modules: dict[str, SourceModule] = {}
        for row in require_array(header.document["modules"]):
            source = _source_module(row)
            modules[source.module] = source
        scenarios: list[EntryScenario] = []
        for row in require_array(header.document["scenarios"]):
            scenario = require_object(row)
            scenarios.append(
                EntryScenario(
                    root_module=require_string(scenario["root_module"]),
                    entry_qualname=_optional_string(scenario["entry_qualname"]),
                    arguments=tuple(
                        freeze_json(value) for value in require_array(scenario.get("arguments", []))
                    ),
                    scenario_id=require_string(scenario["scenario_id"]),
                )
            )
        value_symbols: dict[str, list[str]] = {}
        for row in require_array(header.config["queries"]):
            if not isinstance(row, dict) or row.get("kind") != "value":
                continue
            module, symbol = row.get("module"), row.get("symbol")
            if isinstance(module, str) and isinstance(symbol, str) and symbol:
                value_symbols.setdefault(module, []).append(symbol)
        return cls(
            header,
            modules,
            tuple(scenarios),
            require_object(header.config["andersen"]),
            require_object(require_object(header.config["inputs"])["bindings"]),
            {module: tuple(symbols) for module, symbols in value_symbols.items()},
        )


class _Names(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    @override
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    @override
    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(name.asname or name.name.split(".")[0] for name in node.names)

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(name.asname or name.name for name in node.names if name.name != "*")


def _names(source: str) -> set[str]:
    visitor = _Names()
    visitor.visit(ast.parse(source))
    return visitor.names


def _is_scalar(value: object) -> TypeGuard[JsonScalar]:
    return value is None or type(value) in (str, bool, int, float)


def _constant_sort_key(value: JsonScalar) -> tuple[str, str]:
    return type(value).__name__, repr(value)


def _value(objects: Iterable[AbstractObject]) -> JsonObject:
    from pythonstan.analysis.pointer.kcfa.object import AllocKind, ConstantObject

    constants: list[JsonScalar] = []
    identities: list[str] = []
    types: set[str] = set()
    top = False
    top_sorts: set[str] = set()
    for obj in objects:
        kind = obj.kind.value
        if obj.kind is AllocKind.UNKNOWN:
            top = True
        literal: object = obj.value if isinstance(obj, ConstantObject) else obj
        if isinstance(obj, ConstantObject) and _is_scalar(literal):
            if isinstance(literal, float) and not math.isfinite(literal):
                top_sorts.add("float")
                types.add("float")
                continue
            if not any(type(value) is type(literal) and value == literal for value in constants):
                constants.append(literal)
            types.add(type(literal).__name__)
        else:
            identities.append(hashlib.sha256(str(obj).encode()).hexdigest()[:24])
            types.add(kind)
    ordered_constants = sorted(constants, key=_constant_sort_key)
    result = require_object(
        thaw_snapshot(
            value_snapshot(
                constants=ordered_constants,
                objects=sorted(set(identities)),
                types=sorted(types),
                top=top,
            )
        )
    )
    if top_sorts:
        result["top_sorts"] = list[JsonValue](sorted(top_sorts))
        result["bottom"] = False
    return result


def _object_field(document: JsonObject, key: str) -> JsonObject:
    return require_object(document.setdefault(key, {}))


def _array_field(document: JsonObject, key: str) -> list[JsonValue]:
    return require_array(document.setdefault(key, []))


def _numeric(value: JsonValue) -> int | float:
    if not isinstance(value, (int, float)):
        raise TypeError("Native metrics must contain numeric counters")
    return value


def _increment(document: JsonObject, key: str, amount: JsonValue) -> None:
    document[key] = _numeric(document.get(key, 0)) + _numeric(amount)


def _logging_level(name: str) -> int:
    level = logging.getLevelNamesMapping().get(name)
    if level is None:
        raise AttributeError(f"module 'logging' has no attribute {name!r}")
    return level


def _legacy_residual(record: Mapping[str, object]) -> JsonObject:
    """Retain the existing top-level residual conversion at the process edge."""
    result: JsonObject = {}
    for key, value in record.items():
        result[str(key)] = (
            validate_json(value)
            if type(value) in (str, bool, int, float, type(None), list, dict)
            else str(value)
        )
    return result


def _strings(value: JsonValue) -> list[str]:
    return [require_string(item) for item in require_array(value)]


def _merge_values(previous: JsonObject, current: JsonObject) -> None:
    typed = {
        (type(value).__name__, json.dumps(value, sort_keys=True)): value
        for value in require_array(previous["constants"]) + require_array(current["constants"])
    }
    current["constants"] = [typed[key] for key in sorted(typed)]
    tagged = {
        (
            require_string(require_object(value)["type"]),
            json.dumps(require_object(value)["value"], sort_keys=True),
        ): value
        for value in require_array(previous["tagged_constants"])
        + require_array(current["tagged_constants"])
    }
    current["tagged_constants"] = [tagged[key] for key in sorted(tagged)]
    current["objects"] = list[JsonValue](
        sorted(set(_strings(previous["objects"]) + _strings(current["objects"])))
    )
    current["types"] = list[JsonValue](
        sorted(set(_strings(previous["types"]) + _strings(current["types"])))
    )
    current["top"] = require_bool(previous["top"]) or require_bool(current["top"])
    current["bottom"] = require_bool(previous["bottom"]) and require_bool(current["bottom"])


def _zero_work(engine: str) -> JsonObject:
    return {
        "engine": engine,
        "solver_status": "budget_exhausted",
        "coverage_status": "open",
        "values": {},
        "calls": [],
        "returns": {},
        "effects": {},
        "outcomes": {},
        "stats": {"native_work": 0},
        "obligations": [{"kind": "work_budget", "pending": True}],
        "phase_trace": [{"phase": "native", "work": 0, "status": "budget_exhausted"}],
    }


def _call_key(row: JsonObject) -> tuple[str, str, str]:
    return require_string(row["caller"]), require_string(row["callee"]), require_string(row["site"])


def main(payload: object, publish: ProgressPublisher | None = None) -> JsonObject:
    header = WorkerHeader.from_wire(payload)
    if header.max_work == 0:
        return _zero_work(header.engine)
    if header.max_memory_mb:
        import resource

        cap = int(header.max_memory_mb * 1024 * 1024)
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    request = WorkerRequest.from_header(header)
    from pythonstan.analysis.pointer.kcfa.analysis import AnalysisResult
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.compat.native_services import configure_native_services
    from pythonstan.ir import IRModule
    from pythonstan.world.pipeline import Pipeline

    aggregate: JsonObject = {
        "engine": header.engine,
        "solver_status": "converged",
        "coverage_status": "open",
        "values": {},
        "returns": {},
        "outcomes": {},
        "calls": [],
        "effects": {},
        "obligations": [
            {
                "kind": "legacy_source_coverage",
                "reason": "Native constraint convergence does not establish exhaustive Python source coverage",
            }
        ],
        "diagnostics": [],
        "stats": {"scenarios": 0, "native_work": 0},
        "phase_trace": [],
    }
    metrics = _object_field(aggregate, "stats")
    with tempfile.TemporaryDirectory(prefix="pythonstan-legacy-") as directory:
        root = Path(directory)
        paths: dict[str, Path] = {}
        for name, module in request.modules.items():
            path = root.joinpath(*name.split("."))
            path = path / "__init__.py" if module.is_package else path.with_suffix(".py")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(module.source, encoding="utf-8")
            paths[name] = path
        for scenario in request.scenarios:
            remaining_work = header.max_work - require_int(metrics.get("global_work_used", 0))
            if remaining_work <= 0:
                aggregate["solver_status"] = "budget_exhausted"
                _array_field(aggregate, "obligations").append(
                    {"kind": "work_budget", "stage": "native", "pending_scenarios": True}
                )
                break
            native = dict(request.native_options)
            native["entry_points"] = (
                [scenario.root_module + "." + scenario.entry_qualname]
                if scenario.entry_qualname
                else []
            )
            native["max_iterations"] = min(require_int(native["max_iterations"]), remaining_work)
            native["project_path"] = str(root)
            logging.basicConfig(
                level=_logging_level(require_string(native["log_level"])),
                stream=sys.stderr,
                format="%(levelname)s: %(message)s",
                force=True,
            )
            native = Config.from_dict(native).to_dict()
            analysis: list[JsonValue] = [
                {"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"], "options": native}
            ]
            started = time.monotonic()
            pipeline = Pipeline(
                config={
                    "filename": str(paths[scenario.root_module]),
                    "project_path": str(root),
                    "library_paths": [],
                    "import_level": -1,
                    "mock_libs": False,
                    "time_count": False,
                    "analysis": analysis,
                },
                source_snapshots=request.modules,
            )
            source_metadata = {
                name: {
                    "__file__": module.filename,
                    "__name__": name,
                    "__package__": name if module.is_package else name.rpartition(".")[0],
                }
                for name, module in request.modules.items()
            }
            configure_native_services(
                pipeline.analysis_manager,
                max_work=remaining_work,
                source_metadata=source_metadata,
                input_bindings=request.input_bindings,
            )
            pipeline.run()
            native_result = pipeline.analysis_manager.get_results("pta")
            if not isinstance(native_result, AnalysisResult):
                raise TypeError("Native pipeline must return its actual AnalysisResult")
            query = native_result.query()
            stats = validate_json_object(query.get_statistics())
            native_seconds = time.monotonic() - started
            if require_bool(native["enable_synthetic_method_contexts"]):
                contract = require_object(
                    aggregate.setdefault(
                        "synthetic_entry_contract",
                        {
                            "enabled": True,
                            "entry_coverage": "initialization_and_synthetic_method_contexts",
                            "coverage": "candidate_method_discovery_not_arbitrary_external_requests",
                            "roots": [],
                        },
                    )
                )
                roots = _array_field(contract, "roots")
                roots.extend(
                    {**require_object(row), "scenario_id": scenario.scenario_id}
                    for row in require_array(stats.get("synthetic_method_roots", []))
                )
                metrics["synthetic_method_root_count"] = len(roots)
            _increment(metrics, "scenarios", 1)
            _increment(
                metrics, "native_work", stats.get("selected_work_items", stats.get("iterations", 0))
            )
            for name in (
                "global_work_used",
                "ai_intrinsic_work",
                "intrinsic_bridge_work",
                "native_selected_work",
                "ai_intrinsic_seconds",
                "input_publications",
            ):
                _increment(metrics, name, stats.get(name, 0))
            events = query.get_unknown_effect_events()
            effects = _object_field(aggregate, "effects")
            for key, kind in (
                ("havoc", "heap_havoc"),
                ("calls", "unknown_callback"),
                ("exceptions", "unknown_exception"),
            ):
                _array_field(effects, key).extend(
                    event for event in events if event["kind"] == kind
                )
            _array_field(aggregate, "phase_trace").append(
                {
                    "phase": "native",
                    "scenario": scenario.scenario_id,
                    "seconds": native_seconds,
                    "work": stats.get("global_work_used", stats.get("iterations", 0)),
                    "native_selected_work": stats.get("native_selected_work", 0),
                    "ai_intrinsic_work": stats.get("ai_intrinsic_work", 0),
                    "intrinsic_bridge_work": stats.get("intrinsic_bridge_work", 0),
                    "ai_intrinsic_seconds_subset": stats.get("ai_intrinsic_seconds", 0),
                }
            )
            if not stats.get("converged"):
                aggregate["solver_status"] = "budget_exhausted"
            for item in query.get_residual_effects():
                _array_field(aggregate, "obligations").append(_legacy_residual(item))
            for scope in query.get_reachable_scopes():
                if not isinstance(scope, IRModule):
                    continue
                name = scope.get_qualname()
                if name not in request.modules:
                    continue
                values = _object_field(_object_field(aggregate, "values"), name)
                symbols = _names(request.modules[name].source)
                symbols.update(
                    binding.rpartition(".")[2]
                    for binding in request.input_bindings
                    if binding.rpartition(".")[0] == name
                )
                symbols.update(request.value_symbols.get(name, ()))
                for symbol in sorted(symbols):
                    points = query.points_to_at(symbol, scope)
                    if not points and not stats.get("converged"):
                        _array_field(aggregate, "obligations").append(
                            {
                                "kind": "native_pending_value",
                                "module": name,
                                "symbol": symbol,
                                "reason": "Empty points-to observation before fixed point is unavailable, not proved Bottom",
                            }
                        )
                        continue
                    value = _value(points)
                    if symbol in values:
                        _merge_values(require_object(values[symbol]), value)
                    values[symbol] = value
            for edge in query.call_graph().get_edges():
                site = edge.get_callsite()
                if site.scope is None:
                    raise ValueError("A Native call edge must have its caller scope")
                _array_field(aggregate, "calls").append(
                    {
                        "caller": site.scope.stmt.get_qualname(),
                        "callee": edge.get_callee().stmt.get_qualname(),
                        "site": str(site.content.statement),
                    }
                )
            metrics["total_work"] = metrics.get("global_work_used", 0)
            if publish is not None:
                publish(aggregate)
    calls = {
        json.dumps(row, sort_keys=True): require_object(row)
        for row in _array_field(aggregate, "calls")
    }
    ordered_calls: list[JsonObject] = sorted(calls.values(), key=_call_key)
    aggregate["calls"] = list[JsonValue](ordered_calls)
    return aggregate


def _worker_frame(kind: Literal["progress", "result"], result: JsonObject) -> JsonObject:
    """Keep child memory/model observations outside semantic result data."""
    import resource

    return {
        "kind": kind,
        "result": result,
        "cost_model": {"version": COST_MODEL_VERSION, "digest": COST_MODEL_DIGEST},
        "peak_resident_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024),
    }


def _publish_progress(partial: JsonObject) -> None:
    stream = sys.__stdout__
    if stream is None:
        raise RuntimeError("Native process stdout is unavailable")
    stream.write(json.dumps(_worker_frame("progress", partial), allow_nan=False) + "\n")
    stream.flush()


def _run_cli() -> None:
    incoming: object = json.load(sys.stdin)
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = main(incoming, publish=_publish_progress)
        if log.getvalue():
            _array_field(result, "diagnostics").append(
                {
                    "kind": "legacy_log",
                    "message": log.getvalue()[-2000:],
                    "truncated": len(log.getvalue()) > 2000,
                    "total_characters": len(log.getvalue()),
                }
            )
    except Exception:
        result = {"error": traceback.format_exc(), "captured_log": log.getvalue()[-2000:]}
    sys.stdout.write(json.dumps(_worker_frame("result", result), allow_nan=False) + "\n")


if __name__ == "__main__":
    _run_cli()
