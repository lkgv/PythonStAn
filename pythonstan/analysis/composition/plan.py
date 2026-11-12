"""Source-bound region selection and conservative recursive component routing."""

from __future__ import annotations

import ast
import fnmatch
import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal, override

from pythonstan.analysis.contracts.identity import ProgramPointId, SourceModule, stable_digest
from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    freeze_json,
    freeze_json_object,
    require_array,
    require_int,
    require_object,
    require_string,
    thaw_json,
    thaw_json_object,
)
from pythonstan.config import ConfigurationError, EffectiveConfig, resolve_config
from pythonstan.frontend.source import source_node_identity as node_identity
from pythonstan.inputs import SourceSnapshot

type RegionEngineName = Literal["native", "ai_primary"]
type SourceRegionKind = Literal["function", "class", "loop", "block"]


@dataclass(frozen=True)
class SourceRegion:
    module: str
    source_digest: str
    qualname: str
    scope_qualname: str
    node_id: ProgramPointId
    kind: SourceRegionKind
    span: tuple[int, int, int | None, int | None]

    def to_dict(self) -> JsonObject:
        return {
            "module": self.module,
            "source_digest": self.source_digest,
            "qualname": self.qualname,
            "scope_qualname": self.scope_qualname,
            "node_id": self.node_id,
            "kind": self.kind,
            "span": list(self.span),
        }


@dataclass(frozen=True, eq=False)
class RegionDecision(Mapping[str, JsonValue]):
    source: SourceRegion
    engine: RegionEngineName
    abstract: FrozenJsonObject
    origin: FrozenJsonValue
    region_id: str
    boundary_profile: str
    priority: int | None = None

    def to_dict(self) -> JsonObject:
        result = self.source.to_dict()
        result.update(
            engine=self.engine,
            abstract=thaw_json_object(self.abstract),
            origin=thaw_json(self.origin),
            region_id=self.region_id,
            boundary_profile=self.boundary_profile,
        )
        if self.priority is not None:
            result["priority"] = self.priority
        return result

    @override
    def __getitem__(self, key: str) -> JsonValue:
        return self.to_dict()[key]

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    @override
    def __len__(self) -> int:
        return len(self.to_dict())


@dataclass(frozen=True)
class _SourceRow:
    source: SourceRegion
    node: ast.stmt


def _nested_bodies(node: ast.stmt) -> tuple[Sequence[ast.stmt], ...]:
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.If)):
        return node.body, node.orelse
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return (node.body,)
    if isinstance(node, (ast.Try, ast.TryStar)):
        return node.body, node.orelse, node.finalbody, *(handler.body for handler in node.handlers)
    return ()


def _inventory(snapshot: SourceSnapshot) -> tuple[list[_SourceRow], dict[str, _SourceRow]]:
    rows: list[_SourceRow] = []
    functions: dict[str, _SourceRow] = {}
    for module in snapshot.modules:
        tree = ast.parse(module.source, module.filename)

        def walk(
            nodes: Sequence[ast.stmt], qualname: str, source_module: SourceModule = module
        ) -> None:
            for node in nodes:
                kind: SourceRegionKind
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind, actual = "function", f"{qualname}.{node.name}"
                elif isinstance(node, ast.ClassDef):
                    kind, actual = "class", f"{qualname}.{node.name}"
                else:
                    kind = (
                        "loop" if isinstance(node, (ast.For, ast.AsyncFor, ast.While)) else "block"
                    )
                    actual = qualname
                source = SourceRegion(
                    source_module.module,
                    source_module.digest,
                    actual,
                    qualname,
                    node_identity(source_module.module, source_module.digest, node),
                    kind,
                    (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset),
                )
                row = _SourceRow(source, node)
                rows.append(row)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[actual] = row
                    walk(node.body, actual)
                elif isinstance(node, ast.ClassDef):
                    walk(node.body, actual)
                else:
                    for body in _nested_bodies(node):
                        walk(body, qualname)

        walk(tree.body, module.module)
    return rows, functions


def _scc(functions: Mapping[str, _SourceRow]) -> tuple[list[list[str]], dict[str, set[str]]]:
    graph: dict[str, set[str]] = {name: set() for name in functions}
    builtin_names = {
        "str",
        "int",
        "bool",
        "float",
        "len",
        "range",
        "dict",
        "list",
        "tuple",
        "set",
        "print",
        "super",
        "object",
        "next",
        "iter",
        "isinstance",
        "issubclass",
        "getattr",
        "setattr",
        "hasattr",
        "delattr",
    }
    for name, row in functions.items():
        for call in (node for node in ast.walk(row.node) if isinstance(node, ast.Call)):
            if isinstance(call.func, ast.Name):
                short = call.func.id
                candidates = {key for key in functions if key.rsplit(".", 1)[-1] == short}
                if candidates:
                    graph[name].update(candidates)
                elif short not in builtin_names:
                    graph[name].update(functions)
            elif isinstance(call.func, ast.Attribute):
                attribute = call.func.attr
                candidates = {key for key in functions if key.rsplit(".", 1)[-1] == attribute}
                graph[name].update(candidates or functions)
    return _strong_components(graph), graph


