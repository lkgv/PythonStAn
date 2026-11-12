"""Optional trusted bridge from checked relational summaries to AI flows.

Only independently validated finite source-module calls receive complete guard
evidence. Partial solver stores are checked for consistency, never promoted to
point-input coverage. Unsupported cases return None to the ordinary engine.
"""

from __future__ import annotations

import ast
import hashlib
import time
from collections.abc import Mapping
from dataclasses import replace

from pythonstan.analysis.contracts import SourceModule, stable_digest
from pythonstan.analysis.contracts.identity import ObjectId
from pythonstan.analysis.contracts.json_data import FrozenJsonObject, JsonObject, freeze_json_object
from pythonstan.analysis.domains.relations import PartitionToken
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.state import HeapObject
from pythonstan.analysis.state.effects import HeapWrite
from pythonstan.analysis.state.flow import (
    ExceptionValue,
    NormalExprFlow,
    RaiseFlow,
)
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.verification.guarded_dispatch.apply import (
    apply_verified_summary,
    guard_from_source_call,
)
from pythonstan.analysis.verification.guarded_dispatch.checker import check_source_candidate
from pythonstan.analysis.verification.guarded_dispatch.language import parse_identity
from pythonstan.analysis.verification.guarded_dispatch.obligations import VerificationBudget
from pythonstan.llm.candidates import CandidateProvider, SourceBundle


class _StableSourceCallArena:
    """One allocation per checked acyclic initializer call, stable on replay."""

    def __init__(self, scenario: str, module: str) -> None:
        self.prefix = f"checked-box:{scenario}:{module}"

    def allocate(self, allocation: str, point: str) -> ObjectId:
        return ObjectId(f"{self.prefix}:{allocation}:{point}")


