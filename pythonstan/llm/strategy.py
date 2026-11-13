"""Finite L1 proposals with typed, source-preserving clean reanalysis."""

from __future__ import annotations

import ast
import hashlib
import time
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from pythonstan.analysis.composition.plan import build_plan
from pythonstan.analysis.contracts import stable_digest
from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    JsonObject,
    JsonScalar,
    JsonValue,
    freeze_json_object,
    require_array,
    require_bool,
    require_int,
    require_number,
    require_object,
    require_string,
    thaw_json_object,
    validate_json,
    validate_json_object,
)
from pythonstan.analysis.contracts.resources import RunLedger
from pythonstan.analysis.contracts.wire import thaw_snapshot
from pythonstan.config import ConfigurationError, resolve_config
from pythonstan.inputs import Source, SourceSnapshot
from pythonstan.inputs.targets import Target
from pythonstan.request import resolve_request
from pythonstan.results.output import OutputSpec
from pythonstan.results.queries import QueryInput, QuerySpec, parse_query, query_to_wire

REGISTERED_STRATEGIES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "key_partitions": frozenset({"enable_value_partition"}),
        "string_capacity": frozenset({"max_const_strings"}),
        "call_context": frozenset({"context_depth"}),
    }
)
_PROPOSAL_FIELDS = frozenset(
    {"query", "source_digest", "region", "strategy", "parameters", "resources", "reason"}
)


@dataclass(frozen=True, init=False)
class PolicyProposal:
    query: QuerySpec
    source_digest: str
    region: FrozenJsonObject
    strategy: str
    parameters: FrozenJsonObject
    resources: FrozenJsonObject
    reason: str

    def __init__(
        self,
        *,
        query: QueryInput,
        source_digest: str,
        region: Mapping[str, object],
        strategy: str,
        parameters: Mapping[str, object],
        resources: Mapping[str, object],
        reason: str = "",
    ) -> None:
        object.__setattr__(self, "query", parse_query(query))
        object.__setattr__(self, "source_digest", require_string(source_digest))
        object.__setattr__(self, "region", freeze_json_object(validate_json_object(region)))
        object.__setattr__(self, "strategy", require_string(strategy))
        object.__setattr__(self, "parameters", freeze_json_object(validate_json_object(parameters)))
        object.__setattr__(self, "resources", freeze_json_object(validate_json_object(resources)))
        object.__setattr__(self, "reason", require_string(reason))

    def to_dict(self) -> JsonObject:
        return {
            "query": query_to_wire(self.query),
            "source_digest": self.source_digest,
            "region": thaw_json_object(self.region),
            "strategy": self.strategy,
            "parameters": thaw_json_object(self.parameters),
            "resources": thaw_json_object(self.resources),
            "reason": self.reason,
        }

    @classmethod
    def parse(cls, value: object) -> PolicyProposal:
        if isinstance(value, PolicyProposal):
            return value
        try:
            data = validate_json_object(value)
            if set(data) - _PROPOSAL_FIELDS:
                raise ConfigurationError(
                    f"Forbidden policy proposal fields: {sorted(set(data) - _PROPOSAL_FIELDS)}"
                )
            required = _PROPOSAL_FIELDS - {"reason"}
            if required - data.keys():
                raise ConfigurationError(
                    f"Missing policy proposal fields: {sorted(required - data.keys())}"
                )
            return cls(
                query=parse_query(data["query"]),
                source_digest=require_string(data["source_digest"]),
                region=require_object(data["region"]),
                strategy=require_string(data["strategy"]),
                parameters=require_object(data["parameters"]),
                resources=require_object(data["resources"]),
                reason=require_string(data.get("reason", "")),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ConfigurationError):
                raise
            raise ConfigurationError(str(error)) from error


def _relation_token(row: JsonObject) -> tuple[tuple[str, JsonScalar], ...] | None:
    result: list[tuple[str, JsonScalar]] = []
    for name, value in sorted(row.items()):
        if isinstance(value, (dict, list)):
            return None
        result.append((name, value))
    return tuple(result)