def _strong_components(graph: Mapping[str, set[str]]) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    onstack: set[str] = set()
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    groups: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = low[node] = index
        index += 1
        stack.append(node)
        onstack.add(node)
        for child in graph[node]:
            if child not in indices:
                visit(child)
                low[node] = min(low[node], low[child])
            elif child in onstack:
                low[node] = min(low[node], indices[child])
        if low[node] == indices[node]:
            group: list[str] = []
            while True:
                child = stack.pop()
                onstack.remove(child)
                group.append(child)
                if child == node:
                    break
            groups.append(sorted(group))

    for name in graph:
        if name not in indices:
            visit(name)
    return sorted(groups)


@dataclass(frozen=True)
class _Selector:
    module: str
    qualname: str
    kind: JsonValue
    node_id: JsonValue
    source_digest: JsonValue
    first_line: int | None
    last_line: int | None

    @classmethod
    def parse(cls, value: JsonValue) -> _Selector:
        raw = require_object(value)
        allowed = {
            "module",
            "qualname",
            "kind",
            "node_id",
            "source_digest",
            "line_start",
            "line_end",
            "span",
        }
        if not raw or set(raw) - allowed:
            raise ConfigurationError("Region selector must use registered source identity fields")
        start = raw.get("line_start")
        end = raw.get("line_end", start)
        if "span" in raw:
            span = require_array(raw["span"])
            if len(span) != 2:
                raise ConfigurationError("Selector span is [first_line,last_line]")
            start, end = span
        first: int | None = None
        last: int | None = None
        if start is not None:
            first, last = require_int(start), require_int(end)
            if first < 1 or last < first:
                raise ConfigurationError("Invalid selector line range")
        return cls(
            require_string(raw.get("module", "*")),
            require_string(raw.get("qualname", "*")),
            raw.get("kind"),
            raw.get("node_id"),
            raw.get("source_digest"),
            first,
            last,
        )

    def matches(self, row: _SourceRow) -> bool:
        source = row.source
        if not fnmatch.fnmatch(source.module, self.module) or not fnmatch.fnmatch(
            source.qualname, self.qualname
        ):
            return False
        if self.kind not in (None, source.kind) or self.node_id not in (None, source.node_id):
            return False
        return (
            self.first_line is None
            or self.last_line is not None
            and self.first_line <= source.span[0] <= self.last_line
        )


@dataclass(frozen=True)
class _PreparedRule:
    ordinal: int
    engine: RegionEngineName
    abstract: FrozenJsonObject
    priority: int
    origin: FrozenJsonValue
    matched: tuple[_SourceRow, ...]


def _prepare_rules(
    snapshot: SourceSnapshot, rows: Sequence[_SourceRow], rules: JsonValue
) -> list[_PreparedRule]:
    prepared: list[_PreparedRule] = []
    allowed = {"selector", "engine", "abstract", "priority", "name"}
    for ordinal, value in enumerate(require_array(rules)):
        rule = require_object(value)
        if set(rule) - allowed:
            raise ConfigurationError("Unknown region rule fields")
        selector = _Selector.parse(rule.get("selector"))
        engine = rule.get("engine", "ai_primary")
        if engine not in ("native", "ai_primary"):
            raise ConfigurationError("Region engines are native or ai_primary")
        actual_engine: RegionEngineName = "native" if engine == "native" else "ai_primary"
        priority = require_int(rule.get("priority", 0))
        abstract = require_object(rule.get("abstract", {}))
        resolve_config(config={"engine": "ai_primary", "abstract": abstract})
        for module in snapshot.modules:
            if fnmatch.fnmatch(module.module, selector.module) and selector.source_digest not in (
                None,
                module.digest,
            ):
                raise ConfigurationError("Stale source digest in region selector")
        matched = tuple(row for row in rows if selector.matches(row))
        if not matched:
            raise ConfigurationError(
                f"Region selector matched no current source: {rule['selector']}"
            )
        prepared.append(
            _PreparedRule(
                ordinal,
                actual_engine,
                freeze_json_object(abstract),
                priority,
                freeze_json(rule.get("name", f"rule:{ordinal}")),
                matched,
            )
        )
    return prepared


