"""Read-only causal explanations over detached public analysis results.

This adapter imports no engine. It cannot add facts, invoke a solver, or improve
proof/coverage status. Trace absence is never evidence of semantic absence.
"""
from __future__ import annotations
from collections.abc import Mapping

from pythonstan.analysis.contracts.wire import freeze_snapshot, thaw_snapshot


class FrozenResultInspector:
    def __init__(self, result):
        from pythonstan.results import AnalysisResult
        if isinstance(result, AnalysisResult):
            data = result.raw_snapshot()
        elif isinstance(result, Mapping):
            data = dict(result)
        else:
            raise TypeError("Inspector requires a detached AnalysisResult or JSON snapshot")
        self._data = freeze_snapshot(data)

    def snapshot(self):
        return thaw_snapshot(self._data)

    def events(self, *, kind=None, point=None, unit=None):
        observation = self._data.get("events", {})
        records = observation.get("events", ()) if isinstance(observation, Mapping) else observation
        selected = []
        for event in records:
            if kind is not None and event.get("kind") != kind:
                continue
            if point is not None and event.get("point") != point:
                continue
            if unit is not None and event.get("unit") != unit:
                continue
            selected.append(thaw_snapshot(event))
        return selected

    def explain(self, *, module, symbol, point="module_exit", max_events=256):
        if point not in ("module_exit", "function_exit"):
            raise ValueError("Explanation point must be module_exit or function_exit")
        if type(max_events) is not int or max_events < 0:
            raise ValueError("max_events must be nonnegative")
        data = self._data
        if point == "module_exit":
            value = data.get("values", {}).get(module, {}).get(symbol)
            cell = f"global:{module}:{symbol}"
        else:
            value = data.get("returns", {}).get(module + "." + symbol)
            cell = None
        observation = data.get("events", {})
        records = observation.get("events", ()) if isinstance(observation, Mapping) else observation
        by_id = {event["event_id"]: event for event in records if "event_id" in event}
        if cell is not None:
            roots = [event["event_id"] for event in records
                     if event.get("kind") == "name_write" and event.get("payload", {}).get("cell") == cell]
            # Keep all alternatives at the latest observed unit revision. A
            # linear last event alone would lose branch alternatives.
            revisions = {}
            for identifier in roots:
                event = by_id[identifier]
                key = event.get("unit")
                revision = event.get("revision")
                if type(revision) is int:
                    revisions[key] = max(revisions.get(key, -1), revision)
            roots = [identifier for identifier in roots if not revisions
                     or by_id[identifier].get("revision") == revisions.get(by_id[identifier].get("unit"))]
        else:
            qualname = module + "." + symbol
            roots = [event["event_id"] for event in records
                     if event.get("kind") == "summary_update"
                     and event.get("payload", {}).get("qualname") == qualname]
        pending = list(roots)
        seen = set()
        missing_parents = set()
        while pending and len(seen) < max_events:
            identifier = pending.pop()
            if identifier in seen:
                continue
            event = by_id.get(identifier)
            if event is None:
                missing_parents.add(identifier)
                continue
            seen.add(identifier)
            pending.extend(event.get("parents", ()))
        chain = sorted((by_id[x] for x in seen), key=lambda event: event.get("sequence", 0))
        partial = bool(pending or missing_parents or (isinstance(observation, Mapping) and
                      (observation.get("truncated") or observation.get("errors"))))
        source = [event["source"] for event in chain if event.get("source")]
        inputs = [event for event in chain if event.get("kind") == "name_read"]
        summaries = [event for event in chain if event.get("kind") == "summary_update"]
        dependencies = [event for event in chain if event.get("kind") == "call_dependency"]
        missing = []
        if not roots:
            missing.append("no_observed_producer_for_query")
        if roots and not inputs:
            missing.append("no_retained_input_read_values")
        if value is None:
            missing.append("query_value_unavailable_in_snapshot")
        return {"schema_version": 1, "status": "observed_chain" if roots else "unavailable",
                "claim": "diagnostic_observation_only", "truncated": partial,
                "query": {"module": module, "symbol": symbol, "point": point},
                "analysis_status": {key: data.get(key) for key in
                                    ("actual_engine", "solver_status", "coverage_status", "proof_status", "profile")},
                "identity": {key: data.get(key) for key in
                             ("run_id", "source_digest", "model_digest", "policy_digest")},
                "sources": thaw_snapshot(tuple(source)),
                "inputs": thaw_snapshot(tuple(inputs)),
                "summaries": thaw_snapshot(tuple(summaries)),
                "dependencies": thaw_snapshot(tuple(dependencies)),
                "chain": thaw_snapshot(tuple(chain)),
                "projection": {"kind": "query_projection", "value": thaw_snapshot(value),
                               "producer_events": roots},
                "missing": missing, "missing_parents": sorted(missing_parents)}


def inspect_result(result):
    return FrozenResultInspector(result)
