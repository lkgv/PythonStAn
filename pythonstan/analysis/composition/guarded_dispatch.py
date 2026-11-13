"""Optional trusted bridge from checked relational summaries to AI flows.

Only independently validated finite source-module calls receive complete guard
evidence. Partial solver stores are checked for consistency, never promoted to
point-input coverage. Unsupported cases return None to the ordinary engine.
"""

from __future__ import annotations

import ast
import hashlib
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Literal, TypedDict, assert_never

from pythonstan.analysis.composition.summary_context import (
    SummaryApplicationContext as SummaryApplicationContext,
)
from pythonstan.analysis.composition.summary_context import (
    SummaryChargeCategory as SummaryChargeCategory,
)
from pythonstan.analysis.composition.summary_context import (
    SummaryChargePort as SummaryChargePort,
)
from pythonstan.analysis.composition.summary_context import (
    SummaryHostId as SummaryHostId,
)
from pythonstan.analysis.contracts import SourceModule, stable_digest
from pythonstan.analysis.contracts.identity import IdentityEpoch, ObjectId
from pythonstan.analysis.contracts.identity import ProgramPointId as ProgramPointId
from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    freeze_json_object,
    require_int,
    require_object,
    require_string,
)
from pythonstan.analysis.domains.relations import PartitionToken
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.state.effects import HeapWrite
from pythonstan.analysis.state.flow import (
    ExceptionValue,
    Flow,
    NormalExprFlow,
    RaiseFlow,
)
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.metadata import ContainerMeta, Metadata
from pythonstan.analysis.state.store import Frame, HeapObject, Store
from pythonstan.analysis.verification.guarded_dispatch.apply import (
    apply_verified_summary,
    guard_from_source_call,
)
from pythonstan.analysis.verification.guarded_dispatch.checker import (
    VerifiedSummary,
    check_source_candidate,
)
from pythonstan.analysis.verification.guarded_dispatch.language import parse_identity
from pythonstan.analysis.verification.guarded_dispatch.obligations import (
    SourceCall,
    VerificationBudget,
)
from pythonstan.llm.candidates import CandidateProvider, SourceBundle

type WorkMetric = Literal[
    "proposal_work",
    "checker_work",
    "bridge_setup_work",
    "runtime_validation_work",
    "application_work",
    "state_invalidation_work",
]
type DurationMetric = Literal["proposer_seconds", "checker_seconds", "application_seconds"]
WORK_METRICS: tuple[WorkMetric, ...] = (
    "proposal_work",
    "checker_work",
    "bridge_setup_work",
    "runtime_validation_work",
    "application_work",
    "state_invalidation_work",
)
SETUP_METRICS: tuple[WorkMetric, ...] = ("proposal_work", "checker_work", "bridge_setup_work")
DURATION_METRICS: tuple[DurationMetric, ...] = (
    "proposer_seconds",
    "checker_seconds",
    "application_seconds",
)


class DispatchStatistics(TypedDict):
    proposals: int
    accepted: int
    rejected: int
    unknown: int
    unsupported: int
    proposal_work: int
    checker_work: int
    application_work: int
    bridge_setup_work: int
    runtime_validation_work: int
    proposer_seconds: float
    checker_seconds: float
    application_seconds: float
    applications: int
    fallbacks: int
    charged_to_driver_work: int
    state_invalidations: int
    state_invalidation_work: int


@dataclass(frozen=True)
class UnchangedSummaryEpoch:
    work_precharged: bool
    work: int = 0
    unchanged: Literal[True] = field(default=True, init=False)


@dataclass(frozen=True)
class RevokedSummaryEpoch:
    """Detached transaction builder; commit transfers ownership of its maps."""

    epoch: IdentityEpoch
    programs: dict[str, VerifiedSummary]
    records: dict[str, JsonObject]
    retired: set[ObjectId]
    stats: DispatchStatistics
    work: int
    work_precharged: bool
    unchanged: Literal[False] = field(default=False, init=False)


type SummaryEpochPreparation = UnchangedSummaryEpoch | RevokedSummaryEpoch