class GuardedDispatchProvider:
    def __init__(
        self,
        snapshot: SourceBundle | Mapping[str, SourceModule],
        config: JsonObject,
        proposer: CandidateProvider,
        remaining_work: int | None = None,
    ) -> None:
        self.config = config
        self.policy_digest = stable_digest(config)
        self.proposer = proposer
        self.programs = {}
        self.source_calls = {}
        self._records: dict[str, JsonObject] = {}
        self._stats: dict[str, int | float] = {
            "proposals": 0,
            "accepted": 0,
            "rejected": 0,
            "unknown": 0,
            "unsupported": 0,
            "proposal_work": 0,
            "checker_work": 0,
            "application_work": 0,
            "bridge_setup_work": 0,
            "runtime_validation_work": 0,
            "proposer_seconds": 0.0,
            "checker_seconds": 0.0,
            "application_seconds": 0.0,
            "applications": 0,
            "fallbacks": 0,
            "charged_to_driver_work": 0,
            "state_invalidations": 0,
            "state_invalidation_work": 0,
        }
        self._charged_drivers = set()
        self._state_epoch = 0
        self._applied_allocation_ids = set()
        self._retired_allocation_ids = set()
        modules = (
            tuple(snapshot.values()) if isinstance(snapshot, Mapping) else tuple(snapshot.modules)
        )
        self.required_property_points: list[str] = []
        max_proposals = config.get("llm", {}).get("max_proposals", 2)
        remaining = config.get("resources", {}).get("max_work", 100000)
        if remaining_work is not None:
            remaining = min(remaining, max(0, remaining_work))
        visited_modules = set()
        for source in modules:
            if self._stats["proposals"] >= max_proposals or remaining <= 0:
                break
            visited_modules.add(source.module)
            self._stats["proposals"] += 1
            # Identity creation in the untrusted proposer is metadata hashing;
            # the checker independently compares the actual full source below.
            proposal = proposer.propose(source.source, max_work=remaining)
            self._stats["proposal_work"] += proposal.work
            self._stats["proposer_seconds"] += proposal.seconds
            remaining -= proposal.work
            if proposal.candidate is None:
                self._record_unknown(source.module, "proposal_unavailable")
                self._stats["unknown"] += 1
                continue
            from pythonstan.analysis.verification.guarded_dispatch.language import (
                candidate_to_dict,
                parse_candidate,
            )

            # Bind the host policy version. Source/IR/model/dependency data still
            # originates in the untrusted proposal and must pass the checker.
            try:
                wire = candidate_to_dict(parse_candidate(proposal.candidate))
                wire["identity"]["policy_digest"] = self.policy_digest
                identity = parse_identity(wire["identity"])
            except (ValueError, TypeError, KeyError):
                self._stats["rejected"] += 1
                self._record_unknown(source.module, "malformed_candidate")
                continue
            started = time.monotonic()
            result = check_source_candidate(
                source.source,
                wire,
                identity=identity,
                budget=VerificationBudget(
                    max_work=max(
                        0, min(config.get("llm", {}).get("checker_budget", 10000), remaining)
                    )
                ),
            )
            self._stats["checker_seconds"] += time.monotonic() - started
            self._stats["checker_work"] += result.checked_work
            remaining -= result.checked_work
            if not result.certified:
                key = (
                    result.status
                    if result.status in ("rejected", "unknown", "unsupported")
                    else "unknown"
                )
                self._stats[key] += 1
                self._record_unknown(
                    source.module, "candidate_" + result.status, result.diagnostics
                )
                continue
            self._stats["accepted"] += 1
            certificate = result.certificate
            bridge_work = len(certificate.source_facts.calls) + len(
                certificate.source_facts.handlers
            )
            if bridge_work > remaining:
                self._stats["bridge_setup_work"] += max(0, remaining)
                remaining = 0
                self._record_unknown(source.module, "bridge setup work budget exhausted")
                continue
            self._stats["bridge_setup_work"] += bridge_work
            remaining -= bridge_work
            self.programs[source.module] = certificate
            for call in certificate.source_facts.calls:
                point = f"{source.module}:{call.point}"
                self.source_calls[point] = call
                self.required_property_points.append(point)
                self._records[point] = {
                    "property": "guarded_dispatch_return_relation",
                    "module": source.module,
                    "point": point,
                    "symbol": call.target,
                    "status": "unknown",
                    "profile": "guarded-dispatch-v1",
                    "source_digest": source.digest,
                    "open_obligations": ["checked source call has not yet been applied"],
                    "certificate_digest": certificate.proof_digest,
                }
        for source in modules:
            if source.module not in visited_modules:
                self._record_unknown(
                    source.module, "module not proposed/checked under L2 resource budget"
                )

    def _record_unknown(self, module, reason, diagnostics=()):
        key = module + ":candidate"
        self._records[key] = {
            "property": "guarded_dispatch_return_relation",
            "module": module,
            "point": None,
            "status": "unknown",
            "profile": "guarded-dispatch-v1",
            "open_obligations": [reason, *diagnostics],
        }

    @property
    def statistics(self) -> JsonObject:
        total = sum(
            self._stats[key]
            for key in (
                "proposal_work",
                "checker_work",
                "bridge_setup_work",
                "runtime_validation_work",
                "application_work",
                "state_invalidation_work",
            )
        )
        unpaid = max(0, total - self._stats["charged_to_driver_work"])
        return {
            **self._stats,
            "total_work": total,
            "attempted_work": total,
            "consumed_driver_work": self._stats["charged_to_driver_work"],
            "uncharged_attempted_work": unpaid,
            "work_count_status": "fully_charged" if not unpaid else "unpaid_reported_work",
            "state_work_scope": "Committed invalidations; aborted detached preparation is measured by the host reabstract_summary_work counter",
            "total_seconds": sum(
                self._stats[key]
                for key in ("proposer_seconds", "checker_seconds", "application_seconds")
            ),
            "required_property_points": len(self.required_property_points),
        }

    @property
    def proof_records(self) -> tuple[FrozenJsonObject, ...]:
        return tuple(freeze_json_object(self._records[key]) for key in sorted(self._records))

    def prepare_state_epoch(self, epoch, charge=None):
        """Detach revocation before the host charges and atomically coarsens.

        A checked acyclic source call has a stable fresh allocation name on
        equation replay.  Once that name has been summarized it cannot be
        reintroduced as a singleton by replaying the certificate.  Retiring
        this provider's applications is covering: the ordinary shared engine
        executes subsequent calls with the host's new address abstraction.
        """
        if epoch <= self._state_epoch:
            return {"unchanged": True, "work": 0, "work_precharged": charge is not None}
        work = 0

        def spend(amount):
            nonlocal work
            if charge is not None:
                charge(amount)
            work += amount

        spend(1 + len(self._stats) + len(self.programs))
        base_stats = dict(self._stats)
        records = {}
        for key, record in self._records.items():
            spend(1 + len(record) + len(record.get("open_obligations", ())))
            obligations = list(
                dict.fromkeys(
                    [
                        *record.get("open_obligations", ()),
                        "host address epoch changed; checked frame/fresh bounds revoked",
                    ]
                )
            )
            records[key] = {**record, "status": "unknown", "open_obligations": [*obligations]}
        spend(1 + len(self._retired_allocation_ids) + len(self._applied_allocation_ids))
        retired = self._retired_allocation_ids | self._applied_allocation_ids
        stats = {
            **base_stats,
            "state_invalidations": base_stats["state_invalidations"] + 1,
            "state_invalidation_work": base_stats["state_invalidation_work"] + work,
            "charged_to_driver_work": base_stats["charged_to_driver_work"] + work,
        }
        return {
            "unchanged": False,
            "epoch": epoch,
            "programs": {},
            "records": records,
            "retired": retired,
            "stats": stats,
            "work": work,
            "work_precharged": charge is not None,
        }

    def commit_state_epoch(self, prepared, *, charged=True):
        # Assignment only.  All allocation and host budget checks happened in
        # the detached prepare phase, before any address aliases were exposed.
        if prepared["unchanged"]:
            return
        self._state_epoch = prepared["epoch"]
        self.programs = prepared["programs"]
        self._records = prepared["records"]
        self._retired_allocation_ids = prepared["retired"]
        self._stats = prepared["stats"]
        if not charged:
            self._stats["charged_to_driver_work"] -= prepared["work"]

    def invalidate_state_epoch(self, epoch, driver=None):
        """Retire authority; standalone calls do not fabricate billed work."""
        charge = (
            (lambda amount: driver.charge(amount, "llm_state_invalidation_work"))
            if driver is not None
            else None
        )
        prepared = self.prepare_state_epoch(epoch, charge=charge)
        self.commit_state_epoch(prepared, charged=driver is not None)

    def _charge(self, driver, amount):
        driver.charge(amount, "llm_proposer_checker_application_work")
        self._stats["charged_to_driver_work"] += amount

    def charge_setup(self, driver):
        """Called inside Driver.run's budget boundary even with zero calls."""
        if id(driver) not in self._charged_drivers:
            self._charged_drivers.add(id(driver))
            self._charge(
                driver,
                sum(
                    self._stats[key]
                    for key in ("proposal_work", "checker_work", "bridge_setup_work")
                ),
            )

    @staticmethod
    def _only_refs(value):
        return (
            bool(value.refs)
            and not (
                value.top
                or value.top_sorts
                or value.unbound
                or value.bools
                or value.floats
                or value.none
            )
            and value.ints.is_bottom
            and value.strings.is_bottom()
        )

    def _table_consistent(self, driver, store, value, certificate, module):
        if not self._only_refs(value) or len(value.refs) != 1:
            return False
        table = store.heap.get(next(iter(value.refs)))
        if (
            table is None
            or table.kind != "dict"
            or not table.singleton
            or table.havoc
            or table.maybe_missing
            or not table.default.is_bottom()
            or set(table.fields) != {key for key, _ in certificate.source_facts.table}
        ):
            return False
        for key, name in certificate.source_facts.table:
            handler = table.fields[key]
            if not self._only_refs(handler) or len(handler.refs) != 1:
                return False
            info = driver.functions.get(next(iter(handler.refs)))
            function = store.heap.get(next(iter(handler.refs)))
            fact = next(item for item in certificate.source_facts.handlers if item.name == name)
            if (
                function is None
                or function.kind != "function"
                or function.havoc
                or not function.singleton
                or info is None
                or info.qualname != module + "." + name
                or hashlib.sha256(
                    ast.dump(info.node, include_attributes=False).encode()
                ).hexdigest()
                != fact.digest
            ):
                return False
        return True

    def try_apply(self, driver, identifier, args, keywords, store, frame, site):
        started = time.monotonic()
        try:
            return self._try_apply(driver, identifier, args, keywords, store, frame, site)
        finally:
            self._stats["application_seconds"] += time.monotonic() - started

    def _try_apply(self, driver, identifier, args, keywords, store, frame, site):
        self.charge_setup(driver)
        epoch = getattr(getattr(driver.ops, "arena", None), "epoch", 0)
        if epoch != self._state_epoch:
            # A foreign host which skipped the transaction hook cannot retain
            # stale Verified records.  Charge revocation before publishing it.
            prepared = self.prepare_state_epoch(
                epoch, charge=lambda amount: driver.charge(amount, "llm_state_invalidation_work")
            )
            self.commit_state_epoch(prepared)
            return None
        info = driver.functions.get(identifier)
        if info is None or info.module not in self.programs or not frame.is_module:
            return None
        certificate = self.programs[info.module]
        if (
            info.qualname != info.module + "." + certificate.source_facts.dispatch
            or len(args) != 3
            or keywords
        ):
            return None
        point = driver.point(site)
        source_call = self.source_calls.get(point)
        if source_call is None:
            return None
        validation_work = 2 + sum(1 for _ in ast.walk(info.node))
        validation_work += certificate.source_facts.source_nodes
        validation_work += sum(
            2 + sum(1 for _ in ast.walk(driver.functions[ref].node))
            for table_ref in args[0].refs
            for handler in store.heap.get(table_ref, HeapObject("unknown")).fields.values()
            for ref in handler.refs
            if ref in driver.functions
        )
        self._stats["runtime_validation_work"] += validation_work
        self._charge(driver, validation_work)
        dispatch_dependency = next(
            item
            for item in certificate.source_facts.identity.dependencies
            if item.kind == "function" and item.identity == certificate.source_facts.dispatch
        )
        if (
            driver.modules[info.module].digest != certificate.source_facts.identity.source_digest
            or driver.policy_digest != certificate.source_facts.identity.policy_digest
            or driver.model_digest != "pythonstan-builtins-v1"
            or hashlib.sha256(
                ast.dump(driver.trees[info.module], include_attributes=False).encode()
            ).hexdigest()
            != certificate.source_facts.identity.ir_digest
            or hashlib.sha256(ast.dump(info.node, include_attributes=False).encode()).hexdigest()
            != dispatch_dependency.version
            or not self._table_consistent(driver, store, args[0], certificate, info.module)
        ):
            self._stats["fallbacks"] += 1
            self._records[point]["status"] = "unknown"
            self._records[point]["open_obligations"] = [
                "runtime table/source/policy is inconsistent with independently checked guard"
            ]
            return None
        keys = args[1].exact_constants()
        if keys is None or any(type(key) is not str for key in keys):
            self._stats["fallbacks"] += 1
            return None
        evidence = guard_from_source_call(certificate, source_call.target)
        application = apply_verified_summary(
            certificate,
            evidence,
            identity=certificate.source_facts.identity,
            point=source_call.point,
            table_digest=certificate.source_facts.table_digest,
            keys=tuple(keys),
            payload=args[2].export(),
            arena=_StableSourceCallArena(driver.scenario, info.module),
            max_work=max(0, driver.max_work - driver.work),
            existing_objects=tuple(store.heap),
        )
        self._stats["application_work"] += application.work
        self._charge(driver, application.work)
        if application.coverage_status != "closed_under_profile":
            self._stats["fallbacks"] += 1
            self._records[point]["status"] = "unknown"
            self._records[point]["open_obligations"] = list(application.obligations)
            return None
        self._stats["applications"] += 1
        result = []
        source_handlers = {handler.name: handler for handler in certificate.source_facts.handlers}
        dispatch_call = next(node for node in ast.walk(info.node) if isinstance(node, ast.Call))
        handler_site = driver.point(dispatch_call)
        for outcome in application.outcomes:
            current = store.copy()
            if outcome["kind"] == "raise":
                driver.exceptions.add("KeyError")
                result.append(
                    RaiseFlow(
                        StateHandle.capture(current),
                        ExceptionValue(Value.constant("KeyError"), "KeyError"),
                    )
                )
                continue
            handler = outcome["handler"]
            driver.calls.add((info.qualname, info.module + "." + handler, handler_site))
            driver.reads.add((next(iter(args[0].refs)), repr(outcome["key"]), handler_site))
            for allocation in application.allocations:
                if allocation["object"] in self._retired_allocation_ids:
                    self._stats["fallbacks"] += 1
                    self._records[point] = {
                        **self._records[point],
                        "status": "unknown",
                        "open_obligations": [
                            "checked fresh identity was retired by address coarsening"
                        ],
                    }
                    return None
                self._applied_allocation_ids.add(allocation["object"])
                current.heap[allocation["object"]] = HeapObject(
                    "dict", {"value": args[2]}, metadata={"length": 1, "fresh_source_call": point}
                )
                current.record_write(HeapWrite(allocation["object"], "value"))
                driver.writes.add((allocation["object"], "value", point))
            relation = next(
                arm.relation for arm in certificate.candidate.arms if arm.key == outcome["key"]
            )
            if relation == "input":
                value = args[2]
            elif relation == "fresh_box":
                value = Value.ref(outcome["value"]["objects"][0])
            else:
                value = Value.unknown()
            token = PartitionToken.make(point, outcome["key"], info.module + "." + handler)
            value = replace(value, relations=value.relations | frozenset((token,)))
            result.append(NormalExprFlow(StateHandle.capture(current), value))
        self._records[point] = {
            **self._records[point],
            "status": application.proof_status,
            "input_bound_digest": evidence.input_bound_digest,
            "coverage_origin": evidence.origin,
            "identity_digest": evidence.identity.digest,
            "open_obligations": list(application.obligations),
            "frame_bound_digest": application.frame_bound_digest,
            "checked_property": "key-handler-return/fresh-slot relation at the declared source call",
            "evidence": [certificate.proof_digest, evidence.proof_digest],
        }
        return result