def _quality(snapshot: JsonObject) -> JsonObject:
    tops = 0
    relations: set[tuple[tuple[str, JsonScalar], ...]] = set()
    for module in require_object(snapshot.get("values", {})).values():
        for item in require_object(module).values():
            value = require_object(item)
            tops += bool(value.get("top") or value.get("top_sorts"))
            for relation in require_array(value.get("relations", [])):
                if isinstance(relation, dict):
                    token = _relation_token(relation)
                    if token is not None:
                        relations.add(token)
    return {"top_answers": tops, "relation_rows": len(relations)}


class StrategyProposer(Protocol):
    def propose(
        self, snapshot: JsonObject, source_snapshot: SourceSnapshot, config: JsonObject
    ) -> Sequence[PolicyProposal | JsonValue]: ...


class OfflineStrategyProposer:
    def propose(
        self, snapshot: JsonObject, source_snapshot: SourceSnapshot, config: JsonObject
    ) -> list[JsonValue]:
        llm = require_object(config["llm"])
        explicit = require_array(llm["proposals"])
        if explicit or llm["provider"] in {"recorded", "replay"}:
            return list(explicit)
        if llm["provider"] == "adversarial":
            return [{"strategy": "delete_entry", "verified": True}]
        ambiguous = any(
            not require_object(row).get("partitioned", False)
            for module in require_object(snapshot.get("values", {})).values()
            for value in require_object(module).values()
            for row in require_array(require_object(value).get("relations", []))
        )
        if not ambiguous:
            return []
        return [
            PolicyProposal(
                query="relations",
                source_digest=source_snapshot.source_digest,
                region={"module": "*", "qualname": "*", "kind": "block"},
                strategy="key_partitions",
                parameters={"enable_value_partition": True},
                resources={},
                reason="Exported key/handler rows contain an unpartitioned may product",
            ).to_dict()
        ]


def _work(snapshot: JsonObject) -> int:
    stats = require_object(snapshot.get("stats", {}))
    if "total_work" in stats:
        return require_int(stats["total_work"])
    return (
        require_int(stats.get("work", 0))
        + require_int(stats.get("pta_work", 0))
        + require_int(stats.get("native_work", 0))
    )


def _proposal_document(raw: object, ledger: RunLedger | None, category: str) -> JsonValue:
    if ledger is None:
        return raw.to_dict() if isinstance(raw, PolicyProposal) else validate_json(raw)
    source: object = raw
    if isinstance(raw, PolicyProposal):
        # These are immutable source records; the metered boundary makes the
        # actual owned JSON copy, rather than thawing all payloads beforehand.
        source = {
            "query": query_to_wire(raw.query),
            "source_digest": raw.source_digest,
            "region": raw.region,
            "strategy": raw.strategy,
            "parameters": raw.parameters,
            "resources": raw.resources,
            "reason": raw.reason,
        }
    with ledger.structural_meter(category) as meter:
        return thaw_snapshot(source, visit=meter.visit)


def _revalidate_source(snapshot: SourceSnapshot, ledger: RunLedger | None) -> None:
    for module in snapshot.modules:
        try:
            if ledger is None:
                current = hashlib.sha256(Path(module.filename).read_bytes()).hexdigest()
            else:
                ledger.charge_work(1, "l1_source_revalidation")
                digest = hashlib.sha256()
                with Path(module.filename).open("rb") as source:
                    while True:
                        ledger.charge_work(1, "l1_source_revalidation")
                        block = source.read(1024)
                        if not block:
                            break
                        digest.update(block)
                ledger.checkpoint(force=True)
                current = digest.hexdigest()
        except OSError as error:
            raise ConfigurationError("L1 source changed after the baseline snapshot") from error
        if current != module.original_bytes_digest:
            raise ConfigurationError("L1 source changed after the baseline snapshot")


