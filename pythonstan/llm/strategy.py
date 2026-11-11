"""Finite L1 policy proposals with validated, bounded clean reanalysis."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import copy
import hashlib
from pathlib import Path
import time

from pythonstan.analysis.contracts import stable_digest, freeze_snapshot, thaw_snapshot
from pythonstan.config import ConfigurationError, resolve_config
from pythonstan.analysis.composition.plan import build_plan

REGISTERED_STRATEGIES = {
    "key_partitions": {"enable_value_partition"},
    "string_capacity": {"max_const_strings"},
    "call_context": {"context_depth"},
}


@dataclass(frozen=True)
class PolicyProposal:
    query: object
    source_digest: str
    region: Mapping
    strategy: str
    parameters: Mapping
    resources: Mapping
    reason: str = ""

    def __post_init__(self):
        for name in ("query", "region", "parameters", "resources"):
            object.__setattr__(self, name, freeze_snapshot(getattr(self, name)))

    def to_dict(self):
        return {name: thaw_snapshot(getattr(self, name)) if name in {"query", "region", "parameters", "resources"}
                else getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def parse(cls, value):
        if not isinstance(value, Mapping): raise ConfigurationError("Policy proposal must be an object")
        allowed = set(cls.__dataclass_fields__)
        if set(value) - allowed: raise ConfigurationError(f"Forbidden policy proposal fields: {sorted(set(value)-allowed)}")
        required = allowed - {"reason"}
        if required - set(value): raise ConfigurationError(f"Missing policy proposal fields: {sorted(required-set(value))}")
        return cls(**dict(value))


def _quality(snapshot):
    """Report observable useful precision, without inventing a ground truth."""
    tops = 0
    relation_tokens = set()
    for module in snapshot.get("values", {}).values():
        for value in module.values():
            tops += bool(value.get("top") or value.get("top_sorts"))
            relation_tokens.update(tuple(sorted(row.items())) for row in value.get("relations", ()) if isinstance(row, dict) and all(not isinstance(x, (dict, list)) for x in row.values()))
    return {"top_answers": tops, "relation_rows": len(relation_tokens)}


class OfflineStrategyProposer:
    """A deterministic stand-in for an untrusted LLM, never a trusted planner."""
    def propose(self, snapshot, source_snapshot, config):
        explicit = config["llm"]["proposals"]
        if explicit: return list(explicit)
        if config["llm"]["provider"] == "adversarial":
            return [{"strategy": "delete_entry", "verified": True}]
        # A registered global policy is proposed only when current exported
        # relations actually contain a Cartesian approximation.
        ambiguous = any(not row.get("partitioned", False)
                        for module in snapshot.get("values", {}).values()
                        for value in module.values() for row in value.get("relations", ()))
        if not ambiguous: return []
        return [PolicyProposal(query="relations", source_digest=source_snapshot.source_digest,
                               region={"module": "*", "qualname": "*", "kind": "block"},
                               strategy="key_partitions", parameters={"enable_value_partition": True},
                               resources={}, reason="Exported key/handler rows contain an unpartitioned may product").to_dict()]


class RefinementController:
    def __init__(self, proposer=None):
        self.proposer = proposer or OfflineStrategyProposer()

    def validate(self, proposal, source_snapshot, config):
        proposal = PolicyProposal.parse(proposal) if not isinstance(proposal, PolicyProposal) else proposal
        if proposal.source_digest != source_snapshot.source_digest: raise ConfigurationError("Stale L1 source identity")
        if proposal.strategy not in REGISTERED_STRATEGIES: raise ConfigurationError("Unregistered L1 strategy")
        from pythonstan.results import normalize_query
        normalize_query(proposal.query)
        params = thaw_snapshot(proposal.parameters)
        if set(params) != REGISTERED_STRATEGIES[proposal.strategy]: raise ConfigurationError("L1 parameters do not match registered strategy")
        selector = thaw_snapshot(proposal.region)
        if not isinstance(selector, dict) or not selector: raise ConfigurationError("L1 requires a source-bound region")
        resources = thaw_snapshot(proposal.resources)
        if not isinstance(resources, dict) or set(resources) - {"max_work", "max_seconds", "max_memory_mb"}:
            raise ConfigurationError("L1 cannot change entry, model, provider, or retry budgets")
        for name, value in resources.items():
            if type(value) not in (int, float) or value <= 0 or value > config["resources"][name]:
                raise ConfigurationError("L1 resources must be positive and within the original budget")
        resolve_config(config={"engine": config["engine"], "abstract": params})
        updated = copy.deepcopy(config)
        updated["llm"]["level1"] = False
        updated["llm"]["enabled"] = bool(updated["llm"]["level2"])
        updated["resources"].update(resources)
        rule = {"selector": selector, "engine": "ai_primary", "abstract": params, "priority": 100,
                "name": "l1:" + proposal.strategy}
        updated["regions"] = [*updated["regions"], rule]
        build_plan(source_snapshot, updated)  # real conflict/SCC/source validation
        return proposal, updated

    def run(self, *, target, source_snapshot, config, queries, output, baseline):
        from pythonstan.session import AnalysisSession
        started = time.monotonic()
        rows = []
        seen = set()
        selected = baseline
        def work(snapshot):
            stats = snapshot.get("stats", {})
            return stats.get("total_work", stats.get("work", 0) + stats.get("pta_work", 0) + stats.get("native_work", 0))
        charged_work = work(baseline)
        baseline_wall = baseline.get("stats", {}).get("total_wall_seconds", 0)
        limit = min(config["llm"]["max_proposals"], config["resources"]["max_reanalyses"])
        provider_error = None
        try:
            proposals = self.proposer.propose(baseline, source_snapshot, config)
            if not isinstance(proposals, (list, tuple)):
                raise ConfigurationError("L1 provider must return a finite proposal array")
        except Exception as exc:
            proposals = []
            provider_error = {"kind": type(exc).__name__, "reason": str(exc)}
        for raw in proposals[:max(0, limit)]:
            row = {"status": "rejected", "proposal": raw.to_dict() if isinstance(raw, PolicyProposal) else raw}
            reserved_work = 0
            finished_work = False
            try:
                proposal, updated = self.validate(raw, source_snapshot, config)
                if getattr(target, "kind", "") != "source":
                    for module in source_snapshot.modules:
                        try:
                            current = hashlib.sha256(Path(module.filename).read_bytes()).hexdigest()
                        except OSError as exc:
                            raise ConfigurationError("L1 source changed after the baseline snapshot") from exc
                        if current != module.original_bytes_digest:
                            raise ConfigurationError("L1 source changed after the baseline snapshot")
                digest = stable_digest(updated)
                if digest in seen: raise ConfigurationError("Repeated L1 policy")
                seen.add(digest)
                remaining_work = config["resources"]["max_work"] - charged_work
                if remaining_work <= 0: raise ConfigurationError("No remaining global L1 work budget")
                updated["resources"]["max_work"] = min(updated["resources"]["max_work"], remaining_work)
                if config["resources"]["max_seconds"]:
                    remaining_seconds = config["resources"]["max_seconds"] - baseline_wall - (time.monotonic()-started)
                    if remaining_seconds <= 0: raise ConfigurationError("No remaining global L1 time budget")
                    proposed_seconds = updated["resources"]["max_seconds"]
                    updated["resources"]["max_seconds"] = min(proposed_seconds, remaining_seconds) if proposed_seconds else remaining_seconds
                before = _quality(selected)
                rerun_started = time.monotonic()
                reserved_work = updated["resources"]["max_work"]
                # Execute the exact frozen source inventory with fresh AST and
                # state. A later filesystem edit cannot alter the proposal's
                # source identity between validation and execution.
                session = AnalysisSession.from_snapshot(source_snapshot, target=target,
                            config=updated, queries=queries, output=output)
                result = session.execute()
                after = result.raw_snapshot()
                charged_work += work(after)
                finished_work = True
                row.update(status="accepted", policy_digest=digest, before=before, after=_quality(after),
                           seconds=time.monotonic()-rerun_started, solver_status=result.solver_status,
                           coverage_status=result.coverage_status)
                row["proposal_policy_digest"] = digest
                row["policy_digest"] = after["policy_digest"]
                row["phase_trace"] = after.get("phase_trace", [])
                # Adoption does not close obligations. Results are a complete
                # clean rerun under the new policy, never pruning old facts.
                if result.solver_status == "converged" and (
                        selected.get("coverage_status") != "closed_under_profile" or result.coverage_status == "closed_under_profile"):
                    selected = after
                else:
                    row.update(status="retained_baseline", reason="Reanalysis did not retain the established convergence/coverage")
            except Exception as exc:
                row["reason"] = str(exc)
                row["error_kind"] = type(exc).__name__
                if reserved_work and not finished_work:
                    # An aborted engine/export does not have a trustworthy
                    # final counter. Reserve its entire authorized window so
                    # another proposal cannot reuse unaccounted work.
                    charged_work += reserved_work
                    row.update(status="retained_baseline", reserved_failed_work=reserved_work)
            rows.append(row)
        return selected, {"provider": config["llm"]["provider"], "proposals": rows,
                          "seconds": time.monotonic()-started,
                          "baseline_quality": _quality(baseline), "selected_quality": _quality(selected),
                          "attempted": len(rows), "limit": limit, "clean_reanalysis": True,
                          "provider_error": provider_error,
                          "baseline_work": work(baseline), "total_work": charged_work,
                          "reanalysis_work": charged_work-work(baseline)}
