"""Finite L1 proposals with typed, source-preserving clean reanalysis."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
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


class RefinementController:
    proposer: StrategyProposer

    def __init__(self, proposer: StrategyProposer | None = None) -> None:
        self.proposer = OfflineStrategyProposer() if proposer is None else proposer

    def validate(
        self, raw: object, source_snapshot: SourceSnapshot, config: JsonObject
    ) -> tuple[PolicyProposal, JsonObject]:
        proposal = PolicyProposal.parse(raw)
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
        build_plan(source_snapshot, updated)
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
    ) -> tuple[JsonObject, JsonObject]:
        from pythonstan.session import AnalysisSession

        started = time.monotonic()
        rows: list[JsonValue] = []
        seen: set[str] = set()
        selected = baseline
        charged_work = _work(baseline)
        baseline_wall = require_number(
            require_object(baseline.get("stats", {})).get("total_wall_seconds", 0)
        )
        llm, resource_limits = require_object(config["llm"]), require_object(config["resources"])
        limit = min(
            require_int(llm["max_proposals"]), require_int(resource_limits["max_reanalyses"])
        )
        provider_error: JsonValue = None
        proposals: list[PolicyProposal | JsonValue] = []
        try:
            raw_proposals: object = self.proposer.propose(baseline, source_snapshot, config)
            if not isinstance(raw_proposals, (list, tuple)):
                raise ConfigurationError("L1 provider must return a finite proposal array")
            for raw in raw_proposals:
                proposals.append(raw if isinstance(raw, PolicyProposal) else validate_json(raw))
        except Exception as error:
            provider_error = {"kind": type(error).__name__, "reason": str(error)}
        for raw in proposals[: max(0, limit)]:
            row: JsonObject = {
                "status": "rejected",
                "proposal": raw.to_dict() if isinstance(raw, PolicyProposal) else raw,
            }
            reserved_work = 0
            finished_work = False
            try:
                proposal, updated = self.validate(raw, source_snapshot, config)
                if not isinstance(target, Source):
                    for module in source_snapshot.modules:
                        try:
                            current = hashlib.sha256(Path(module.filename).read_bytes()).hexdigest()
                        except OSError as error:
                            raise ConfigurationError(
                                "L1 source changed after the baseline snapshot"
                            ) from error
                        if current != module.original_bytes_digest:
                            raise ConfigurationError(
                                "L1 source changed after the baseline snapshot"
                            )
                digest = stable_digest(updated)
                if digest in seen:
                    raise ConfigurationError("Repeated L1 policy")
                seen.add(digest)
                remaining_work = require_int(resource_limits["max_work"]) - charged_work
                if remaining_work <= 0:
                    raise ConfigurationError("No remaining global L1 work budget")
                updated_resources = require_object(updated["resources"])
                updated_resources["max_work"] = min(
                    require_int(updated_resources["max_work"]), remaining_work
                )
                seconds = require_number(resource_limits["max_seconds"])
                if seconds:
                    remaining_seconds = seconds - baseline_wall - (time.monotonic() - started)
                    if remaining_seconds <= 0:
                        raise ConfigurationError("No remaining global L1 time budget")
                    proposed_seconds = require_number(updated_resources["max_seconds"])
                    updated_resources["max_seconds"] = (
                        min(proposed_seconds, remaining_seconds)
                        if proposed_seconds
                        else remaining_seconds
                    )
                before = _quality(selected)
                rerun_started = time.monotonic()
                reserved_work = require_int(updated_resources["max_work"])
                rerun_request = resolve_request(
                    target, config=updated, queries=queries, output=output
                )
                with AnalysisSession.from_snapshot(rerun_request, source_snapshot) as session:
                    result = session.execute()
                after = result.raw_snapshot()
                charged_work += _work(after)
                finished_work = True
                row.update(
                    status="accepted",
                    policy_digest=digest,
                    before=before,
                    after=_quality(after),
                    seconds=time.monotonic() - rerun_started,
                    solver_status=result.solver_status,
                    coverage_status=result.coverage_status,
                )
                row["proposal_policy_digest"] = digest
                row["policy_digest"] = after["policy_digest"]
                row["phase_trace"] = after.get("phase_trace", [])
                if result.solver_status == "converged" and (
                    selected.get("coverage_status") != "closed_under_profile"
                    or result.coverage_status == "closed_under_profile"
                ):
                    selected = after
                else:
                    row.update(
                        status="retained_baseline",
                        reason="Reanalysis did not retain the established convergence/coverage",
                    )
            except Exception as error:
                row["reason"] = str(error)
                row["error_kind"] = type(error).__name__
                if reserved_work and not finished_work:
                    charged_work += reserved_work
                    row.update(status="retained_baseline", reserved_failed_work=reserved_work)
            rows.append(row)
        return selected, {
            "provider": llm["provider"],
            "proposals": rows,
            "seconds": time.monotonic() - started,
            "baseline_quality": _quality(baseline),
            "selected_quality": _quality(selected),
            "attempted": len(rows),
            "limit": limit,
            "clean_reanalysis": True,
            "provider_error": provider_error,
            "baseline_work": _work(baseline),
            "total_work": charged_work,
            "reanalysis_work": charged_work - _work(baseline),
        }
