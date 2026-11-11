"""Source-bound region selection and conservative recursive component routing."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import ast
import fnmatch
import json

from pythonstan.analysis.contracts import stable_digest, freeze_snapshot, thaw_snapshot
from pythonstan.config import ConfigurationError, resolve_config


def node_identity(module, digest, node):
    return f"{module}:{digest[:16]}:{getattr(node, 'lineno', 0)}:{getattr(node, 'col_offset', 0)}:{type(node).__name__}"


def _inventory(snapshot):
    rows, functions = [], {}
    for module in snapshot.modules:
        tree = ast.parse(module.source, module.filename)
        def walk(nodes, qualname, scope_kind):
            for node in nodes:
                if not isinstance(node, ast.stmt): continue
                kind = "function" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "class" if isinstance(node, ast.ClassDef) else "loop" if isinstance(node, (ast.For, ast.AsyncFor, ast.While)) else "block"
                actual = qualname + "." + node.name if kind in ("function", "class") else qualname
                row = {"module": module.module, "source_digest": module.digest, "qualname": actual,
                       "scope_qualname": qualname, "node_id": node_identity(module.module, module.digest, node),
                       "kind": kind, "span": [node.lineno, node.col_offset, node.end_lineno, node.end_col_offset],
                       "node": node, "scope_kind": scope_kind}
                rows.append(row)
                if kind == "function":
                    functions[actual] = row
                    walk(node.body, actual, "function")
                elif kind == "class":
                    walk(node.body, actual, "class")
                else:
                    for field in ("body", "orelse", "finalbody"):
                        walk(getattr(node, field, ()), qualname, scope_kind)
                    for handler in getattr(node, "handlers", ()): walk(handler.body, qualname, scope_kind)
        walk(tree.body, module.module, "module")
    return rows, functions


def _scc(functions):
    graph = {name: set() for name in functions}
    builtin_names = {"str", "int", "bool", "float", "len", "range", "dict", "list", "tuple", "set", "print", "super",
                     "object", "next", "iter", "isinstance", "issubclass", "getattr", "setattr", "hasattr", "delattr"}
    for name, row in functions.items():
        for call in (x for x in ast.walk(row["node"]) if isinstance(x, ast.Call)):
            if isinstance(call.func, ast.Name):
                short = call.func.id
                candidates = {f for f in functions if f.rsplit(".", 1)[-1] == short}
                if candidates: graph[name].update(candidates)
                elif short not in builtin_names:
                    graph[name].update(functions)  # unresolved dynamic calls cannot prove SCC separation
            elif isinstance(call.func, ast.Attribute):
                candidates = {f for f in functions if f.rsplit(".", 1)[-1] == call.func.attr}
                graph[name].update(candidates or functions)
    index, stack, onstack, indices, low, groups = 0, [], set(), {}, {}, []
    def visit(node):
        nonlocal index
        indices[node] = low[node] = index; index += 1; stack.append(node); onstack.add(node)
        for child in graph[node]:
            if child not in indices: visit(child); low[node] = min(low[node], low[child])
            elif child in onstack: low[node] = min(low[node], indices[child])
        if low[node] == indices[node]:
            group = []
            while True:
                child = stack.pop(); onstack.remove(child); group.append(child)
                if child == node: break
            groups.append(sorted(group))
    for name in graph:
        if name not in indices: visit(name)
    return sorted(groups), graph


@dataclass(frozen=True)
class EffectiveRegionPlan:
    source_digest: str
    policy_digest: str
    _data: Mapping

    def __post_init__(self): object.__setattr__(self, "_data", freeze_snapshot(self._data))
    def to_dict(self): return thaw_snapshot(self._data)
    def decision(self, module, qualname, node_id):
        value = self._data["decisions"].get(node_id)
        if value is None: raise ConfigurationError(f"No planned statement: {node_id}")
        if value["module"] != module: raise ConfigurationError("Region module identity mismatch")
        return thaw_snapshot(value)
    @property
    def decisions(self): return thaw_snapshot(self._data["decisions"])


def build_plan(snapshot, config):
    config = config.to_dict() if hasattr(config, "to_dict") else dict(config)
    rows, functions = _inventory(snapshot)
    components, graph = _scc(functions)
    rules = config["regions"]
    if not isinstance(rules, (list, tuple)): raise ConfigurationError("regions must be an ordered list")
    allowed = {"selector", "engine", "abstract", "priority", "name"}
    selector_allowed = {"module", "qualname", "kind", "node_id", "source_digest", "line_start", "line_end", "span"}
    prepared = []
    for ordinal, rule in enumerate(rules):
        if not isinstance(rule, Mapping) or set(rule) - allowed: raise ConfigurationError("Unknown region rule fields")
        selector = rule.get("selector")
        if not isinstance(selector, Mapping) or not selector or set(selector) - selector_allowed: raise ConfigurationError("Region selector must use registered source identity fields")
        if rule.get("engine", "ai_primary") not in ("native", "ai_primary"): raise ConfigurationError("Region engines are native or ai_primary")
        if type(rule.get("priority", 0)) is not int: raise ConfigurationError("Region priority must be an integer")
        abstract = rule.get("abstract", {})
        if not isinstance(abstract, Mapping): raise ConfigurationError("Region abstract policy must be an object")
        resolve_config(config={"engine": "ai_primary", "abstract": dict(abstract)})
        for module in snapshot.modules:
            if fnmatch.fnmatch(module.module, selector.get("module", "*")) and selector.get("source_digest") not in (None, module.digest):
                raise ConfigurationError("Stale source digest in region selector")
        matched = []
        for row in rows:
            if not fnmatch.fnmatch(row["module"], selector.get("module", "*")): continue
            if not fnmatch.fnmatch(row["qualname"], selector.get("qualname", "*")): continue
            if selector.get("kind") not in (None, row["kind"]): continue
            if selector.get("node_id") not in (None, row["node_id"]): continue
            start = selector.get("line_start")
            end = selector.get("line_end", start)
            if "span" in selector:
                span = selector["span"]
                if not isinstance(span, (list, tuple)) or len(span) != 2: raise ConfigurationError("Selector span is [first_line,last_line]")
                start, end = span
            if start is not None:
                if type(start) is not int or type(end) is not int or start < 1 or end < start: raise ConfigurationError("Invalid selector line range")
                if not start <= row["span"][0] <= end: continue
            matched.append(row)
        if not matched: raise ConfigurationError(f"Region selector matched no current source: {dict(selector)}")
        prepared.append((ordinal, dict(rule), matched))
    decisions, selected_priority = {}, {}
    default_engine = "native" if config["engine"] in ("native", "native_then_ai") else "ai_primary"
    for row in rows:
        decisions[row["node_id"]] = {k: v for k, v in row.items() if k not in {"node", "scope_kind"}}
        decisions[row["node_id"]].update(engine=default_engine, abstract={}, origin="default", boundary_profile="python-core-v1",
            region_id=stable_digest({"source": row["source_digest"], "node": row["node_id"], "qualname": row["qualname"]}))
    def descendants(selected):
        node = selected["node"]
        if selected["kind"] not in ("function", "class", "loop"): return [selected]
        ids = {id(x) for x in ast.walk(node) if isinstance(x, ast.stmt)}
        return [row for row in rows if id(row["node"]) in ids and row["node_id"] != selected["node_id"]]
    for ordinal, rule, matched in prepared:
        priority = rule.get("priority", 0)
        targets = {r["node_id"]: r for row in matched for r in descendants(row)}
        for node_id, row in targets.items():
            proposed = {"engine": rule.get("engine", "ai_primary"), "abstract": dict(rule.get("abstract", {}))}
            existing = decisions[node_id]
            if node_id in selected_priority and selected_priority[node_id] == priority:
                if any(existing[key] != value for key, value in proposed.items()):
                    raise ConfigurationError(f"Equal-priority region conflict at {node_id}")
            if priority < selected_priority.get(node_id, -10**9): continue
            existing.update(proposed, origin=rule.get("name", f"rule:{ordinal}"), priority=priority)
            existing["region_id"] = stable_digest({"source": row["source_digest"], "node": node_id, "qualname": row["qualname"]})
            existing["boundary_profile"] = "native-straightline-v1" if proposed["engine"] == "native" else "python-core-v1"
            selected_priority[node_id] = priority
    for group in components:
        strategies = set()
        for name in group:
            relevant = [d for d in decisions.values() if d["scope_qualname"] == name]
            strategies.update(json.dumps({"engine": d["engine"], "abstract": d["abstract"]}, sort_keys=True) for d in relevant)
        if len(group) > 1 and len(strategies) > 1:
            raise ConfigurationError(f"Heterogeneous recursive SCC requires an unavailable joint adapter: {group}")
    data = {"schema_version": 1, "source_digest": snapshot.source_digest, "policy_digest": stable_digest(config),
            "decisions": decisions, "sccs": components,
            "call_dependencies": {key: sorted(value) for key, value in graph.items()},
            "entry_contract": snapshot.entry_contract,
            "default_engine": default_engine}
    return EffectiveRegionPlan(snapshot.source_digest, stable_digest(config), data)
