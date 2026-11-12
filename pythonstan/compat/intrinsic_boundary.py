"""Checked legacy intrinsic backend results, using the existing summary record.

The complete bit is a trusted backend assertion, not a source certificate.
Native publication remains additive and declines unrepresentable effects.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import SimpleNamespace
from typing import TYPE_CHECKING, Protocol, TypeGuard, runtime_checkable

from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.contracts.json_data import validate_json_object
from pythonstan.analysis.contracts.work import WorkLedgerPort
from pythonstan.compat.legacy_ai.api import AISummary, ArgBinding, FieldWrite, UnknownEffect

_NAMESPACE_TYPE: type[object] = SimpleNamespace


def _is_namespace(value: object) -> TypeGuard[SimpleNamespace]:
    return isinstance(value, _NAMESPACE_TYPE)


def _is_state(value: object) -> TypeGuard[AIState]:
    from pythonstan.compat.legacy_ai.state import AIState

    state_type: type[object] = AIState
    return isinstance(value, state_type)


def _is_absval(value: object) -> TypeGuard[AbsVal]:
    from pythonstan.compat.legacy_ai.value_domain import AbsVal

    value_type: type[object] = AbsVal
    return isinstance(value, value_type)


def _is_native_object(value: object) -> TypeGuard[AbstractObject]:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject

    object_type: type[object] = AbstractObject
    return isinstance(value, object_type)


def _is_field(value: object) -> TypeGuard[Field]:
    from pythonstan.analysis.pointer.kcfa.heap_model import Field

    field_type: type[object] = Field
    return isinstance(value, field_type)


if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
    from pythonstan.analysis.pointer.kcfa.context import NativeContext, Scope
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    from pythonstan.compat.legacy_ai.state import AIState
    from pythonstan.compat.legacy_ai.value_domain import AbsVal


@runtime_checkable
class IntrinsicProviderPort(Protocol):
    """The real optional provider injection surface, independent of host internals."""

    def supports(self, value: AbstractObject) -> bool: ...

    def apply(
        self,
        host: PointerSolver,
        scope: Scope,
        context: NativeContext,
        call: CallConstraint,
        builtin: AbstractObject,
        work_ledger: WorkLedgerPort,
    ) -> bool: ...


class IntrinsicEnginePort(Protocol):
    """Actual engine service; foreign return data is checked before publication."""

    budget: AnalysisBudget
    work_ledger: WorkLedgerPort | None

    def analyze_intrinsic(
        self,
        builtin_name: str,
        call_context: NativeContext | None,
        arg_bindings: Sequence[ArgBinding],
        caller_scope: Scope | None = None,
        target_name: str | None = None,
    ) -> object: ...


def _items(value: object, label: str) -> tuple[object, ...]:
    if not isinstance(value, (tuple, list, set, frozenset)):
        raise TypeError(f"{label} must be a finite builtin container")
    entries: Iterable[object] = value
    return tuple(entries)


def _objects(value: object, label: str) -> frozenset[AbstractObject]:
    result: set[AbstractObject] = set()
    for item in _items(value, label):
        if not _is_native_object(item):
            raise TypeError(f"{label} must contain Native objects")
        result.add(item)
    return frozenset(result)


def _texts(value: object, label: str) -> tuple[str, ...]:
    result: list[str] = []
    for item in _items(value, label):
        if not isinstance(item, str):
            raise TypeError(f"{label} must contain strings")
        result.append(item)
    return tuple(result)


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be a Boolean")
    return value


def _namespace(value: SimpleNamespace) -> Mapping[str, object]:
    entries: Mapping[str, object] = vars(value)
    return entries


def _write(value: object) -> FieldWrite:
    owners: object
    field: object
    targets: object
    scalar: object
    if isinstance(value, FieldWrite):
        owners, field, targets, scalar = (
            value.obj_addrs,
            value.field_key,
            value.value_addrs,
            value.value,
        )
    elif _is_namespace(value):
        data = _namespace(value)
        owners, field, targets, scalar = (
            data["obj_addrs"],
            data["field_key"],
            data["value_addrs"],
            data.get("value"),
        )
    else:
        raise TypeError("Intrinsic writes require FieldWrite or a legacy namespace")
    if not _is_field(field):
        raise TypeError("Intrinsic write field must be a Native Field")
    if scalar is not None and not _is_absval(scalar):
        raise TypeError("Intrinsic write scalar must be AbsVal or None")
    return FieldWrite(
        _objects(owners, "write owners"), field, _objects(targets, "write values"), scalar
    )


def checked_intrinsic_summary(value: object) -> AISummary:
    """Normalize the actual record or the explicit legacy namespace boundary."""
    if isinstance(value, AISummary):
        data: Mapping[str, object] = {
            "ret": value.ret,
            "ret_value": value.ret_value,
            "normal_exit": value.normal_exit,
            "exit_state": value.exit_state,
            "prefix_exceptions": value.prefix_exceptions,
            "exceptional_exit_states": value.exceptional_exit_states,
            "writes": value.writes,
            "reads": value.reads,
            "calls": value.calls,
            "unknown_effects": value.unknown_effects,
            "may_raise": value.may_raise,
            "is_complete": value.is_complete,
            "incomplete_reasons": value.incomplete_reasons,
            "core_statistics": value.core_statistics,
        }
    elif _is_namespace(value):
        data = _namespace(value)
    else:
        raise TypeError("Intrinsic backend must return AISummary or a legacy namespace")
    complete = _boolean(data.get("is_complete", False), "is_complete")
    reasons = _texts(data.get("incomplete_reasons", ()), "incomplete reasons")
    statistics = validate_json_object(data.get("core_statistics", {}))
    if not complete:
        declined = AISummary(is_complete=False, incomplete_reasons=reasons)
        declined.core_statistics = statistics
        return declined
    # Completion never supplies missing return/write fields by guessing facts.
    ret = _objects(data["ret"], "return objects")
    writes = tuple(_write(item) for item in _items(data["writes"], "writes"))
    reads: list[tuple[frozenset[AbstractObject], Field]] = []
    for item in _items(data.get("reads", ()), "reads"):
        pair = _items(item, "read")
        if len(pair) != 2 or not _is_field(pair[1]):
            raise TypeError("Intrinsic read requires objects and a Native Field")
        reads.append((_objects(pair[0], "read owners"), pair[1]))
    calls: list[tuple[str, frozenset[AbstractObject]]] = []
    for item in _items(data.get("calls", ()), "calls"):
        pair = _items(item, "call")
        if len(pair) != 2 or not isinstance(pair[0], str):
            raise TypeError("Intrinsic call requires a name and Native objects")
        calls.append((pair[0], _objects(pair[1], "call objects")))
    effects: list[UnknownEffect] = []
    for item in _items(data.get("unknown_effects", ()), "unknown effects"):
        if not isinstance(item, UnknownEffect):
            raise TypeError("Intrinsic unknown effects require UnknownEffect records")
        if not isinstance(item.kind, str) or (
            item.field_key is not None and not _is_field(item.field_key)
        ):
            raise TypeError("Malformed intrinsic unknown effect")
        effects.append(
            UnknownEffect(item.kind, _objects(item.target_addrs, "effect targets"), item.field_key)
        )
    scalar, state = data.get("ret_value"), data.get("exit_state")
    if scalar is not None and not _is_absval(scalar):
        raise TypeError("Intrinsic scalar return must be AbsVal or None")
    if state is not None and not _is_state(state):
        raise TypeError("Intrinsic exit state must be AIState or None")
    exceptional: list[tuple[str, AIState]] = []
    for item in _items(data.get("exceptional_exit_states", ()), "exceptional exits"):
        pair = _items(item, "exceptional exit")
        if len(pair) != 2 or not isinstance(pair[0], str) or not _is_state(pair[1]):
            raise TypeError("Intrinsic exceptional exit requires a name and AIState")
        exceptional.append((pair[0], pair[1]))
    result = AISummary(
        ret=ret,
        ret_value=scalar,
        normal_exit=_boolean(data.get("normal_exit", False), "normal_exit"),
        exit_state=state,
        prefix_exceptions=frozenset(_texts(data.get("prefix_exceptions", ()), "prefix exceptions")),
        exceptional_exit_states=tuple(exceptional),
        writes=writes,
        reads=tuple(reads),
        calls=tuple(calls),
        unknown_effects=tuple(effects),
        may_raise=frozenset(_texts(data.get("may_raise", ()), "may_raise")),
        is_complete=True,
        incomplete_reasons=reasons,
    )
    result.core_statistics = statistics
    return result