class RefinementController:
    proposer: StrategyProposer

    def __init__(self, proposer: StrategyProposer | None = None) -> None:
        self.proposer = OfflineStrategyProposer() if proposer is None else proposer

    def validate(
        self,
        raw: object,
        source_snapshot: SourceSnapshot,
        config: JsonObject,
        *,
        resource_ledger: RunLedger | None = None,
    ) -> tuple[PolicyProposal, JsonObject]:
        if resource_ledger is None:
            proposal = PolicyProposal.parse(raw)
        else:
            resource_ledger.charge_work(1, "l1_validation_attempt")
            proposal = PolicyProposal.parse(
                _proposal_document(raw, resource_ledger, "l1_validation_copy")
            )
            resource_ledger.charge_json(config, "l1_policy_copy")
        if proposal.source_digest != source_snapshot.source_digest:
            raise ConfigurationError("Stale L1 source identity")
        if proposal.strategy not in REGISTERED_STRATEGIES:
            raise ConfigurationError("Unregistered L1 strategy")
        parameters = thaw_json_object(proposal.parameters)
        if set(parameters) != REGISTERED_STRATEGIES[proposal.strategy]:
            raise ConfigurationError("L1 parameters do not match registered strategy")
        selector = thaw_json_object(proposal.region)
        if not selector:
            raise ConfigurationError("L1 requires a source-bound region")
        resources = thaw_json_object(proposal.resources)
        if set(resources) - {"max_work", "max_seconds", "max_memory_mb"}:
            raise ConfigurationError("L1 cannot change entry, model, provider, or retry budgets")
        original_resources = require_object(config["resources"])
        for name, value in resources.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
                or value > require_number(original_resources[name])
            ):
                raise ConfigurationError(
                    "L1 resources must be positive and within the original budget"
                )
        resolve_config(config={"engine": config["engine"], "abstract": parameters})
        updated = validate_json_object(config)
        llm = require_object(updated["llm"])
        llm["level1"] = False
        llm["enabled"] = require_bool(llm["level2"])
        require_object(updated["resources"]).update(resources)
        rule: JsonObject = {
            "selector": selector,
            "engine": "ai_primary",
            "abstract": parameters,
            "priority": 100,
            "name": "l1:" + proposal.strategy,
        }
        updated["regions"] = [*require_array(updated["regions"]), rule]
        if resource_ledger is not None:
            resource_ledger.charge_json(updated, "l1_plan_configuration")
            for module in source_snapshot.modules:
                resource_ledger.charge_work(1, "l1_plan_module")
                cached = source_snapshot.parsed_trees.get(module.module)
                if cached is None:
                    resource_ledger.charge_json(module.source, "l1_plan_source")
                    tree = ast.parse(module.source, module.filename)
                else:
                    tree = cached[1]
                for _ in ast.walk(tree):
                    resource_ledger.charge_work(1, "l1_plan_nodes")
        if resource_ledger is None:
            build_plan(source_snapshot, updated)
        else:
            resolved = resolve_config(config=updated)
            build_plan(source_snapshot, resolved, resource_ledger=resource_ledger)
            resource_ledger.checkpoint(force=True)
        return proposal, updated

    def run(
        self,
        *,
        target: Target,
        source_snapshot: SourceSnapshot,
        config: JsonObject,
        queries: Sequence[QueryInput],
        output: OutputSpec,
        baseline: JsonObject,
        resource_ledger: RunLedger | None = None,
    ) -> tuple[JsonObject, JsonObject]:
        from pythonstan.session import AnalysisSession

        started = time.monotonic()
        rows: list[JsonValue] = []
        seen: set[str] = set()
        selected = baseline
        baseline_work = _work(baseline) if resource_ledger is None else resource_ledger.work.used
        charged_work = baseline_work
        baseline_wall = require_number(
            require_object(baseline.get("stats", {})).get("total_wall_seconds", 0)
        )
        llm, resource_limits = require_object(config["llm"]), require_object(config["resources"])
        limit = min(
            require_int(llm["max_proposals"]), require_int(resource_limits["max_reanalyses"])
        )
        provider_error: JsonValue = None
        proposals: list[PolicyProposal | JsonValue] = []
        baseline_quality: JsonObject = {
            "status": "unavailable",
            "reason": "not_observed_with_control_budget",
        }
        selected_quality = baseline_quality
        try:
            with nullcontext() if resource_ledger is None else resource_ledger.phase("l1_proposer"):
                if resource_ledger is not None:
                    resource_ledger.charge_work(1, "l1_proposer_attempt")
                    resource_ledger.charge_json(baseline.get("values", {}), "l1_quality")
                    baseline_quality = _quality(baseline)
                    selected_quality = baseline_quality
                    observed_baseline = require_object(
                        _proposal_document(baseline, resource_ledger, "l1_proposer_baseline_copy")
                    )
                    observed_config = require_object(
                        _proposal_document(
                            config, resource_ledger, "l1_proposer_configuration_copy"
                        )
                    )
                else:
                    observed_baseline, observed_config = baseline, config
                raw_proposals: object = self.proposer.propose(
                    observed_baseline, source_snapshot, observed_config
                )
                if resource_ledger is not None:
                    resource_ledger.checkpoint(force=True)
                if not isinstance(raw_proposals, (list, tuple)):
                    raise ConfigurationError("L1 provider must return a finite proposal array")
                bounded = (
                    raw_proposals if resource_ledger is None else raw_proposals[: max(0, limit)]
                )
                for raw in bounded:
                    proposals.append(
                        raw
                        if isinstance(raw, PolicyProposal)
                        else _proposal_document(raw, resource_ledger, "l1_proposal_materialization")
                    )
        except Exception as error:
            if isinstance(error, MemoryError):
                if resource_ledger is not None:
                    resource_ledger.stop("max_memory_mb")
                raise
            provider_error = {"kind": type(error).__name__, "reason": str(error)}
        for raw in proposals[: max(0, limit)]:
            row: JsonObject = {"status": "rejected"}
            reserved_work = 0
            finished_work = False
            attempt_start = 0 if resource_ledger is None else resource_ledger.work.used
            try:
                with (
                    nullcontext()
                    if resource_ledger is None
                    else resource_ledger.phase("l1_validation")
                ):
                    row["proposal"] = _proposal_document(raw, resource_ledger, "l1_proposal_record")
                    proposal, updated = (
                        self.validate(raw, source_snapshot, config)
                        if resource_ledger is None
                        else self.validate(
                            raw, source_snapshot, config, resource_ledger=resource_ledger
                        )
                    )
                    if not isinstance(target, Source):
                        _revalidate_source(source_snapshot, resource_ledger)
                    if resource_ledger is not None:
                        resource_ledger.charge_json(updated, "l1_policy_identity")
                digest = stable_digest(updated)
                if digest in seen:
                    raise ConfigurationError("Repeated L1 policy")
                seen.add(digest)
                remaining_work = (
                    require_int(resource_limits["max_work"]) - charged_work
                    if resource_ledger is None
                    else resource_ledger.remaining_work
                )
                if remaining_work <= 0:
                    raise ConfigurationError("No remaining global L1 work budget")
                updated_resources = require_object(updated["resources"])
                updated_resources["max_work"] = min(
                    require_int(updated_resources["max_work"]), remaining_work
                )
                seconds = require_number(resource_limits["max_seconds"])
                if resource_ledger is not None:
                    remaining_seconds = resource_ledger.remaining_seconds
                    if remaining_seconds is not None:
                        resource_ledger.checkpoint()
                        if remaining_seconds <= 0:
                            raise ConfigurationError("No remaining global L1 time budget")
                        proposed_seconds = require_number(updated_resources["max_seconds"])
                        updated_resources["max_seconds"] = (
                            min(proposed_seconds, remaining_seconds)
                            if proposed_seconds
                            else remaining_seconds
                        )
                elif seconds:
                    remaining_seconds = seconds - baseline_wall - (time.monotonic() - started)
                    if remaining_seconds <= 0:
                        raise ConfigurationError("No remaining global L1 time budget")
                    proposed_seconds = require_number(updated_resources["max_seconds"])
                    updated_resources["max_seconds"] = (
                        min(proposed_seconds, remaining_seconds)
                        if proposed_seconds
                        else remaining_seconds
                    )
                before = _quality(selected) if resource_ledger is None else selected_quality
                rerun_started = time.monotonic()
                reserved_work = require_int(updated_resources["max_work"])
                with (
                    nullcontext() if resource_ledger is None else resource_ledger.phase("rerun"),
                    nullcontext()
                    if resource_ledger is None
                    else resource_ledger.work_scope(
                        reserved_work,
                        "l1_rerun",
                        max_seconds=require_number(updated_resources["max_seconds"]),
                        max_memory_mb=require_number(updated_resources["max_memory_mb"]),
                    ),
                ):
                    if resource_ledger is not None:
                        resource_ledger.charge_work(1, "l1_rerun_request")
                    rerun_request = resolve_request(
                        target, config=updated, queries=queries, output=output
                    )
                    prepared_session = (
                        AnalysisSession.from_snapshot(rerun_request, source_snapshot)
                        if resource_ledger is None
                        else AnalysisSession.from_snapshot(
                            rerun_request, source_snapshot, resource_ledger=resource_ledger
                        )
                    )
                    with prepared_session as session:
                        result = session.execute()
                    after = result.raw_snapshot()
                charged_work = (
                    charged_work + _work(after)
                    if resource_ledger is None
                    else resource_ledger.work.used
                )
                finished_work = True
                if resource_ledger is not None:
                    resource_ledger.charge_json(after.get("values", {}), "l1_quality")
                after_solver = after.get("solver_status", "failed")
                after_coverage = after.get("coverage_status", "open")
                after_quality = _quality(after)
                row.update(
                    status="accepted",
                    policy_digest=digest,
                    before=before,
                    after=after_quality,
                    seconds=time.monotonic() - rerun_started,
                    solver_status=after_solver,
                    coverage_status=after_coverage,
                )
                row["proposal_policy_digest"] = digest
                row["policy_digest"] = after.get("policy_digest", digest)
                row["phase_trace"] = after.get("phase_trace", [])
                if after_solver == "converged" and (
                    selected.get("coverage_status") != "closed_under_profile"
                    or after_coverage == "closed_under_profile"
                ):
                    selected = after
                    selected_quality = after_quality
                else:
                    row.update(
                        status="retained_baseline",
                        reason="Reanalysis did not retain the established convergence/coverage",
                    )
            except Exception as error:
                if isinstance(error, MemoryError):
                    if resource_ledger is not None:
                        resource_ledger.stop("max_memory_mb")
                    raise
                row["reason"] = str(error)
                row["error_kind"] = type(error).__name__
                if reserved_work:
                    row["status"] = "retained_baseline"
                    if not finished_work:
                        if resource_ledger is None:
                            charged_work += reserved_work
                            row["reserved_failed_work"] = reserved_work
                        else:
                            charged_work = resource_ledger.work.used
                            row["rerun_work_limit"] = reserved_work
                            row["failed_work_count_status"] = "measured_shared_ledger"
            if resource_ledger is not None:
                row["charged_attempt_work"] = resource_ledger.work.used - attempt_start
            rows.append(row)
            if resource_ledger is not None and (
                resource_ledger.stop_reason is not None or resource_ledger.remaining_work <= 0
            ):
                break
        if resource_ledger is not None:
            charged_work = resource_ledger.work.used
        return selected, {
            "provider": llm["provider"],
            "proposals": rows,
            "seconds": time.monotonic() - started,
            "baseline_quality": _quality(baseline) if resource_ledger is None else baseline_quality,
            "selected_quality": _quality(selected) if resource_ledger is None else selected_quality,
            "attempted": len(rows),
            "limit": limit,
            "clean_reanalysis": True,
            "provider_error": provider_error,
            "baseline_work": baseline_work,
            "total_work": charged_work,
            "reanalysis_work": charged_work - baseline_work,
            "total_work_scope": "controller_return_before_outer_publication"
            if resource_ledger is not None
            else "legacy_supplied_baseline_plus_child_reported_or_reserved_work",
            "reanalysis_work_scope": "All post-baseline L1 control, validation, rerun and paid read work before outer publication"
            if resource_ledger is not None
            else "Legacy supplied baseline plus child reported or conservatively reserved work",
        }
