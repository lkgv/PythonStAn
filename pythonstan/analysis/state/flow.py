"""The single discriminated completion hierarchy consumed by the live solver."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal, TypeGuard, assert_never, override

from pythonstan.analysis.domains.values import Value

from .continuation import (
    AttachmentValue,
    CompletionKind,
    Continuation,
    parse_graph_attachment,
    parse_legacy_continuation,
)
from .handles import StateHandle

if TYPE_CHECKING:
    from .store import Store


@dataclass(frozen=True, slots=True)
class ExceptionValue:
    value: Value
    family: str


@dataclass(frozen=True, slots=True)
class FlowTail:
    continuation: Continuation = field(default_factory=Continuation)
    cleanups: Continuation = field(default_factory=Continuation)
    resume_target: AttachmentValue = None


class _FlowView:
    state: StateHandle
    tail: FlowTail

    @property
    def store(self) -> Store:
        return self.state.read()

    @property
    def exception(self) -> str | None:
        return None

    @property
    def continuation(self) -> Continuation:
        return self.tail.continuation

    @property
    def cleanups(self) -> Continuation:
        return self.tail.cleanups

    @property
    def resume_target(self) -> AttachmentValue:
        return self.tail.resume_target


@dataclass(frozen=True, slots=True)
class NormalExprFlow(_FlowView):
    state: StateHandle
    value: Value
    tail: FlowTail = field(default_factory=FlowTail)
    kind: Literal["normal"] = field(default="normal", init=False)
    form: Literal["expression"] = field(default="expression", init=False)


@dataclass(frozen=True, slots=True)
class NormalStmtFlow(_FlowView):
    state: StateHandle
    tail: FlowTail = field(default_factory=FlowTail)
    kind: Literal["normal"] = field(default="normal", init=False)
    form: Literal["statement"] = field(default="statement", init=False)


@dataclass(frozen=True, slots=True)
class ReturnFlow(_FlowView):
    state: StateHandle
    value: Value
    tail: FlowTail = field(default_factory=FlowTail)
    kind: Literal["return"] = field(default="return", init=False)


@dataclass(frozen=True, slots=True)
class RaiseFlow(_FlowView):
    state: StateHandle
    exception_value: ExceptionValue
    tail: FlowTail = field(default_factory=FlowTail)
    kind: Literal["raise"] = field(default="raise", init=False)

    @property
    def value(self) -> Value:
        return self.exception_value.value

    @property
    @override
    def exception(self) -> str:
        return self.exception_value.family


@dataclass(frozen=True, slots=True)
class BreakFlow(_FlowView):
    state: StateHandle
    tail: FlowTail = field(default_factory=FlowTail)
    kind: Literal["break"] = field(default="break", init=False)


@dataclass(frozen=True, slots=True)
class ContinueFlow(_FlowView):
    state: StateHandle
    tail: FlowTail = field(default_factory=FlowTail)
    kind: Literal["continue"] = field(default="continue", init=False)


@dataclass(frozen=True, slots=True)
class SuspendFlow(_FlowView):
    state: StateHandle
    value: Value
    tail: FlowTail = field(default_factory=FlowTail)
    kind: Literal["suspend"] = field(default="suspend", init=False)


type NormalFlow = NormalExprFlow | NormalStmtFlow
type Flow = NormalFlow | ReturnFlow | RaiseFlow | BreakFlow | ContinueFlow | SuspendFlow


def is_flow(value: object) -> TypeGuard[Flow]:
    return type(value) in (
        NormalExprFlow,
        NormalStmtFlow,
        ReturnFlow,
        RaiseFlow,
        BreakFlow,
        ContinueFlow,
        SuspendFlow,
    )


def flow_payload(flow: Flow) -> Value | None:
    """No-value completions remain distinct from an abstract bottom or None."""
    match flow:
        case NormalExprFlow(value=value) | ReturnFlow(value=value) | SuspendFlow(value=value):
            return value
        case RaiseFlow(exception_value=exception):
            return exception.value
        case NormalStmtFlow() | BreakFlow() | ContinueFlow():
            return None
        case _:
            assert_never(flow)


def require_payload(flow: Flow) -> Value:
    value = flow_payload(flow)
    if value is None:
        raise ValueError("A no-value completion entered a value consumer")
    return value


def with_state(flow: Flow, state: StateHandle) -> Flow:
    """The real exhaustive state replacement handler used by control cleanup."""
    match flow:
        case NormalExprFlow(value=value, tail=tail):
            return NormalExprFlow(state, value, tail)
        case NormalStmtFlow(tail=tail):
            return NormalStmtFlow(state, tail)
        case ReturnFlow(value=value, tail=tail):
            return ReturnFlow(state, value, tail)
        case RaiseFlow(exception_value=exception, tail=tail):
            return RaiseFlow(state, exception, tail)
        case BreakFlow(tail=tail):
            return BreakFlow(state, tail)
        case ContinueFlow(tail=tail):
            return ContinueFlow(state, tail)
        case SuspendFlow(value=value, tail=tail):
            return SuspendFlow(state, value, tail)
        case _:
            assert_never(flow)


class _Unset(Enum):
    TOKEN = "unchanged"


_UNSET = _Unset.TOKEN


def make_completion(
    state: StateHandle,
    kind: CompletionKind,
    payload: Value | None,
    exception: str | None,
    tail: FlowTail,
) -> Flow:
    match kind:
        case "normal":
            return (
                NormalStmtFlow(state, tail)
                if payload is None
                else NormalExprFlow(state, payload, tail)
            )
        case "return":
            if payload is None:
                raise ValueError("Return requires an explicit target value, including Python None")
            return ReturnFlow(state, payload, tail)
        case "raise":
            if payload is None:
                raise ValueError("Raise requires an explicit exception value")
            return RaiseFlow(state, ExceptionValue(payload, exception or "AnyException"), tail)
        case "break":
            return BreakFlow(state, tail)
        case "continue":
            return ContinueFlow(state, tail)
        case "suspend":
            if payload is None:
                raise ValueError("Suspend requires an explicit yielded/awaited value")
            return SuspendFlow(state, payload, tail)
        case _:
            assert_never(kind)


def replace_flow(
    flow: Flow,
    *,
    store: Store | None = None,
    state: StateHandle | None = None,
    kind: CompletionKind | None = None,
    value: Value | None | _Unset = _UNSET,
    exception: str | None | _Unset = _UNSET,
    continuation: Continuation | _Unset = _UNSET,
    cleanups: Continuation | _Unset = _UNSET,
    resume_target: AttachmentValue | _Unset = _UNSET,
) -> Flow:
    selected_state = StateHandle.capture(store) if store is not None else state or flow.state
    selected_payload = flow_payload(flow) if value is _UNSET else value
    selected_exception = flow.exception if exception is _UNSET else exception
    tail = FlowTail(
        flow.continuation if continuation is _UNSET else continuation,
        flow.cleanups if cleanups is _UNSET else cleanups,
        flow.resume_target if resume_target is _UNSET else resume_target,
    )
    return make_completion(
        selected_state,
        flow.kind if kind is None else kind,
        selected_payload,
        selected_exception,
        tail,
    )


def legacy_flow(
    store: Store,
    kind: CompletionKind = "normal",
    value: Value | None | _Unset = _UNSET,
    exception: str | None = None,
    continuation: object = (),
    cleanups: object = (),
    resume_target: object = None,
) -> Flow:
    """Temporary old-constructor codec; there is one live completion hierarchy."""
    tail = FlowTail(
        parse_legacy_continuation(continuation),
        parse_legacy_continuation(cleanups),
        parse_graph_attachment(resume_target),
    )
    return make_completion(
        StateHandle.capture(store), kind, None if value is _UNSET else value, exception, tail
    )


__all__ = [
    "BreakFlow",
    "ContinueFlow",
    "ExceptionValue",
    "Flow",
    "FlowTail",
    "NormalExprFlow",
    "NormalFlow",
    "NormalStmtFlow",
    "RaiseFlow",
    "ReturnFlow",
    "SuspendFlow",
    "flow_payload",
    "is_flow",
    "legacy_flow",
    "replace_flow",
    "require_payload",
    "with_state",
]


def as_statement(flow: Flow) -> Flow:
    """Discard a normal statement evaluation result explicitly."""
    if isinstance(flow, NormalExprFlow):
        return NormalStmtFlow(flow.state, flow.tail)
    return flow