def _statistics_wire(stats: DispatchStatistics) -> JsonObject:
    return {
        "proposals": stats["proposals"],
        "accepted": stats["accepted"],
        "rejected": stats["rejected"],
        "unknown": stats["unknown"],
        "unsupported": stats["unsupported"],
        "proposal_work": stats["proposal_work"],
        "checker_work": stats["checker_work"],
        "application_work": stats["application_work"],
        "bridge_setup_work": stats["bridge_setup_work"],
        "runtime_validation_work": stats["runtime_validation_work"],
        "proposer_seconds": stats["proposer_seconds"],
        "checker_seconds": stats["checker_seconds"],
        "application_seconds": stats["application_seconds"],
        "applications": stats["applications"],
        "fallbacks": stats["fallbacks"],
        "charged_to_driver_work": stats["charged_to_driver_work"],
        "state_invalidations": stats["state_invalidations"],
        "state_invalidation_work": stats["state_invalidation_work"],
    }


def _obligations(record: JsonObject) -> list[str]:
    value = record.get("open_obligations", [])
    if not isinstance(value, list):
        raise TypeError("Checked property obligations must be an array")
    return [require_string(item) for item in value]


def _wire_text(value: FrozenJsonValue) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected text in checked application record")
    return value


def _returned_object(value: FrozenJsonValue) -> ObjectId:
    if not isinstance(value, Mapping):
        raise TypeError("Expected checked fresh return value")
    identities = value["objects"]
    if not isinstance(identities, tuple) or not identities:
        raise TypeError("Expected checked fresh object identities")
    return ObjectId(_wire_text(identities[0]))


def _literal_keys(values: Sequence[object]) -> tuple[str, ...] | None:
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or type(value) is not str:
            return None
        result.append(value)
    return tuple(result)


class _StableSourceCallArena:
    """One allocation per checked acyclic initializer call, stable on replay."""

    def __init__(self, scenario: str, module: str) -> None:
        self.prefix = f"checked-box:{scenario}:{module}"

    def allocate(self, allocation: str, point: str) -> ObjectId:
        return ObjectId(f"{self.prefix}:{allocation}:{point}")


