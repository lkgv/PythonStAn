"""One run owns all source, policy, solver state and published interpretation."""
from __future__ import annotations

from dataclasses import dataclass, replace
import copy
import time
import uuid

from pythonstan.analysis.contracts import thaw_snapshot
from pythonstan.inputs import snapshot_target
from pythonstan.config import resolve_config
from pythonstan.results import AnalysisResult, OutputSpec, normalize_query


class AnalysisSession:
    def __init__(self, target, *, config=None, overrides=None, queries=None, output=None, _snapshot=None):
        self.started = time.monotonic()
        self.target = target
        self.run_id = uuid.uuid4().hex
        try:
            self.config = resolve_config(target, config, overrides, queries=queries, output=output)
            self.snapshot = snapshot_target(target, self.config) if _snapshot is None else _snapshot
            self.queries = tuple(normalize_query(q) for q in self.config["queries"])
            self.output = output or OutputSpec(**self.config["output"])
            from pythonstan.frontend.source import parse_sources
            try:
                self.program = parse_sources(self.snapshot.module_map, parsed_trees=self.snapshot.parsed_trees)
            except SyntaxError as exc:
                from pythonstan.inputs import InputError
                raise InputError(str(exc)) from exc
        except (TypeError, ValueError) as exc:
            from pythonstan.inputs import InputError
            from pythonstan.config import ConfigurationError
            if isinstance(exc, (InputError, ConfigurationError)): raise
            raise ConfigurationError(str(exc)) from exc
        self.plan = None
        if self.config["regions"] or self.output.regions or any(q["kind"] == "plan" for q in self.queries):
            from pythonstan.analysis.composition.plan import build_plan
            if self.config["engine"] in ("native", "native_then_ai") and self.config["regions"]:
                from pythonstan.config import ConfigurationError
                raise ConfigurationError("Region routing requires ai_primary or hybrid")
            self.plan = build_plan(self.snapshot, self.config.to_dict())
        self.closed = False
        priority = {query["module"]: index for index, query in enumerate(self.queries)
                    if query["kind"] == "value"}
        scenarios = tuple(sorted(self.snapshot.scenarios, key=lambda entry: priority.get(entry.root_module, len(priority))))
        self.execution_snapshot = replace(self.snapshot, scenarios=scenarios)

    @classmethod
    def from_snapshot(cls, snapshot, *, target, config, queries=None, output=None):
        """Reanalyze immutable source bytes with a fresh per-session AST."""
        private = replace(snapshot, target=copy.deepcopy(snapshot.target),
                          inventory=tuple(copy.deepcopy(row) for row in snapshot.inventory), parsed_trees={})
        return cls(target, config=config, queries=queries, output=output, _snapshot=private)

    def execute(self):
        if self.closed: raise RuntimeError("AnalysisSession is closed")
        started = self.started
        config = self.config.to_dict()
        mode = config["engine"]
        execution_config = self.config.to_dict()
        elapsed = time.monotonic() - self.started
        seconds = config["resources"]["max_seconds"]
        exhausted = bool(seconds and elapsed >= seconds)
        if seconds: execution_config["resources"]["max_seconds"] = max(0.001, seconds - elapsed)
        sink = None
        if config["observation"]["enabled"]:
            from pythonstan.analysis.contracts.observation import ObservationSink
            sink = ObservationSink(run_id=self.run_id, source_digest=self.snapshot.source_digest,
                                   model_digest="pythonstan-builtins-v1", policy_digest=self.config.digest,
                                   profile=config["semantic_profile"], sources=self.snapshot.module_map,
                                   max_events=config["observation"]["max_events"])
        if exhausted:
            result = {"solver_status": "budget_exhausted", "coverage_status": "open",
                      "values": {}, "calls": [], "returns": {}, "effects": {}, "outcomes": {},
                      "phase_trace": [{"phase": "source_preparation", "seconds": elapsed, "status": "budget_exhausted"}],
                      "obligations": [{"kind": "time_budget", "stage": "source_preparation"}]}
        elif mode == "native":
            from pythonstan.compat.legacy_runner import run_legacy
            result = run_legacy(self.execution_snapshot, execution_config)
        elif mode in ("ai_primary", "hybrid", "native_then_ai"):
            from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
            from pythonstan.analysis.composition.hybrid import configure_driver
            native_phase = None
            native_work = 0
            oracle = None
            if mode == "native_then_ai":
                from pythonstan.compat.legacy_runner import run_legacy
                native_config = copy.deepcopy(execution_config)
                native_config["engine"] = "native"
                native_phase = run_legacy(self.execution_snapshot, native_config)
                native_work = native_phase.get("stats", {}).get("total_work",
                                native_phase.get("stats", {}).get("global_work_used",
                                native_phase.get("stats", {}).get("native_work", 0)))
                native_phase.update(source_digest=self.snapshot.source_digest, profile=config["semantic_profile"],
                                    model_digest="pythonstan-builtins-v1", policy_digest=self.config.digest)
                from pythonstan.analysis.composition.native_snapshot import NativeSnapshotOracle
                oracle = NativeSnapshotOracle(native_phase, source_digest=self.snapshot.source_digest,
                            profile=config["semantic_profile"], policy_digest=self.config.digest,
                            model_digest="pythonstan-builtins-v1",
                            max_work=max(0, config["resources"]["max_work"]-native_work))
            driver = InterproceduralDriver(self.snapshot.module_map, options=config, oracle=oracle,
                                            observer=sink, program=self.program)
            driver.max_work = max(0, config["resources"]["max_work"]-native_work)
            provider = None
            provider_failure = None
            if config["llm"]["enabled"] and config["llm"]["level2"]:
                try:
                    from pythonstan.llm.providers import make_summary_provider
                    provider = make_summary_provider(self.snapshot, config, remaining_work=driver.max_work)
                except Exception as exc:
                    provider_failure = f"{type(exc).__name__}: {exc}"
            configure_driver(driver, plan=self.plan, source_digest=self.snapshot.source_digest,
                             config=config, summary_provider=provider)
            remaining = seconds-(time.monotonic()-self.started) if seconds else None
            if remaining is not None and remaining <= 0:
                driver.max_work = 0
            elif remaining is not None:
                # Publication is part of the global deadline. Reserve bounded
                # time to freeze and serialize an honest partial result.
                reserve = min(1.0, max(0.05, remaining * 0.25))
                driver.max_seconds = max(0.001, remaining-reserve)
            snapshot = driver.run(self.execution_snapshot.scenarios)
            result = snapshot.to_dict() if hasattr(snapshot, "to_dict") else thaw_snapshot(snapshot)
            result.setdefault("stats", {})["native_prepass_work"] = native_work
            result["stats"]["total_work"] = native_work + driver.work
            if native_phase is not None:
                result["phase_trace"] = native_phase.get("phase_trace", []) + result.get("phase_trace", [])
                result["stats"]["native_prepass"] = native_phase.get("stats", {})
            if provider is not None:
                records = [thaw_snapshot(record) for record in provider.proof_records]
                properties, query_proofs = {}, {}
                for query in self.queries:
                    if query["kind"] != "property": continue
                    selected = [row for row in records if row.get("property") == query["property"] and
                                (query["point"] == "all" or row.get("point") == query["point"])]
                    status = "verified" if selected and all(row.get("status") == "verified" for row in selected) else "unknown"
                    proof = {"status": status, "property": query["property"], "point": query["point"],
                             "profile": "guarded-dispatch-v1", "records": selected,
                             "scope": "Independently checked source initialization call inputs",
                             "source_digest": self.snapshot.source_digest}
                    from pythonstan.results.schema import query_key
                    query_proofs[query_key(query)] = proof
                    previous = properties.get(query["property"])
                    properties[query["property"]] = proof if previous is None or status != "verified" else previous
                result["proof"] = {"properties": properties, "queries": query_proofs, "records": records}
                result.setdefault("llm", {})["level2"] = provider.statistics
            if provider_failure:
                result.setdefault("diagnostics", []).append({"kind": "llm_provider_failure", "message": provider_failure})
        else:
            raise ValueError(f"Unsupported engine: {mode}")
        result.update(schema_version=1, run_id=self.run_id, source_digest=self.snapshot.source_digest,
                      model_digest="pythonstan-builtins-v1", policy_digest=self.config.digest,
                      target=self.snapshot.target, inventory=list(self.snapshot.inventory),
                      entry_contract=self.snapshot.entry_contract, effective_config=config,
                      config_provenance=self.config.provenance, actual_engine=mode,
                      profile=config["semantic_profile"])
        result["entry_contract"]["execution_order"] = [entry.scenario_id for entry in self.execution_snapshot.scenarios]
        requested_properties = {query["property"] for query in self.queries if query["kind"] == "property"}
        proof = result.setdefault("proof", {"properties": {}})
        for name in requested_properties:
            proof.setdefault("properties", {}).setdefault(name, {"status": "unknown", "reason": "Requested property has no closed source certificate"})
        result["proof_status"] = ("verified" if requested_properties and all(proof["properties"][name]["status"] == "verified" for name in requested_properties)
                                  else "unknown" if requested_properties else "not_requested")
        result.setdefault("diagnostics", [])
        result.setdefault("obligations", [])
        result.setdefault("stats", {})["total_wall_seconds"] = time.monotonic()-started
        result["stats"]["source_preparation_seconds"] = elapsed
        if sink is not None:
            result["events"] = sink.snapshot()
            if mode == "native":
                result["events"]["observation_status"] = "unsupported"
                result["events"]["reason"] = "legacy_native_producer_not_attached"
            elif mode == "native_then_ai":
                result["events"]["phase_coverage"] = {"native": "not_attached", "abstract": "recorded"}
                result["events"]["observation_status"] = "partial"
            result["stats"]["observation"] = sink.stats
        result.setdefault("evidence", {"source_digest": self.snapshot.source_digest})
        if self.plan is not None: result["plan"] = self.plan.to_dict()
        if config["llm"]["enabled"] and config["llm"]["level1"]:
            from pythonstan.llm.strategy import RefinementController
            baseline_stats = dict(result["stats"])
            baseline_trace = list(result.get("phase_trace", []))
            selected, feedback = RefinementController().run(target=self.target, source_snapshot=self.snapshot,
                                                            config=config, queries=self.queries, output=self.output,
                                                            baseline=result)
            result = selected
            result["requested_config"] = config
            result.setdefault("llm", {})["level1"] = feedback
            result.setdefault("stats", {})["baseline_before_l1"] = baseline_stats
            result["stats"]["l1_seconds"] = feedback["seconds"]
            result["stats"]["total_work"] = feedback["total_work"]
            result["stats"]["total_wall_seconds"] = time.monotonic()-self.started
            result["phase_trace"] = baseline_trace + [
                {**phase, "l1_rerun": index} for index, row in enumerate(feedback["proposals"])
                for phase in row.get("phase_trace", [])
            ] + [{"phase": "l1_policy_control", "attempted": feedback["attempted"],
                  "seconds_excluding_reruns": max(0, feedback["seconds"] - sum(row.get("seconds", 0) for row in feedback["proposals"]))}]
        return AnalysisResult(result, self.queries, self.output)

    def close(self): self.closed = True
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
