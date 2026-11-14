"""Owned procedure observations with staged generations and epoch preparation.

Unit exits can be borrowed while their real producer and state revisions remain
unchanged. Other producers pay for an owned detached graph. No observation is a
proof certificate, and no synthetic Unit is created to represent a summary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from threading import get_ident
from weakref import WeakSet

from pythonstan.analysis.contracts.cost_model import visit_json_text
from pythonstan.analysis.contracts.identity import (
    IdentityEpoch,
    SemanticIdentity,
    StateRevision,
    UnitId,
    VersionedDependency,
)
from pythonstan.analysis.contracts.procedure_facts import (
    FactPublication,
    ProcedureFactHead,
    ProcedureProducer,
)
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.analysis.state.flow import Flow
from pythonstan.analysis.state.store import Store, Unit
from pythonstan.analysis.state.transform import PayloadTransformer

type DependencyCheck = Callable[[VersionedDependency], bool]


@dataclass(frozen=True, slots=True)
class _StateStamp:
    root: Store
    epoch: IdentityEpoch
    revision: StateRevision


@dataclass(frozen=True, eq=False)
class _PayloadOwnership:
    owner: ProcedureObservationStore
    head: ProcedureFactHead
    outcomes: tuple[Flow, ...]
    stamps: tuple[_StateStamp, ...]
    unit: Unit | None = None
    unit_outcomes: list[Flow] | None = None


_OWNED_PAYLOADS: WeakSet[_PayloadOwnership] = WeakSet()


@dataclass(frozen=True, slots=True)
class ProcedurePayload:
    outcomes: tuple[Flow, ...]
    _ownership: _PayloadOwnership | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ObservationView:
    head: ProcedureFactHead
    payload: ProcedurePayload
    reason: str | None = None

    @property
    def covering(self) -> bool:
        return self.head.materialization == "complete"


@dataclass(frozen=True, slots=True)
class _Generation:
    number: int
    facts: Mapping[ProcedureProducer, FactPublication[ProcedurePayload]]


@dataclass(eq=False)
class PreparedGeneration:
    _store: ProcedureObservationStore
    _base_revision: int
    _epoch: IdentityEpoch
    _owner: str
    _generation: _Generation
    _validated: bool = False
    _committed: bool = False

    def validate(self, *, application_accepted: bool | None = None) -> None:
        self._validated = False
        self._store._validate_generation(self, application_accepted=application_accepted)
        self._validated = True

    def commit(self) -> None:
        self._store._commit_generation(self)


@dataclass(eq=False)
class PreparedObservationEpoch:
    _store: ProcedureObservationStore
    _base_revision: int
    _old_epoch: IdentityEpoch
    _new_epoch: IdentityEpoch
    _identity: SemanticIdentity
    _generations: dict[str, _Generation]
    _unit_owners: dict[UnitId, Unit]
    _validated: bool = False
    _committed: bool = False

    def validate(self) -> None:
        self._validated = False
        self._store._validate_epoch(self)
        self._validated = True

    def commit(self) -> None:
        self._store._commit_epoch(self)


class ProcedureObservationStore:
    def __init__(
        self,
        identity: SemanticIdentity,
        epoch: IdentityEpoch,
        resource_ledger: RunLedger,
        *,
        dependency_check: DependencyCheck | None = None,
    ) -> None:
        self.identity = identity
        self.epoch = epoch
        self.resource_ledger = resource_ledger
        self.dependency_check = dependency_check
        self.revision = 0
        self._owner_thread = get_ident()
        self._unit_owners: dict[UnitId, Unit] = {}
        self._generations: dict[str, _Generation] = {}
        self._started: dict[str, int] = {}
        self._generation_plans: WeakSet[PreparedGeneration] = WeakSet()
        self._epoch_plans: WeakSet[PreparedObservationEpoch] = WeakSet()

    def check_writer(self) -> None:
        if get_ident() != self._owner_thread:
            raise ValueError("Only the observation owner can publish semantic facts")

    def register_unit(self, unit: Unit) -> None:
        self.check_writer()
        self._text(unit.key, "procedure_unit_register")
        prior = self._unit_owners.get(unit.key)
        if prior is not None and prior is not unit:
            raise ValueError("A different Unit owns this registered identity")
        if prior is None:
            self._unit_owners[unit.key] = unit
            self.revision += 1

    def _text(self, text: str, category: str) -> None:
        with self.resource_ledger.structural_meter(category) as meter:
            visit_json_text(text, meter.visit)

    def _admit_head(self, head: ProcedureFactHead) -> None:
        self.resource_ledger.charge_work(1, "procedure_fact_header")
        with self.resource_ledger.structural_meter("procedure_fact_header") as meter:
            scope = head.producer.scope
            for text in (
                head.producer.owner,
                head.producer.identity,
                scope.module,
                scope.qualname,
                scope.scenario,
                head.identity.source_digest,
                head.identity.ir_digest,
                head.identity.model_digest,
                head.identity.policy_digest,
                head.identity.profile,
                head.identity.state_version,
                head.identity.python_version,
            ):
                visit_json_text(text, meter.visit)
            for point in scope.context:
                visit_json_text(point, meter.visit)
            if scope.site is not None:
                visit_json_text(scope.site, meter.visit)
            for dependencies in (head.identity.dependencies, head.dependencies):
                for dependency in dependencies:
                    for text in (dependency.kind, dependency.identity, dependency.version):
                        visit_json_text(text, meter.visit)

    def _compatible_head(self, head: ProcedureFactHead) -> None:
        if head.identity != self.identity or head.version.epoch != self.epoch:
            raise ValueError("Observation source/policy/model or epoch is stale")
        if not self._dependencies_current(head):
            raise ValueError("Observation dependencies are stale or unvalidated")

    def _dependencies_current(self, head: ProcedureFactHead) -> bool:
        for dependencies in (head.identity.dependencies, head.dependencies):
            for dependency in dependencies:
                self.resource_ledger.charge_work(1, "procedure_fact_dependency")
                if self.dependency_check is None:
                    return False
                try:
                    if not self.dependency_check(dependency):
                        return False
                except ResourceStop:
                    raise
                except Exception:
                    return False
        return True

    def _outcome_tuple(self, outcomes: Sequence[Flow]) -> tuple[Flow, ...]:
        admitted: list[Flow] = []
        for outcome in outcomes:
            self.resource_ledger.charge_work(1, "procedure_fact_edge")
            outcome.state.read()
            admitted.append(outcome)
        return tuple(admitted)

    def _issue(
        self, head: ProcedureFactHead, outcomes: tuple[Flow, ...], unit: Unit | None = None
    ) -> FactPublication[ProcedurePayload]:
        stamps: list[_StateStamp] = []
        for outcome in outcomes:
            self.resource_ledger.charge_work(1, "procedure_fact_stamp")
            root = outcome.state.read()
            if root.identity_epoch != head.version.epoch:
                raise ValueError("Observation flow has a different identity epoch")
            stamps.append(
                _StateStamp(root, IdentityEpoch(root.identity_epoch), root.state_revision)
            )
        receipt = _PayloadOwnership(
            self, head, outcomes, tuple(stamps), unit, None if unit is None else unit.outcomes
        )
        _OWNED_PAYLOADS.add(receipt)
        return FactPublication(head, ProcedurePayload(outcomes, receipt))

    def capture_unit(
        self, head: ProcedureFactHead, unit: Unit
    ) -> FactPublication[ProcedurePayload]:
        self.check_writer()
        self._admit_head(head)
        self._compatible_head(head)
        if self._unit_owners.get(unit.key) is not unit:
            raise ValueError("Unit is not registered to this observation owner")
        self._match_unit(head, unit)
        return self._issue(head, self._outcome_tuple(unit.outcomes), unit)

    @staticmethod
    def _match_unit(head: ProcedureFactHead, unit: Unit) -> None:
        scope = head.producer.scope
        if (
            head.producer.kind != "unit"
            or head.producer.identity != unit.key
            or scope.module != unit.frame.module
            or scope.qualname != unit.frame.qualname
            or scope.context != unit.frame.context
            or (scope.kind == "module") != unit.is_module
            or head.version.revision != unit.revision
            or head.version.generation != unit.visits
            or (head.materialization == "complete" and unit.visits == 0)
        ):
            raise ValueError("Observation does not describe this real Unit generation")

    def capture_detached(
        self, head: ProcedureFactHead, outcomes: Sequence[Flow]
    ) -> FactPublication[ProcedurePayload]:
        self.check_writer()
        self._admit_head(head)
        self._compatible_head(head)
        if head.producer.kind == "unit":
            raise ValueError("Unit facts require their actual Unit owner")
        selected = self._outcome_tuple(outcomes)
        with self.resource_ledger.structural_meter("procedure_fact_copy") as meter:
            copied = PayloadTransformer(charge=meter.visit).visit(selected)
        return self._issue(head, copied)

    def _payload_current(self, fact: FactPublication[ProcedurePayload]) -> bool:
        receipt = fact.payload._ownership
        if (
            type(receipt) is not _PayloadOwnership
            or receipt not in _OWNED_PAYLOADS
            or receipt.owner is not self
            or receipt.head is not fact.head
            or receipt.outcomes is not fact.payload.outcomes
        ):
            return False
        unit = receipt.unit
        if unit is not None:
            self.resource_ledger.charge_work(1, "procedure_fact_borrow")
            if (
                unit.outcomes is not receipt.unit_outcomes
                or unit.revision != fact.head.version.revision
                or unit.visits != fact.head.version.generation
                or len(unit.outcomes) != len(receipt.outcomes)
            ):
                return False
            for actual, retained in zip(unit.outcomes, receipt.outcomes, strict=True):
                self.resource_ledger.charge_work(1, "procedure_fact_borrow")
                if actual is not retained:
                    return False
        for stamp in receipt.stamps:
            self.resource_ledger.charge_work(1, "procedure_fact_borrow")
            if (
                stamp.root.identity_epoch != stamp.epoch
                or stamp.root.state_revision != stamp.revision
            ):
                return False
        return True

    def start_generation(self, owner: str, generation: int) -> None:
        self.check_writer()
        self._text(owner, "procedure_generation_start")
        if not owner or type(generation) is not int or generation < 0:
            raise ValueError("Invalid observation generation")
        prior = self._generations.get(owner)
        started = self._started.get(owner)
        if prior is not None and generation <= prior.number:
            raise ValueError("Observation generation did not advance")
        if started is not None and generation < started:
            raise ValueError("Observation generation moved backwards")
        if started == generation:
            return
        self._started[owner] = generation
        self.revision += 1

    def prepare_generation(
        self,
        owner: str,
        generation: int,
        facts: tuple[FactPublication[ProcedurePayload], ...],
    ) -> PreparedGeneration:
        self.check_writer()
        source_revision = self.revision
        self._text(owner, "procedure_generation_prepare")
        if not owner or type(generation) is not int or generation < 0:
            raise ValueError("Invalid observation generation")
        prior = self._generations.get(owner)
        if prior is not None and generation <= prior.number:
            raise ValueError("Observation generation did not advance")
        if self._started.get(owner, generation) != generation:
            raise ValueError("A different observation generation is in progress")
        prepared: dict[ProcedureProducer, FactPublication[ProcedurePayload]] = {}
        for fact in facts:
            self._admit_head(fact.head)
            self._compatible_head(fact.head)
            if fact.head.producer.owner != owner or fact.head.version.generation != generation:
                raise ValueError("Observation belongs to another generation owner")
            if fact.head.producer in prepared:
                raise ValueError("Duplicate producer in one generation")
            if not self._payload_current(fact):
                raise ValueError("Observation payload ownership or lifetime is invalid")
            prepared[fact.head.producer] = fact
        replacement = _Generation(generation, prepared)
        if self.revision != source_revision:
            raise ValueError("Observation store changed during generation preparation")
        plan = PreparedGeneration(self, source_revision, self.epoch, owner, replacement)
        self._generation_plans.add(plan)
        return plan

    def _generation_ticket(self, plan: PreparedGeneration) -> None:
        self.check_writer()
        if (
            plan not in self._generation_plans
            or plan._store is not self
            or plan._committed
            or plan._base_revision != self.revision
            or plan._epoch != self.epoch
        ):
            raise ValueError("Foreign, consumed or stale observation preparation")

    def _validate_generation(
        self, plan: PreparedGeneration, *, application_accepted: bool | None = None
    ) -> None:
        self._generation_ticket(plan)
        if (
            any(
                fact.head.producer.kind == "checked_summary"
                for fact in plan._generation.facts.values()
            )
            and application_accepted is not True
        ):
            raise ValueError("Checked-summary facts require the whole accepted application")
        for fact in plan._generation.facts.values():
            self._compatible_head(fact.head)
            if not self._payload_current(fact):
                raise ValueError("Observation changed during generation preparation")
        self.resource_ledger.checkpoint(force=True)
        self._generation_ticket(plan)

    def _commit_generation(self, plan: PreparedGeneration) -> None:
        self._generation_ticket(plan)
        if not plan._validated:
            raise ValueError("Observation preparation was not validated")
        # No callback, clock check or graph walk after publication.
        self._generations[plan._owner] = plan._generation
        self._started.pop(plan._owner, None)
        self.revision += 1
        plan._committed = True

    def publish_generation(
        self,
        owner: str,
        generation: int,
        facts: tuple[FactPublication[ProcedurePayload], ...],
        *,
        application_accepted: bool | None = None,
    ) -> None:
        plan = self.prepare_generation(owner, generation, facts)
        plan.validate(application_accepted=application_accepted)
        plan.commit()

    def current(self, module: str, qualname: str) -> tuple[ObservationView, ...]:
        self._text(module, "procedure_fact_read")
        self._text(qualname, "procedure_fact_read")
        source_revision = self.revision
        self.resource_ledger.charge_work(len(self._generations), "procedure_fact_read_index")
        if self.revision != source_revision:
            # An admission callback published before the index copy. Returning
            # no records carries no completeness claim; QueryLedger checks its
            # own catalog/inventory revision before declaring scope coverage.
            return ()
        # This shallow snapshot costs one admitted entry per owner. It avoids
        # copying the full owner index at every solver-generation publication,
        # and makes later dependency-callback replacement safe for iteration.
        generations = tuple(self._generations.items())
        result: list[ObservationView] = []
        for owner, group in generations:
            self.resource_ledger.charge_work(1, "procedure_fact_read")
            for fact in group.facts.values():
                self.resource_ledger.charge_work(1, "procedure_fact_read")
                if (
                    fact.head.producer.scope.module != module
                    or fact.head.producer.scope.qualname != qualname
                ):
                    continue
                reason = None
                head = fact.head
                if head.identity != self.identity or head.version.epoch != self.epoch:
                    reason = "identity_or_epoch_invalidated"
                elif not self._dependencies_current(head):
                    reason = "dependency_invalidated"
                elif not self._payload_current(fact):
                    reason = "borrowed_generation_changed"
                if reason is not None:
                    head = replace(head, materialization="invalidated")
                elif owner in self._started:
                    head = replace(head, materialization="partial")
                    reason = "generation_incomplete"
                result.append(ObservationView(head, fact.payload, reason))
        if self.revision != source_revision:
            invalidated: list[ObservationView] = []
            for record in result:
                self.resource_ledger.charge_work(1, "procedure_fact_read_invalidation")
                invalidated.append(
                    ObservationView(
                        replace(record.head, materialization="invalidated"),
                        record.payload,
                        "publication_changed_during_read",
                    )
                )
            return tuple(invalidated)
        return tuple(result)

    def prepare_epoch(
        self,
        transform: PayloadTransformer,
        epoch: IdentityEpoch,
        *,
        units: Mapping[UnitId, Unit] | None = None,
    ) -> PreparedObservationEpoch:
        self.check_writer()
        source_revision = self.revision
        self.resource_ledger.charge_work(1, "procedure_epoch_prepare")
        if epoch <= self.epoch or transform.epoch != epoch or transform.charge is None:
            raise ValueError("Epoch preparation requires the admitted shared host transformer")
        identity = replace(self.identity, state_version=str(epoch))
        unit_owners: dict[UnitId, Unit] = {}
        for key, old_unit in self._unit_owners.items():
            self.resource_ledger.charge_work(1, "procedure_epoch_unit")
            unit = None if units is None else units.get(key)
            if unit is None or unit.key != key:
                raise ValueError("Epoch preparation lacks registered host Unit owners")
            if transform.memo.get(id(old_unit)) is not unit:
                raise ValueError("Epoch Unit is not the registered owner's prepared host clone")
            unit_owners[key] = unit
        prepared: dict[str, _Generation] = {}
        for owner, group in self._generations.items():
            self.resource_ledger.charge_work(1, "procedure_epoch_prepare")
            records: dict[ProcedureProducer, FactPublication[ProcedurePayload]] = {}
            for producer, fact in group.facts.items():
                self._admit_head(fact.head)
                payload_current = self._payload_current(fact)
                receipt = fact.payload._ownership
                assert receipt is not None
                stale_unit = (
                    not payload_current
                    and receipt.unit is not None
                    and self._unit_owners.get(receipt.unit.key) is receipt.unit
                )
                if not payload_current and not stale_unit:
                    raise ValueError("Cannot remap an invalid borrowed observation")
                head = replace(
                    fact.head,
                    identity=identity,
                    version=replace(fact.head.version, epoch=epoch),
                    materialization="invalidated"
                    if producer.kind == "checked_summary" or stale_unit
                    else "partial",
                )
                # A running producer has advanced beyond its old borrowed
                # visit. Preserve that old graph only as owned invalidated
                # diagnostics; attaching a new Unit receipt would renew it.
                if receipt.unit is not None and payload_current:
                    unit = None if units is None else units.get(UnitId(producer.identity))
                    if unit is None:
                        raise ValueError(
                            "Borrowed unit observations need prepared host Unit owners"
                        )
                    self._match_unit(head, unit)
                    records[producer] = self._issue(head, self._outcome_tuple(unit.outcomes), unit)
                else:
                    outcomes = transform.visit(fact.payload.outcomes)
                    records[producer] = self._issue(head, outcomes)
            prepared[owner] = _Generation(group.number, records)
        if self.revision != source_revision:
            raise ValueError("Observation store changed during epoch preparation")
        plan = PreparedObservationEpoch(
            self, source_revision, self.epoch, epoch, identity, prepared, unit_owners
        )
        self._epoch_plans.add(plan)
        return plan

    def _epoch_ticket(self, plan: PreparedObservationEpoch) -> None:
        self.check_writer()
        if (
            plan not in self._epoch_plans
            or plan._store is not self
            or plan._committed
            or plan._base_revision != self.revision
            or plan._old_epoch != self.epoch
        ):
            raise ValueError("Foreign, consumed or stale observation epoch preparation")

    def _validate_epoch(self, plan: PreparedObservationEpoch) -> None:
        self._epoch_ticket(plan)
        for group in plan._generations.values():
            for fact in group.facts.values():
                if not self._payload_current(fact):
                    raise ValueError("Prepared epoch payload changed")
        self.resource_ledger.checkpoint(force=True)
        self._epoch_ticket(plan)

    def _commit_epoch(self, plan: PreparedObservationEpoch) -> None:
        self._epoch_ticket(plan)
        if not plan._validated:
            raise ValueError("Observation epoch was not validated")
        self._generations = plan._generations
        self._unit_owners = plan._unit_owners
        self.identity = plan._identity
        self.epoch = plan._new_epoch
        self._started.clear()
        self.revision += 1
        plan._committed = True
