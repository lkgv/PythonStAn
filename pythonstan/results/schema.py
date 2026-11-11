"""Frozen query answers and output projections independent of solver objects."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import copy
import json
import fnmatch

from pythonstan.analysis.contracts.wire import freeze_snapshot, thaw_snapshot

VIEWS = ("summary", "calls", "values", "returns", "effects", "diagnostics", "stats", "proof", "obligations", "outcomes", "relations", "plan", "events")


@dataclass(frozen=True)
class ValueQuery:
    module: str
    symbol: str
    point: str = "module_exit"

    def __post_init__(self):
        if self.point not in ("module_exit", "function_exit"):
            raise ValueError("ValueQuery.point must be module_exit or function_exit")
        if not self.module or not self.symbol: raise ValueError("ValueQuery needs module and symbol")

    def to_dict(self):
        return {"kind": "value", "module": self.module, "symbol": self.symbol, "point": self.point}


@dataclass(frozen=True)
class PropertyQuery:
    property: str
    point: str = "all"

    def __post_init__(self):
        if self.property != "guarded_dispatch_return_relation":
            raise ValueError("Unsupported property query")
        if not isinstance(self.point, str) or not self.point:
            raise ValueError("Property query requires a point or all")

    def to_dict(self): return {"kind": "property", "property": self.property, "point": self.point}


@dataclass(frozen=True)
class OutputSpec:
    views: tuple[str, ...] = ("summary",)
    modules: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    contexts: tuple[str, ...] = ()
    fields: tuple[str, ...] = ()
    max_items: int = 10000
    format: str = "json"

    def __post_init__(self):
        for field in ("views", "modules", "functions", "regions", "contexts", "fields"):
            value = tuple(getattr(self, field))
            if any(not isinstance(x, str) for x in value): raise ValueError(f"{field} must contain strings")
            object.__setattr__(self, field, value)
        if set(self.views) - set(VIEWS): raise ValueError(f"Unknown output views: {sorted(set(self.views)-set(VIEWS))}")
        if type(self.max_items) is not int or self.max_items < 0: raise ValueError("max_items must be nonnegative")
        if self.format not in ("json", "text"): raise ValueError("format must be json or text")

    def to_dict(self):
        return {name: list(getattr(self, name)) for name in ("views", "modules", "functions", "regions", "contexts", "fields")} | {"max_items": self.max_items, "format": self.format}


def normalize_query(query):
    if isinstance(query, ValueQuery): return query.to_dict()
    if isinstance(query, PropertyQuery): return query.to_dict()
    if isinstance(query, str):
        if query.startswith("property:"): return PropertyQuery(query.split(":", 1)[1]).to_dict()
        if query not in VIEWS: raise ValueError(f"Unknown query: {query}")
        return {"kind": query}
    if isinstance(query, Mapping):
        if query.get("kind") == "property":
            if set(query) - {"kind", "property", "point"}: raise ValueError("Unknown property query field")
            return PropertyQuery(query["property"], query.get("point", "all")).to_dict()
        if query.get("kind") in ("value", "return"):
            value = ValueQuery(query["module"], query["symbol"], query.get("point", "function_exit" if query["kind"] == "return" else "module_exit")).to_dict()
            return value
        if set(query) == {"kind"}: return normalize_query(query["kind"])
    raise ValueError("Query must be a registered view or ValueQuery")


def query_key(query):
    return json.dumps(normalize_query(query), sort_keys=True, separators=(",", ":"))


def _matches(value, patterns):
    return not patterns or any(fnmatch.fnmatch(str(value), pattern) for pattern in patterns)


def _module_for_qualname(name, snapshot):
    """Resolve nested functions/classes using the longest frozen module prefix."""
    modules = {row.get("module") for row in snapshot.get("inventory", ())
               if isinstance(row, Mapping) and isinstance(row.get("module"), str)}
    modules.update(snapshot.get("values", {}))
    candidates = [module for module in modules if name == module or name.startswith(module + ".")]
    return max(candidates, key=len) if candidates else name.rpartition(".")[0]


def _point_module(point):
    pieces = point.rsplit(":", 4) if isinstance(point, str) else ()
    return pieces[0] if len(pieces) == 5 else ""


def _context_matches(context, patterns):
    if not patterns:
        return True
    values = [context] if isinstance(context, str) else list(context or ())
    return (_matches(json.dumps(values, separators=(",", ":")), patterns)
            or any(_matches(value, patterns) for value in values))


def _region_matches(point, row, spec, snapshot):
    if not spec.regions:
        return True
    decision = snapshot.get("plan", {}).get("decisions", {}).get(point, {})
    return any(_matches(value, spec.regions) for value in
               (row.get("region_id", ""), decision.get("region_id", ""),
                decision.get("qualname", ""), point) if value)


def _record_matches(row, spec, snapshot, point=""):
    payload = row.get("payload", {})
    payload = payload if isinstance(payload, Mapping) else {}
    function = row.get("caller", row.get("qualname", payload.get("qualname", "")))
    source = row.get("source") or {}
    point = point or row.get("site", row.get("point", payload.get("point", "")))
    module = row.get("module", source.get("module", "")) or _point_module(point)
    module = module or _module_for_qualname(function, snapshot)
    if not _matches(module, spec.modules):
        return False
    if spec.functions and not (_matches(function, spec.functions)
                               or _matches(row.get("callee", ""), spec.functions)):
        return False
    if not _region_matches(point, row, spec, snapshot):
        return False
    return _context_matches(row.get("contexts", row.get("context", ())), spec.contexts)


def _fields(row, spec, required=()):
    if not spec.fields or not isinstance(row, dict):
        return row
    return {key: item for key, item in row.items() if key in spec.fields or key in required}


def _joined_unavailable(spec):
    if spec.contexts or spec.regions:
        return (None, 0, 0, False, "unavailable",
                "Context/region-specific value snapshots were not retained; joined values cannot be split. Use retained outcomes for context records.")
    return None


def _project_payload(view, value, spec, snapshot):
    """Select and bound retained records; never recompute semantic values."""
    if value is None:
        return None, 0, 0, False, None, None
    if view in ("values", "returns") and isinstance(value, dict):
        unavailable = _joined_unavailable(spec)
        if unavailable:
            return unavailable
        if view == "values":
            records = [(module, symbol, row) for module, values in sorted(value.items())
                       if _matches(module, spec.modules) for symbol, row in sorted(values.items())
                       if _matches(module + "." + symbol, spec.functions)]
            selected = {}
            for module, symbol, row in records[:spec.max_items]:
                selected.setdefault(module, {})[symbol] = _fields(row, spec, ("top", "bottom", "top_sorts"))
        else:
            records = [(function, row) for function, row in sorted(value.items())
                       if _matches(function, spec.functions)
                       and _matches(_module_for_qualname(function, snapshot), spec.modules)]
            selected = {function: _fields(row, spec, ("top", "bottom", "top_sorts"))
                        for function, row in records[:spec.max_items]}
        total = len(records)
        count = min(total, spec.max_items)
        return selected, total, count, count < total, None, None
    if view == "outcomes" and isinstance(value, dict):
        records = []
        for function, rows in sorted(value.items()):
            if not _matches(function, spec.functions) or not _matches(_module_for_qualname(function, snapshot), spec.modules):
                continue
            for row in rows:
                context = row.get("context", ())
                if not _context_matches(context, spec.contexts):
                    continue
                if spec.regions and not any(_region_matches(point, {}, spec, snapshot) for point in context):
                    continue
                records.append((function, row))
        selected = {}
        for function, row in records[:spec.max_items]:
            selected.setdefault(function, []).append(_fields(row, spec, ("kind", "exception", "context")))
        count = min(len(records), spec.max_items)
        return selected, len(records), count, count < len(records), None, None
    if view == "plan" and isinstance(value, dict):
        records = [(point, row) for point, row in sorted(value.get("decisions", {}).items())
                   if _record_matches(row, spec, snapshot, point)]
        selected = {key: item for key, item in value.items() if key != "decisions"}
        selected["decisions"] = {point: _fields(row, spec) for point, row in records[:spec.max_items]}
        count = min(len(records), spec.max_items)
        return selected, len(records), count, count < len(records), None, None
    if view == "events" and isinstance(value, dict):
        rows = [row for row in value.get("events", ()) if _record_matches(row, spec, snapshot)]
        selected = {key: item for key, item in value.items() if key != "events"}
        selected["events"] = [_fields(row, spec) for row in rows[:spec.max_items]]
        count = min(len(rows), spec.max_items)
        return selected, len(rows), count, count < len(rows), None, None
    if isinstance(value, list):
        rows = [row for row in value if _record_matches(row, spec, snapshot) if isinstance(row, dict)] if all(isinstance(row, dict) for row in value) else [
            row for row in value if isinstance(row, dict) and _record_matches(row, spec, snapshot)
            or not isinstance(row, dict) and not (spec.modules or spec.functions or spec.regions or spec.contexts)]
        selected = [_fields(row, spec) for row in rows[:spec.max_items]]
        return selected, len(rows), len(selected), len(selected) < len(rows), None, None
    if isinstance(value, dict):
        selected = _fields(value, spec) if view != "proof" else value
        total = len(selected)
        selected = dict(list(selected.items())[:spec.max_items])
        return selected, total, len(selected), len(selected) < total, None, None
    return value, 0, 0, False, None, None


def _project_value_query(query, value, spec, snapshot):
    module, symbol = query["module"], query["symbol"]
    if not _matches(module, spec.modules) or not _matches(module + "." + symbol, spec.functions):
        return None, 0, 0, False, "unavailable", "Value excluded by output module/function selectors"
    if value is None:
        return None, 0, 0, False, None, None
    unavailable = _joined_unavailable(spec)
    if unavailable:
        return unavailable
    if spec.max_items == 0:
        return None, 1, 0, True, "unavailable", "Value omitted by output item limit"
    return _fields(value, spec, ("top", "bottom", "top_sorts")), 1, 1, False, None, None


@dataclass(frozen=True)
class QueryAnswer(Mapping):
    query: object
    status: str
    _payload: object
    approximation: str = "flow_sensitive_may"
    coverage_status: str = "open"
    proof_status: str = "not_requested"

    def __post_init__(self):
        object.__setattr__(self, "query", freeze_snapshot(self.query))
        object.__setattr__(self, "_payload", freeze_snapshot(self._payload))

    @property
    def value(self): return thaw_snapshot(self._payload)
    @property
    def answer(self): return self.value
    def to_dict(self):
        return {"query": thaw_snapshot(self.query), "status": self.status, "value": self.value,
                "approximation": self.approximation, "coverage_status": self.coverage_status,
                "proof_status": self.proof_status}
    def __getitem__(self, key): return self.to_dict()[key]
    def __iter__(self): return iter(self.to_dict())
    def __len__(self): return len(self.to_dict())


class AnalysisResult:
    """Immutable detached result. Queries and serialization never start work."""

    def __init__(self, data, queries, output=None):
        self._data = freeze_snapshot(data)
        self._queries = tuple(normalize_query(q) for q in queries)
        self._query_keys = frozenset(query_key(q) for q in self._queries)
        self.output = output or OutputSpec(**data["effective_config"]["output"])

    @property
    def solver_status(self): return self._data["solver_status"]
    @property
    def coverage_status(self): return self._data["coverage_status"]
    @property
    def proof_status(self): return self._data.get("proof_status", "not_requested")
    @property
    def effective_config(self): return thaw_snapshot(self._data["effective_config"])
    @property
    def phase_trace(self): return thaw_snapshot(self._data.get("phase_trace", ()))
    @property
    def actual_engine(self): return self._data["actual_engine"]
    @property
    def is_complete(self):
        """Compatibility only: convergence and declared coverage; no proof."""
        return self.solver_status == "converged" and self.coverage_status == "closed_under_profile"

    def query(self, query):
        normalized = normalize_query(query)
        kind = normalized["kind"]
        requested = kind == "summary" or query_key(normalized) in self._query_keys
        if kind == "value":
            requested = requested or query_key("values" if normalized["point"] == "module_exit" else "returns") in self._query_keys
        elif kind in ("diagnostics", "stats", "proof", "obligations"):
            requested = requested or query_key(kind) in self._query_keys
        coverage = self.coverage_status
        approximation = "flow_insensitive_may" if self.actual_engine == "native" else "flow_sensitive_may"
        if not requested:
            return QueryAnswer(normalized, "not_requested", None, approximation, coverage)
        data = self._data
        status = "answered"
        if kind == "value":
            if normalized["point"] == "module_exit":
                payload = data.get("values", {}).get(normalized["module"], {}).get(normalized["symbol"])
            else:
                payload = data.get("returns", {}).get(normalized["module"] + "." + normalized["symbol"])
            if payload is None: status = "unavailable"
        elif kind == "property":
            payload = data.get("proof", {}).get("queries", {}).get(query_key(normalized)) or data.get("proof", {}).get("properties", {}).get(normalized["property"],
                        {"status": "unknown", "reason": "No closed certificate for the requested property"})
        elif kind == "summary":
            payload = {"modules": len(data.get("inventory", ())), "entry_contract": data["entry_contract"],
                       "calls": len(data.get("calls", ())), "obligations": len(data.get("obligations", ())),
                       "actual_engine": self.actual_engine, "solver_status": self.solver_status,
                       "coverage_status": coverage, "proof_status": self.proof_status}
        else:
            payload = data.get(kind)
            if payload is None: status = "unavailable"
        return QueryAnswer(normalized, status, payload, approximation, coverage,
                           payload.get("status", "unknown") if kind == "property" else self.proof_status if kind == "proof" else "not_requested")

    def raw_snapshot(self):
        """Independent JSON copy for read-only consumers, never a solver handle."""
        return thaw_snapshot(self._data)

    def to_dict(self, output=None):
        spec = output or self.output
        if not isinstance(spec, OutputSpec): spec = OutputSpec(**spec)
        data = self.raw_snapshot()
        invariant_keys = ("schema_version", "run_id", "source_digest", "model_digest", "policy_digest",
                          "target", "entry_contract", "effective_config", "config_provenance", "inventory",
                          "actual_engine", "phase_trace", "solver_status", "coverage_status", "proof_status",
                          "profile", "diagnostics", "obligations", "evidence")
        result = {key: data[key] for key in invariant_keys if key in data}
        projection = {"views": list(spec.views), "modules": list(spec.modules),
                      "functions": list(spec.functions), "regions": list(spec.regions),
                      "contexts": list(spec.contexts), "fields": list(spec.fields),
                      "truncated": False, "total_items": 0, "output_items": 0}
        answers = {}
        for view in spec.views:
            answer = self.query(view).to_dict()
            value = answer["value"]
            value, total, count, truncated, status, reason = _project_payload(view, value, spec, data)
            projection["total_items"] += total; projection["output_items"] += count
            projection["truncated"] |= truncated
            if status: answer["status"] = status; answer["reason"] = reason
            answer["value"] = value
            answers[view] = answer
        for query in self._queries:
            if query["kind"] not in ("value", "property"):
                continue
            key = query_key(query)
            if key in answers:
                continue
            answer = self.query(query).to_dict()
            if query["kind"] == "value":
                value, total, count, truncated, status, reason = _project_value_query(query, answer["value"], spec, data)
                answer["value"] = value
                if status:
                    answer["status"] = status
                    answer["reason"] = reason
                projection["total_items"] += total
                projection["output_items"] += count
                projection["truncated"] |= truncated
            answers[key] = answer
        result["answers"] = answers; result["projection"] = projection
        return result

    def serialize(self, format="json", output=None):
        data = self.to_dict(output)
        if format == "json": return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        if format != "text": raise ValueError("format must be json or text")
        lines = [f"PythonStAn: {self.actual_engine}", f"solver={self.solver_status}; coverage={self.coverage_status}; proof={self.proof_status}"]
        for view, answer in data["answers"].items():
            lines.append(view + " [" + answer["status"] + "]: " + json.dumps(answer["value"], ensure_ascii=False, sort_keys=True))
        if data["projection"]["truncated"]: lines.append("Output truncated; see projection metadata.")
        return "\n".join(lines) + "\n"

    def write(self, destination, format="json", output=None):
        text = self.serialize(format=format, output=output)
        if hasattr(destination, "write"): destination.write(text)
        else: Path(destination).write_text(text, encoding="utf-8")

    def __repr__(self):
        return f"AnalysisResult(engine={self.actual_engine!r}, solver={self.solver_status!r}, coverage={self.coverage_status!r})"
