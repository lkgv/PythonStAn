"""Typed dynamic exception ownership shared by cleanup and suspended frames."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal, TypeGuard

from pythonstan.analysis.contracts.identity import ContinuationId, FrameId, ProgramPointId
from pythonstan.analysis.domains.reabstract import ReferenceKey
from pythonstan.analysis.domains.values import Value

from .effects import CellWrite, Havoc, HeapDefaultWrite, HeapDelete, HeapWrite, MetadataUpdate
from .keys import FieldKey

if TYPE_CHECKING:
    from .store import Flow, Frame, HeapObject, Store


@dataclass(frozen=True, slots=True)
class ExceptionContext:
    value: Value
    type_value: Value
    owner: Value


@dataclass(frozen=True, slots=True)
class ExceptionRestoreRecord:
    previous: ExceptionContext
    restoring_frame: FrameId | None


@dataclass(frozen=True, slots=True)
class CallResumeContext:
    caller_exception: ExceptionContext
    generator_frame: FrameId


type CompletionKind = Literal["normal", "return", "raise", "break", "continue", "suspend"]
type TryPhase = Literal["body", "handler", "orelse"]


@dataclass(frozen=True, slots=True)
class PendingCompletion:
    """An exit without its obsolete state; cleanup supplies the current state."""

    kind: CompletionKind
    value: Value
    exception: str | None


@dataclass(frozen=True, slots=True)
class HandlerContext:
    name: str | None
    previous: ExceptionRestoreRecord


@dataclass(frozen=True, slots=True)
class AssignResume:
    targets: tuple[ast.expr, ...]
    inner: Continuation


@dataclass(frozen=True, slots=True)
class ReturnResume:
    inner: Continuation


@dataclass(frozen=True, slots=True)
class ExprResume:
    inner: Continuation


@dataclass(frozen=True, slots=True)
class OpaqueResume:
    reason: str


@dataclass(frozen=True, slots=True)
class TryResume:
    node: ast.Try | ast.TryStar
    phase: TryPhase
    handler: HandlerContext | None
    inner: Continuation


@dataclass(frozen=True, slots=True)
class FinallyResume:
    node: ast.Try | ast.TryStar
    pending: PendingCompletion
    previous: ExceptionRestoreRecord | None
    inner: Continuation


@dataclass(frozen=True, slots=True)
class ForResume:
    node: ast.For | ast.AsyncFor
    iterator: Value
    inner: Continuation


@dataclass(frozen=True, slots=True)
class WhileResume:
    node: ast.While
    inner: Continuation


@dataclass(frozen=True, slots=True)
class WithResume:
    node: ast.With | ast.AsyncWith
    exits: tuple[Value, ...]
    inner: Continuation


@dataclass(frozen=True, slots=True)
class DelegateResume:
    node: ast.YieldFrom
    iterator: Value
    inner: Continuation


type AttachmentValue = (
    None
    | bool
    | int
    | float
    | str
    | bytes
    | Enum
    | ReferenceKey
    | ast.AST
    | Value
    | Store
    | Frame
    | HeapObject
    | Flow
    | ExceptionContext
    | ExceptionRestoreRecord
    | Continuation
    | CellWrite
    | HeapWrite
    | HeapDelete
    | HeapDefaultWrite
    | MetadataUpdate
    | Havoc
    | tuple[AttachmentValue, ...]
    | list[AttachmentValue]
    | set[AttachmentValue]
    | frozenset[AttachmentValue]
    | dict[FieldKey, AttachmentValue]
)


@dataclass(frozen=True, slots=True)
class GraphAttachmentCommand:
    """An explicit graph-transport fixture extension, never an executable marker."""

    name: str
    payload: tuple[AttachmentValue, ...]


type ResumeCommand = (
    AssignResume
    | ReturnResume
    | ExprResume
    | OpaqueResume
    | TryResume
    | FinallyResume
    | ForResume
    | WhileResume
    | WithResume
    | DelegateResume
    | GraphAttachmentCommand
)
type ContinuationCommand = ast.stmt | ResumeCommand


@dataclass(frozen=True, slots=True)
class Continuation:
    commands: tuple[ContinuationCommand, ...] = ()
    identity: ContinuationId = ContinuationId("")
    source_point: ProgramPointId | None = None

    def __iter__(self) -> Iterator[ContinuationCommand]:
        return iter(self.commands)

    def __len__(self) -> int:
        return len(self.commands)


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, (tuple, list)):
        raise TypeError("A legacy continuation must contain a tuple/list")
    return tuple(value)


def _typed[T](value: object, expected: type[T]) -> T:
    if not isinstance(value, expected):
        raise TypeError(f"Expected {expected.__name__} in a legacy continuation")
    return value


def _phase(value: object) -> TryPhase:
    match value:
        case "body":
            return "body"
        case "handler":
            return "handler"
        case "orelse":
            return "orelse"
        case _:
            raise ValueError("Unknown try continuation phase")


def _kind(value: object) -> CompletionKind:
    match value:
        case "normal":
            return "normal"
        case "return":
            return "return"
        case "raise":
            return "raise"
        case "break":
            return "break"
        case "continue":
            return "continue"
        case "suspend":
            return "suspend"
        case _:
            raise ValueError("Unknown pending completion kind")


def _optional_text(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError("Expected optional text in a legacy continuation")


def _try_node(value: object) -> ast.Try | ast.TryStar:
    if not isinstance(value, (ast.Try, ast.TryStar)):
        raise TypeError("Expected a try node in a legacy continuation")
    return value


def _attachment_key(value: object) -> TypeGuard[FieldKey]:
    if value is None or isinstance(value, (bool, int, float, str, bytes, Enum, ReferenceKey)):
        return True
    return isinstance(value, tuple) and all(_attachment_key(item) for item in value)


def _is_attachment(value: object, seen: set[int]) -> TypeGuard[AttachmentValue]:
    from .store import Flow, Frame, HeapObject, Store

    if value is None or isinstance(
        value,
        (
            bool,
            int,
            float,
            str,
            bytes,
            Enum,
            ReferenceKey,
            ast.AST,
            Value,
            Store,
            Frame,
            HeapObject,
            Flow,
            ExceptionContext,
            ExceptionRestoreRecord,
            Continuation,
            CellWrite,
            HeapWrite,
            HeapDelete,
            HeapDefaultWrite,
            MetadataUpdate,
            Havoc,
        ),
    ):
        return True
    if id(value) in seen:
        return isinstance(value, (tuple, list, set, frozenset, dict))
    seen.add(id(value))
    if isinstance(value, (tuple, list, set, frozenset)):
        return all(_is_attachment(item, seen) for item in value)
    if isinstance(value, dict):
        return all(
            _attachment_key(key) and _is_attachment(item, seen) for key, item in value.items()
        )
    return False


def parse_graph_attachment(value: object) -> AttachmentValue:
    """Validate registered component graph nodes without changing their aliases."""
    if _is_attachment(value, set()):
        return value
    raise TypeError("Unregistered continuation graph attachment")


def _command(value: object) -> ContinuationCommand:
    if isinstance(value, str):
        return GraphAttachmentCommand(value, ())
    if isinstance(
        value,
        (
            ast.stmt,
            AssignResume,
            ReturnResume,
            ExprResume,
            OpaqueResume,
            TryResume,
            FinallyResume,
            ForResume,
            WhileResume,
            WithResume,
            DelegateResume,
            GraphAttachmentCommand,
        ),
    ):
        return value
    parts = _sequence(value)
    if not parts:
        raise ValueError("A legacy continuation command cannot be empty")
    tag = _typed(parts[0], str)
    if tag == "assign_resume" and len(parts) == 3:
        return AssignResume(
            tuple(_typed(item, ast.expr) for item in _sequence(parts[1])),
            parse_legacy_continuation(parts[2]),
        )
    if tag in {"return_resume", "expr_resume"} and len(parts) == 2:
        inner = parse_legacy_continuation(parts[1])
        return ReturnResume(inner) if tag == "return_resume" else ExprResume(inner)
    if tag == "opaque_resume" and len(parts) == 2:
        return OpaqueResume(_typed(parts[1], str))
    if tag == "try_resume" and len(parts) == 5:
        handler = None
        if parts[3] is not None:
            context = _sequence(parts[3])
            if len(context) != 2:
                raise ValueError("Invalid handler continuation context")
            handler = HandlerContext(
                _optional_text(context[0]), _typed(context[1], ExceptionRestoreRecord)
            )
        return TryResume(
            _try_node(parts[1]), _phase(parts[2]), handler, parse_legacy_continuation(parts[4])
        )
    if tag == "finally_resume" and len(parts) == 5:
        pending = _sequence(parts[2])
        if len(pending) != 3:
            raise ValueError("Invalid pending completion")
        previous = None if parts[3] is None else _typed(parts[3], ExceptionRestoreRecord)
        return FinallyResume(
            _try_node(parts[1]),
            PendingCompletion(
                _kind(pending[0]), _typed(pending[1], Value), _optional_text(pending[2])
            ),
            previous,
            parse_legacy_continuation(parts[4]),
        )
    if tag == "for_resume" and len(parts) == 4:
        node = parts[1]
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            raise TypeError("Expected a for node in a continuation")
        return ForResume(node, _typed(parts[2], Value), parse_legacy_continuation(parts[3]))
    if tag == "while_resume" and len(parts) == 3:
        return WhileResume(_typed(parts[1], ast.While), parse_legacy_continuation(parts[2]))
    if tag == "with_resume" and len(parts) == 4:
        node = parts[1]
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            raise TypeError("Expected a with node in a continuation")
        return WithResume(
            node,
            tuple(_typed(item, Value) for item in _sequence(parts[2])),
            parse_legacy_continuation(parts[3]),
        )
    if tag == "delegate_resume" and len(parts) == 4:
        return DelegateResume(
            _typed(parts[1], ast.YieldFrom),
            _typed(parts[2], Value),
            parse_legacy_continuation(parts[3]),
        )
    if tag.endswith("_resume"):
        raise ValueError("Malformed executable continuation command")
    return GraphAttachmentCommand(tag, tuple(parse_graph_attachment(item) for item in parts[1:]))


def parse_legacy_continuation(value: object) -> Continuation:
    """Validate the finite old marker codec; all returned nodes are typed records."""
    if isinstance(value, Continuation):
        return value
    return Continuation(tuple(_command(item) for item in _sequence(value)))


__all__ = [
    "AssignResume",
    "CallResumeContext",
    "Continuation",
    "ContinuationCommand",
    "DelegateResume",
    "ExceptionContext",
    "ExceptionRestoreRecord",
    "ExprResume",
    "FinallyResume",
    "ForResume",
    "GraphAttachmentCommand",
    "HandlerContext",
    "OpaqueResume",
    "PendingCompletion",
    "ResumeCommand",
    "ReturnResume",
    "TryPhase",
    "TryResume",
    "WhileResume",
    "WithResume",
    "parse_graph_attachment",
    "parse_legacy_continuation",
]