class PreparationState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    PREPARED = "prepared"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


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
        self.programs: dict[str, VerifiedSummary] = {}
        self.source_calls: dict[str, SourceCall] = {}
        self._records: dict[str, JsonObject] = {}
        self._stats: DispatchStatistics = {
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
        self._charged_drivers: set[SummaryHostId] = set()
        self._state_epoch = IdentityEpoch(0)
        self._applied_allocation_ids: set[ObjectId] = set()
        self._retired_allocation_ids: set[ObjectId] = set()
        self._modules = (
            tuple(snapshot.values()) if isinstance(snapshot, Mapping) else tuple(snapshot.modules)
        )
        self._remaining_work = remaining_work
        self.required_property_points: list[str] = []
        self._preparation_state = PreparationState.NEW

    @property
    def preparation_state(self) -> PreparationState:
        return self._preparation_state

    def prepare(self) -> None:
        """Execute proposal/check work once; failed runs cannot be reused."""
        if self._preparation_state is PreparationState.PREPARED:
            return
        if self._preparation_state is not PreparationState.NEW:
            raise RuntimeError("Summary preparation is " + self._preparation_state.value)
        self._preparation_state = PreparationState.RUNNING
        try:
            self._prepare_candidates()
        except Exception:
            self._preparation_state = PreparationState.FAILED
            raise
        except BaseException:
            self._preparation_state = PreparationState.INTERRUPTED
            raise
        self._preparation_state = PreparationState.PREPARED

    def _ensure_prepared(self) -> None:
        if self._preparation_state is not PreparationState.PREPARED:
            raise RuntimeError("Summary provider is not prepared: " + self._preparation_state.value)

    def _prepare_candidates(self) -> None:
        modules = self._modules
        llm = require_object(self.config.get("llm", {}))
        resources = require_object(self.config.get("resources", {}))
        max_proposals = require_int(llm.get("max_proposals", 2))
        remaining = require_int(resources.get("max_work", 100000))
        if self._remaining_work is not None:
            remaining = min(remaining, max(0, self._remaining_work))
        visited_modules = set()
        for source in modules:
            if self._stats["proposals"] >= max_proposals or remaining <= 0:
                break
            visited_modules.add(source.module)
            self._stats["proposals"] += 1
            # Identity creation in the untrusted proposer is metadata hashing;
            # the checker independently compares the actual full source below.
            proposal = self.proposer.propose(source.source, max_work=remaining)
            self._stats["proposal_work"] += proposal.work
            self._stats["proposer_seconds"] += proposal.seconds
            remaining -= proposal.work
            if proposal.candidate is None:
                self._record_unknown(source.module, "proposal_unavailable")
                if proposal.provider == "openai_live":
                    from pythonstan.llm.openai_transport import FailureCode

                    safe_codes = {item.value for item in FailureCode}
                    record = self._records[source.module + ":candidate"]
                    record["provider"] = "openai_live"
                    record["provider_status"] = (
                        proposal.status
                        if proposal.status in {"blocked", "budget_exhausted", "unavailable"}
                        else "unavailable"
                    )
                    record["provider_diagnostics"] = [
                        code for code in proposal.diagnostics if code in safe_codes
                    ]
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
                wire_identity = require_object(wire["identity"])
                wire_identity["policy_digest"] = self.policy_digest
                identity = parse_identity(wire_identity)
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
                    max_work=max(0, min(require_int(llm.get("checker_budget", 10000)), remaining))
                ),
            )
            self._stats["checker_seconds"] += time.monotonic() - started
            self._stats["checker_work"] += result.checked_work
            remaining -= result.checked_work
            if not result.certified:
                if result.status == "rejected":
                    self._stats["rejected"] += 1
                elif result.status == "unsupported":
                    self._stats["unsupported"] += 1
                else:
                    self._stats["unknown"] += 1
                self._record_unknown(
                    source.module, "candidate_" + result.status, result.diagnostics
                )
                continue
            self._stats["accepted"] += 1
            certificate = result.certificate
            assert certificate is not None
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

    def _record_unknown(self, module: str, reason: str, diagnostics: Sequence[str] = ()) -> None:
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
        total = sum(self._stats[key] for key in WORK_METRICS)
        unpaid = max(0, total - self._stats["charged_to_driver_work"])
        return {
            **_statistics_wire(self._stats),
            "total_work": total,
            "attempted_work": total,
            "consumed_driver_work": self._stats["charged_to_driver_work"],
            "uncharged_attempted_work": unpaid,
            "work_count_status": "fully_charged" if not unpaid else "unpaid_reported_work",
            "state_work_scope": "Committed invalidations; aborted detached preparation is measured by the host reabstract_summary_work counter",
            "total_seconds": sum(self._stats[key] for key in DURATION_METRICS),
            "required_property_points": len(self.required_property_points),
        }

    @property
    def proof_records(self) -> tuple[FrozenJsonObject, ...]:
        return tuple(freeze_json_object(self._records[key]) for key in sorted(self._records))

    def prepare_state_epoch(
        self,
        epoch: IdentityEpoch,
        charge: Callable[[int], None] | None = None,
    ) -> SummaryEpochPreparation:
        """Prepare detached revocation; no host or provider state is published."""
        if epoch <= self._state_epoch:
            return UnchangedSummaryEpoch(work_precharged=charge is not None)
        work = 0

        def spend(amount: int) -> None:
            nonlocal work
            if charge is not None:
                charge(amount)
            work += amount

        spend(1 + len(self._stats) + len(self.programs))
        base_stats = self._stats.copy()
        records: dict[str, JsonObject] = {}
        for key, record in self._records.items():
            existing = _obligations(record)
            spend(1 + len(record) + len(existing))
            unique: dict[str, None] = {}
            for obligation in [
                *existing,
                "host address epoch changed; checked frame/fresh bounds revoked",
            ]:
                unique[obligation] = None
            obligations = list(unique)
            records[key] = {**record, "status": "unknown", "open_obligations": [*obligations]}
        spend(1 + len(self._retired_allocation_ids) + len(self._applied_allocation_ids))
        retired = self._retired_allocation_ids | self._applied_allocation_ids
        stats: DispatchStatistics = {
            **base_stats,
            "state_invalidations": base_stats["state_invalidations"] + 1,
            "state_invalidation_work": base_stats["state_invalidation_work"] + work,
            "charged_to_driver_work": base_stats["charged_to_driver_work"] + work,
        }
        return RevokedSummaryEpoch(
            IdentityEpoch(epoch), {}, records, retired, stats, work, charge is not None
        )

    def commit_state_epoch(
        self, prepared: SummaryEpochPreparation, *, charged: bool = True
    ) -> None:
        """Transfer prepared maps by assignment after the host budget checks."""
        match prepared:
            case UnchangedSummaryEpoch():
                return
            case RevokedSummaryEpoch():
                self._state_epoch = prepared.epoch
                self.programs = prepared.programs
                self._records = prepared.records
                self._retired_allocation_ids = prepared.retired
                self._stats = prepared.stats
                if not charged:
                    self._stats["charged_to_driver_work"] -= prepared.work
            case _:
                assert_never(prepared)

    def invalidate_state_epoch(self, epoch: int, driver: SummaryChargePort | None = None) -> None:
        """Legacy standalone ledger port; it exposes only the charge capability."""
        charge: Callable[[int], None] | None = None
        if driver is not None:
            ledger = driver

            def bill(amount: int) -> None:
                ledger.charge(amount, "llm_state_invalidation_work")

            charge = bill
        prepared = self.prepare_state_epoch(IdentityEpoch(epoch), charge=charge)
        self.commit_state_epoch(prepared, charged=driver is not None)

    def _charge(self, context: SummaryApplicationContext, amount: int) -> None:
        context.charge(amount, "llm_proposer_checker_application_work")
        self._stats["charged_to_driver_work"] += amount

    def charge_setup(self, context: SummaryApplicationContext) -> None:
        self._ensure_prepared()
        if context.host_token not in self._charged_drivers:
            self._charged_drivers.add(context.host_token)
            self._charge(context, sum(self._stats[key] for key in SETUP_METRICS))

    @staticmethod
    def _only_refs(value: Value) -> bool:
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

    def _table_consistent(
        self,
        context: SummaryApplicationContext,
        store: Store,
        value: Value,
        certificate: VerifiedSummary,
        module: str,
    ) -> bool:
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
            info = context.functions.get(next(iter(handler.refs)))
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

    def try_apply(
        self,
        context: SummaryApplicationContext,
        identifier: ObjectId,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        site: ast.AST | None,
    ) -> list[Flow] | None:
        started = time.monotonic()
        try:
            return self._try_apply(context, identifier, args, keywords, store, frame, site)
        finally:
            self._stats["application_seconds"] += time.monotonic() - started

    def _try_apply(
        self,
        context: SummaryApplicationContext,
        identifier: ObjectId,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        site: ast.AST | None,
    ) -> list[Flow] | None:
        self.charge_setup(context)
        epoch = context.current_epoch()
        if epoch != self._state_epoch:
            # A foreign host which skipped the transaction hook cannot retain
            # stale Verified records.  Charge revocation before publishing it.
            prepared = self.prepare_state_epoch(
                epoch, charge=lambda amount: context.charge(amount, "llm_state_invalidation_work")
            )
            self.commit_state_epoch(prepared)
            return None
        info = context.functions.get(identifier)
        if info is None or info.module not in self.programs or not frame.is_module:
            return None
        certificate = self.programs[info.module]
        if (
            info.qualname != info.module + "." + certificate.source_facts.dispatch
            or len(args) != 3
            or keywords
        ):
            return None
        point = context.point(site)
        source_call = self.source_calls.get(point)
        if source_call is None:
            return None
        validation_work = 2 + sum(1 for _ in ast.walk(info.node))
        validation_work += certificate.source_facts.source_nodes
        validation_work += sum(
            2 + sum(1 for _ in ast.walk(context.functions[ref].node))
            for table_ref in args[0].refs
            for handler in store.heap.get(table_ref, HeapObject("unknown")).fields.values()
            for ref in handler.refs
            if ref in context.functions
        )
        self._stats["runtime_validation_work"] += validation_work
        self._charge(context, validation_work)
        dispatch_dependency = next(
            item
            for item in certificate.source_facts.identity.dependencies
            if item.kind == "function" and item.identity == certificate.source_facts.dispatch
        )
        if (
            context.modules[info.module].digest != certificate.source_facts.identity.source_digest
            or context.policy_digest != certificate.source_facts.identity.policy_digest
            or context.model_digest != "pythonstan-builtins-v1"
            or hashlib.sha256(
                ast.dump(context.trees[info.module], include_attributes=False).encode()
            ).hexdigest()
            != certificate.source_facts.identity.ir_digest
            or hashlib.sha256(ast.dump(info.node, include_attributes=False).encode()).hexdigest()
            != dispatch_dependency.version
            or not self._table_consistent(context, store, args[0], certificate, info.module)
        ):
            self._stats["fallbacks"] += 1
            self._records[point]["status"] = "unknown"
            self._records[point]["open_obligations"] = [
                "runtime table/source/policy is inconsistent with independently checked guard"
            ]
            return None
        constants = args[1].exact_constants()
        keys = None if constants is None else _literal_keys(constants)
        if keys is None:
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
            arena=_StableSourceCallArena(context.scenario, info.module),
            max_work=max(0, context.remaining_work()),
            existing_objects=tuple(store.heap),
        )
        self._stats["application_work"] += application.work
        self._charge(context, application.work)
        if application.coverage_status != "closed_under_profile":
            self._stats["fallbacks"] += 1
            self._records[point]["status"] = "unknown"
            self._records[point]["open_obligations"] = list(application.obligations)
            return None
        self._stats["applications"] += 1
        result: list[Flow] = []
        dispatch_call = next(node for node in ast.walk(info.node) if isinstance(node, ast.Call))
        handler_site = context.point(dispatch_call)
        for outcome in application.outcomes:
            current = store.copy()
            if outcome["kind"] == "raise":
                context.record_exception("KeyError")
                result.append(
                    RaiseFlow(
                        StateHandle.capture(current),
                        ExceptionValue(Value.constant("KeyError"), "KeyError"),
                    )
                )
                continue
            handler = _wire_text(outcome["handler"])
            outcome_key = _wire_text(outcome["key"])
            context.record_call(info.qualname, info.module + "." + handler, handler_site)
            context.record_read(next(iter(args[0].refs)), repr(outcome_key), handler_site)
            for allocation in application.allocations:
                allocation_id = ObjectId(_wire_text(allocation["object"]))
                if allocation_id in self._retired_allocation_ids:
                    self._stats["fallbacks"] += 1
                    self._records[point] = {
                        **self._records[point],
                        "status": "unknown",
                        "open_obligations": [
                            "checked fresh identity was retired by address coarsening"
                        ],
                    }
                    return None
                self._applied_allocation_ids.add(allocation_id)
                current.heap[allocation_id] = HeapObject(
                    "dict",
                    {"value": args[2]},
                    metadata=Metadata.from_record(
                        "dict", ContainerMeta(length=1, fresh_source_call=point)
                    ),
                )
                current.record_write(HeapWrite(allocation_id, "value"))
                context.record_write(allocation_id, "value", point)
            relation = next(
                arm.relation for arm in certificate.candidate.arms if arm.key == outcome["key"]
            )
            if relation == "input":
                value = args[2]
            elif relation == "fresh_box":
                value = Value.ref(_returned_object(outcome["value"]))
            else:
                value = Value.unknown()
            handler_identifier = next(
                iter(store.heap[next(iter(args[0].refs))].fields[outcome_key].refs)
            )
            token = PartitionToken.make(
                point,
                outcome["key"],
                handler_identifier,
                display_name=info.module + "." + handler,
            )
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


__all__ = [
    "GuardedDispatchProvider",
    "PreparationState",
    "SummaryApplicationContext",
    "SummaryHostId",
    "SummaryChargeCategory",
    "SummaryEpochPreparation",
    "UnchangedSummaryEpoch",
    "RevokedSummaryEpoch",
]