def _descendants(selected: _SourceRow, rows: Sequence[_SourceRow]) -> list[_SourceRow]:
    if selected.source.kind not in ("function", "class", "loop"):
        return [selected]
    ids = {id(node) for node in ast.walk(selected.node) if isinstance(node, ast.stmt)}
    return [
        row for row in rows if id(row.node) in ids and row.source.node_id != selected.source.node_id
    ]


def _decisions(
    rows: Sequence[_SourceRow], rules: Sequence[_PreparedRule], default_engine: RegionEngineName
) -> dict[ProgramPointId, RegionDecision]:
    decisions = {
        row.source.node_id: RegionDecision(
            row.source,
            default_engine,
            freeze_json_object({}),
            "default",
            stable_digest(
                {
                    "source": row.source.source_digest,
                    "node": row.source.node_id,
                    "qualname": row.source.qualname,
                }
            ),
            "python-core-v1",
        )
        for row in rows
    }
    selected_priority: dict[ProgramPointId, int] = {}
    for rule in rules:
        targets = {
            item.source.node_id: item for row in rule.matched for item in _descendants(row, rows)
        }
        for node_id in targets:
            current = decisions[node_id]
            if selected_priority.get(node_id) == rule.priority and (
                current.engine != rule.engine or current.abstract != rule.abstract
            ):
                raise ConfigurationError(f"Equal-priority region conflict at {node_id}")
            if rule.priority < selected_priority.get(node_id, -(10**9)):
                continue
            decisions[node_id] = replace(
                current,
                engine=rule.engine,
                abstract=rule.abstract,
                origin=rule.origin,
                priority=rule.priority,
                boundary_profile="native-straightline-v1"
                if rule.engine == "native"
                else "python-core-v1",
            )
            selected_priority[node_id] = rule.priority
    return decisions


def _validate_components(
    components: Sequence[Sequence[str]], decisions: Mapping[ProgramPointId, RegionDecision]
) -> None:
    for group in components:
        strategies = {
            json.dumps(
                {"engine": decision.engine, "abstract": thaw_json_object(decision.abstract)},
                sort_keys=True,
            )
            for name in group
            for decision in decisions.values()
            if decision.source.scope_qualname == name
        }
        if len(group) > 1 and len(strategies) > 1:
            raise ConfigurationError(
                f"Heterogeneous recursive SCC requires an unavailable joint adapter: {list(group)}"
            )


@dataclass(frozen=True, init=False)
class EffectiveRegionPlan:
    source_digest: str
    policy_digest: str
    _data: FrozenJsonObject
    _decisions: Mapping[ProgramPointId, RegionDecision]

    def __init__(
        self,
        source_digest: str,
        policy_digest: str,
        data: JsonObject,
        decisions: Mapping[ProgramPointId, RegionDecision],
    ) -> None:
        object.__setattr__(self, "source_digest", source_digest)
        object.__setattr__(self, "policy_digest", policy_digest)
        object.__setattr__(self, "_data", freeze_json_object(data))
        object.__setattr__(self, "_decisions", MappingProxyType(dict(decisions)))

    def to_dict(self) -> JsonObject:
        return thaw_json_object(self._data)

    def decision(self, module: str, qualname: str, node_id: ProgramPointId) -> RegionDecision:
        value = self._decisions.get(node_id)
        if value is None:
            raise ConfigurationError(f"No planned statement: {node_id}")
        if value.source.module != module:
            raise ConfigurationError("Region module identity mismatch")
        return value

    @property
    def decisions(self) -> Mapping[ProgramPointId, RegionDecision]:
        return self._decisions


def build_plan(
    snapshot: SourceSnapshot, config: JsonObject | EffectiveConfig
) -> EffectiveRegionPlan:
    """Compile validated source selectors into actual immutable execution decisions."""
    options = config.to_dict() if isinstance(config, EffectiveConfig) else config
    try:
        rows, functions = _inventory(snapshot)
        components, graph = _scc(functions)
        rules = _prepare_rules(snapshot, rows, options["regions"])
        default_engine: RegionEngineName = (
            "native" if options["engine"] in ("native", "native_then_ai") else "ai_primary"
        )
        decisions = _decisions(rows, rules, default_engine)
        _validate_components(components, decisions)
        data: JsonObject = {
            "schema_version": 1,
            "source_digest": snapshot.source_digest,
            "policy_digest": stable_digest(options),
            "decisions": {key: value.to_dict() for key, value in decisions.items()},
            "sccs": [list(group) for group in components],
            "call_dependencies": {
                key: list[JsonValue](sorted(value)) for key, value in graph.items()
            },
            "entry_contract": snapshot.entry_contract,
            "default_engine": default_engine,
        }
        return EffectiveRegionPlan(snapshot.source_digest, stable_digest(options), data, decisions)
    except (TypeError, ValueError) as error:
        if isinstance(error, ConfigurationError):
            raise
        raise ConfigurationError(str(error)) from error
