"""Caller coverage evidence, applicability and relational fresh instantiation.

An accepted source candidate proves a conditional transfer. It does not prove
that a partial solver snapshot covers all inputs to a source point.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import count
from typing import Protocol, TypeGuard, runtime_checkable
from uuid import uuid4

from pythonstan.analysis.contracts import (
    SemanticIdentity,
    freeze_snapshot,
    thaw_snapshot,
    value_snapshot,
)
from pythonstan.analysis.contracts.identity import stable_digest
from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    FrozenJsonValue,
    JsonObject,
    JsonValue,
    require_array,
    require_object,
    require_string,
)

from .checker import VerifiedSummary
from .language import PROFILE

_COVERAGE_SEAL = object()
_COVERAGE_KEY = secrets.token_bytes(32)


def _coverage_digest(evidence: PointCoverageProof) -> str:
    data = {
        "origin": evidence.origin,
        "profile": evidence.profile,
        "point": evidence.point,
        "identity": evidence.identity.digest,
        "input_bound": evidence.input_bound_digest,
        "table": evidence.table_digest,
        "keys": evidence.keys,
        "payload": evidence.payload_digest,
        "complete": evidence.complete_input,
        "pending": evidence.pending_work,
    }
    return hmac.new(_COVERAGE_KEY, stable_digest(data).encode(), hashlib.sha256).hexdigest()


def _seal_evidence(evidence: PointCoverageProof) -> PointCoverageProof:
    from dataclasses import replace

    return replace(evidence, proof_digest=_coverage_digest(evidence), _seal=_COVERAGE_SEAL)


def _tagged_key(value: JsonValue) -> tuple[str, str]:
    record = require_object(value)
    return require_string(record["type"]), repr(record["value"])


def _strings(value: JsonValue) -> list[JsonValue]:
    return [require_string(item) for item in require_array(value)]


def _frozen_object(value: object) -> FrozenJsonObject:
    frozen = freeze_snapshot(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("Expected a frozen application record")
    return frozen


def _wire_digest(value: object) -> str:
    data = thaw_snapshot(freeze_snapshot(value))
    if isinstance(data, dict) and "tagged_constants" in data:
        # Bind represented inputs, preserving the existing canonical wire hash.
        data = {
            "tagged_constants": sorted(require_array(data["tagged_constants"]), key=_tagged_key),
            "objects": sorted(_strings(data.get("objects", [])), key=require_string),
            "top": data.get("top", False),
            "top_sorts": sorted(_strings(data.get("top_sorts", [])), key=require_string),
            "unbound": "unbound" in _strings(data.get("types", [])),
            "integer_interval": data.get("integer_interval"),
            "string_patterns": data.get("string_patterns"),
        }
    return stable_digest(data)


@dataclass(frozen=True)
class GuardedEntryContract:
    """Explicit conditional entry domain, never an inferred caller snapshot.

    The caller declares the exact source table and complete finite string-key
    domain. Payload is universally quantified. This does not cover arbitrary
    external calls outside that stated entry contract.
    """

    point: str
    table_digest: str
    keys: tuple[str, ...]
    payload: str = "all_modeled_values"
    entry_id: str = "explicit_guarded_entry"


@dataclass(frozen=True)
class PointCoverageProof:
    origin: str
    profile: str
    point: str
    identity: SemanticIdentity
    input_bound_digest: str
    table_digest: str
    keys: tuple[str, ...]
    payload_digest: str | None
    complete_input: bool
    pending_work: int = 0
    proof_digest: str = ""
    _seal: object = field(default=None, repr=False, compare=False)

    @property
    def trusted_complete_input(self) -> bool:
        try:
            return (
                self._seal is _COVERAGE_SEAL
                and self.complete_input
                and self.pending_work == 0
                and hmac.compare_digest(self.proof_digest, _coverage_digest(self))
            )
        except (ValueError, TypeError, AttributeError):
            return False


GuardEvidence = PointCoverageProof


def guard_from_source_call(certificate: object, target: str) -> PointCoverageProof:
    """Derive the whole input at one finite module-initialization call.

    The checker already validated every module statement and stable table and
    handler binding. No solver worklist or PTA edge set is used as evidence.
    """
    if not isinstance(certificate, VerifiedSummary) or not certificate.issued_by_checker:
        raise ValueError("source-call guard requires a checker-issued summary")
    calls = [call for call in certificate.source_facts.calls if call.target == target]
    if len(calls) != 1:
        raise ValueError("source target does not identify exactly one checked call")
    call = calls[0]
    if not call.reachable:
        raise ValueError("source call is unreachable after a definite earlier exception")
    payload_digest = _wire_digest(call.payload)
    bound = stable_digest(
        {
            "origin": "source_module_initialization",
            "point": call.point,
            "table": certificate.source_facts.table_digest,
            "keys": [call.key],
            "payload": payload_digest,
        }
    )
    return _seal_evidence(
        PointCoverageProof(
            "source_module_initialization",
            PROFILE,
            call.point,
            certificate.source_facts.identity,
            bound,
            certificate.source_facts.table_digest,
            (call.key,),
            payload_digest,
            True,
        )
    )


def guard_from_entry_contract(certificate: object, contract: object) -> PointCoverageProof:
    if not isinstance(certificate, VerifiedSummary) or not certificate.issued_by_checker:
        raise ValueError("entry guard requires a checker-issued summary")
    if (
        not isinstance(contract, GuardedEntryContract)
        or contract.point != "entry:" + certificate.source_facts.dispatch
        or not contract.entry_id
        or contract.table_digest != certificate.source_facts.table_digest
        or type(contract.keys) is not tuple
        or not contract.keys
        or len(set(contract.keys)) != len(contract.keys)
        or any(type(key) is not str for key in contract.keys)
        or contract.payload != "all_modeled_values"
    ):
        raise ValueError("invalid explicit guarded entry contract")
    bound = stable_digest(
        {
            "entry": contract.entry_id,
            "point": contract.point,
            "table": contract.table_digest,
            "keys": contract.keys,
            "payload": contract.payload,
        }
    )
    return _seal_evidence(
        PointCoverageProof(
            "explicit_entry_contract",
            PROFILE,
            contract.point,
            certificate.source_facts.identity,
            bound,
            contract.table_digest,
            contract.keys,
            None,
            True,
        )
    )


def untrusted_snapshot_evidence(
    identity: SemanticIdentity,
    point: str,
    table_digest: str,
    keys: Sequence[str] = (),
    *,
    pending_work: int = 0,
) -> PointCoverageProof:
    """Partial/read-only/bottom snapshots intentionally cannot prove coverage."""
    return PointCoverageProof(
        "untrusted_solver_snapshot",
        PROFILE,
        point,
        identity,
        "",
        table_digest,
        tuple(keys),
        None,
        False,
        pending_work,
    )


@dataclass(frozen=True)
class GuardDecision:
    applicable: bool
    covered_keys: tuple[str, ...] = ()
    complement_keys: tuple[str, ...] = ()
    unknown_complement: bool = False
    diagnostics: tuple[str, ...] = ()


def entails_guard(
    certificate: object,
    evidence: object,
    *,
    identity: SemanticIdentity,
    point: str,
    table_digest: str,
    keys: Sequence[str],
    payload: object,
    unknown_key: bool = False,
) -> GuardDecision:
    if not isinstance(certificate, VerifiedSummary) or not certificate.issued_by_checker:
        return GuardDecision(False, diagnostics=("summary was not issued by checker",))
    if not isinstance(evidence, PointCoverageProof) or not evidence.trusted_complete_input:
        return GuardDecision(False, diagnostics=("complete point-input coverage is not proved",))
    if (
        evidence.profile != PROFILE
        or evidence.identity != identity
        or certificate.source_facts.identity != identity
        or evidence.point != point
        or evidence.table_digest != table_digest
        or table_digest != certificate.source_facts.table_digest
    ):
        return GuardDecision(
            False,
            diagnostics=("source/IR/model/policy/state/dependency/table/point identity changed",),
        )
    if any(type(key) is not str for key in keys) or set(keys) != set(evidence.keys):
        return GuardDecision(
            False,
            diagnostics=("current keys do not cover exactly the complete point-input evidence",),
        )
    if evidence.payload_digest is not None and evidence.payload_digest != _wire_digest(payload):
        return GuardDecision(False, diagnostics=("payload input differs from source-call bound",))
    known = set(certificate.candidate.guard.keys)
    return GuardDecision(
        True,
        tuple(key for key in keys if key in known),
        tuple(key for key in keys if key not in known),
        bool(unknown_key),
    )


class FreshAllocator(Protocol):
    def allocate(self, allocation: str, point: str) -> str: ...


@runtime_checkable
class ReservableAllocator(Protocol):
    def reserve(self, identities: Iterable[str]) -> None: ...


class FreshArena:
    """Session-owned existential allocation names; no module-global counter."""

    def __init__(self, arena: str | None = None) -> None:
        self.arena = arena or uuid4().hex
        self._next: Iterator[int] = count()
        self._reserved: set[str] = set()

    def reserve(self, identities: Iterable[str]) -> None:
        self._reserved.update(identities)

    def allocate(self, allocation: str, point: str) -> str:
        while True:
            identifier = f"{self.arena}:fresh_box:{allocation}:{point}:{next(self._next)}"
            if identifier not in self._reserved:
                self._reserved.add(identifier)
                return identifier


def _frame_ids(value: object) -> TypeGuard[Sequence[str] | set[str] | frozenset[str]]:
    if not isinstance(value, (tuple, list, set, frozenset)):
        return False
    items: Sequence[object] | set[object] | frozenset[object] = value
    return all(type(item) is str for item in items)


@dataclass(frozen=True)
class DispatchApplication:
    outcomes: tuple[FrozenJsonObject, ...]
    allocations: tuple[FrozenJsonObject, ...]
    relations: tuple[FrozenJsonObject, ...]
    coverage_status: str
    proof_status: str
    obligations: tuple[str, ...] = ()
    residual: tuple[FrozenJsonObject, ...] = ()
    work: int = 0
    input_bound_digest: str = ""
    certificate_digest: str = ""
    solver_status: str | None = None
    frame_bound_digest: str = ""

    def to_dict(self) -> JsonObject:
        return require_object(
            thaw_snapshot(
                freeze_snapshot(
                    {
                        "outcomes": self.outcomes,
                        "allocations": self.allocations,
                        "relations": self.relations,
                        "coverage_status": self.coverage_status,
                        "proof_status": self.proof_status,
                        "obligations": self.obligations,
                        "residual": self.residual,
                        "work": self.work,
                        "input_bound_digest": self.input_bound_digest,
                        "certificate_digest": self.certificate_digest,
                        "solver_status": self.solver_status,
                        "frame_bound_digest": self.frame_bound_digest,
                    }
                )
            )
        )


def _fallback(reason: str, work: int = 0) -> DispatchApplication:
    residual = _frozen_object(
        {
            "kind": "uncovered_guard_complement",
            "may_return_any": True,
            "may_call_any": True,
            "may_modify_heap": True,
            "may_raise": True,
            "may_suspend": True,
            "may_allocate": True,
        }
    )
    return DispatchApplication((), (), (), "open", "unknown", (reason,), (residual,), work)


def apply_verified_summary(
    certificate: object,
    evidence: object,
    *,
    identity: SemanticIdentity,
    point: str,
    table_digest: str,
    keys: Sequence[str],
    payload: object,
    unknown_key: bool = False,
    arena: FreshAllocator | None = None,
    max_work: int = 100000,
    requested_property: str | None = "return_relation",
    existing_objects: object = None,
) -> DispatchApplication:
    if type(max_work) is not int or max_work < 0:
        raise ValueError("application work budget must be a nonnegative integer")
    frozen_payload = freeze_snapshot(payload)
    decision = entails_guard(
        certificate,
        evidence,
        identity=identity,
        point=point,
        table_digest=table_digest,
        keys=keys,
        payload=frozen_payload,
        unknown_key=unknown_key,
    )
    if not decision.applicable:
        return _fallback("; ".join(decision.diagnostics))
    assert isinstance(certificate, VerifiedSummary)
    assert isinstance(evidence, PointCoverageProof)
    required = 1 + len(keys) * 2
    if required > max_work:
        return _fallback("application budget exhausted before complete input coverage", max_work)
    arena = arena or FreshArena()
    arms = {arm.key: arm for arm in certificate.candidate.arms}
    facts = {handler.name: handler for handler in certificate.source_facts.handlers}
    makes_fresh = any(
        facts[arms[key].handler].relation == "fresh_box" for key in decision.covered_keys
    )
    if makes_fresh and existing_objects is None:
        return _fallback(
            "fresh instantiation requires a complete caller frame object-id bound", required
        )
    if existing_objects is not None and not _frame_ids(existing_objects):
        return _fallback("malformed caller frame object-id bound", required)
    frame_objects = () if existing_objects is None else existing_objects
    forbidden: set[str] = set(frame_objects) if makes_fresh else set()
    payload_work = 0

    def reserve_payload(value: FrozenJsonValue) -> None:
        nonlocal payload_work
        payload_work += 1
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key == "objects" and isinstance(item, tuple):
                    payload_work += len(item)
                    forbidden.update(
                        identity
                        for identity in item
                        if isinstance(identity, str) and type(identity) is str
                    )
                elif key == "object" and isinstance(item, str) and type(item) is str:
                    forbidden.add(item)
                else:
                    reserve_payload(item)
        elif isinstance(value, tuple):
            for item in value:
                reserve_payload(item)

    if makes_fresh:
        reserve_payload(frozen_payload)
        required += len(frame_objects) + payload_work
        if required > max_work:
            return _fallback("fresh frame/alpha-renaming budget exhausted", max_work)
    frame_digest = stable_digest(sorted(forbidden)) if makes_fresh else ""
    if makes_fresh and isinstance(arena, ReservableAllocator):
        arena.reserve(forbidden)
    outcomes: list[FrozenJsonObject] = []
    allocations: list[FrozenJsonObject] = []
    relations: list[FrozenJsonObject] = []
    return_precise = True
    for key in decision.covered_keys:
        arm = arms[key]
        actual = facts[arm.handler]
        fresh = None
        if actual.relation == "fresh_box":
            if actual.allocation is None:
                raise AssertionError("Checked fresh handler has no allocation site")
            try:
                fresh = arena.allocate(actual.allocation, point)
            except (ValueError, TypeError, RuntimeError, AttributeError):
                return _fallback("fresh allocator failed its instantiation contract", required)
            if type(fresh) is not str or not fresh or fresh in forbidden:
                return _fallback("fresh allocator captures an existing object identity", required)
            forbidden.add(fresh)
            allocations.append(
                _frozen_object(
                    {
                        "object": fresh,
                        "kind": "dict",
                        "fresh": True,
                        "slots": {"value": frozen_payload},
                        "existing_heap": "unchanged",
                    }
                )
            )
        if arm.relation == "input":
            returned = frozen_payload
            relations.append(
                _frozen_object({"key": key, "handler": arm.handler, "return": "input_payload"})
            )
        elif arm.relation == "fresh_box":
            assert fresh is not None
            returned = value_snapshot(
                objects=(fresh,), types=("dict",), slots={"value": frozen_payload}
            )
            relations.append(
                _frozen_object(
                    {
                        "key": key,
                        "handler": arm.handler,
                        "return": fresh,
                        "fresh": True,
                        "slot": "value",
                        "slot_equals": "input_payload",
                    }
                )
            )
        else:
            returned = value_snapshot(top=True)
            return_precise = False
            relations.append(_frozen_object({"key": key, "handler": arm.handler, "return": "top"}))
        outcomes.append(
            _frozen_object(
                {
                    "kind": "return",
                    "key": key,
                    "handler": arm.handler,
                    "value": returned,
                    "existing_heap": "unchanged",
                }
            )
        )
    # The independently justified exact-dict/string-key complement has no
    # handler call or user-heap mutation. It raises KeyError.
    for key in decision.complement_keys:
        outcomes.append(
            _frozen_object(
                {
                    "kind": "raise",
                    "key": key,
                    "exception": "KeyError",
                    "existing_heap": "unchanged",
                    "origin": "exact_dict_complement",
                }
            )
        )
    residual: tuple[FrozenJsonObject, ...] = ()
    obligations: tuple[str, ...] = ()
    coverage = "closed_under_profile"
    if decision.unknown_complement:
        unresolved = _fallback("unknown key/table protocol is outside the certified guard")
        residual, obligations, coverage = unresolved.residual, unresolved.obligations, "open"
    if requested_property is None:
        proof = "not_requested"
    elif requested_property not in ("return_relation", "key_handler"):
        proof = "unknown"
        obligations += ("property is outside the guarded-dispatch checker",)
    else:
        proof = (
            "verified"
            if coverage == "closed_under_profile"
            and (return_precise or requested_property == "key_handler")
            else "unknown"
        )
    return DispatchApplication(
        tuple(outcomes),
        tuple(allocations),
        tuple(relations),
        coverage,
        proof,
        obligations,
        residual,
        required,
        evidence.input_bound_digest,
        certificate.proof_digest,
        frame_bound_digest=frame_digest,
    )
