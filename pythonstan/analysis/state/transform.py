"""Finite semantic graph copying, identity quotienting and semantic identity.

Every admitted record has an explicit field projection. One memo owns graph
shells; reference roles are registered before alias-first graph traversal.
"""

from __future__ import annotations

import ast
import struct
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from typing import Literal, TypeGuard, assert_never, overload

from pythonstan.analysis.contracts.identity import (
    CellId,
    IdentityEpoch,
    ObjectId,
    StateOwnerId,
    UnitId,
)
from pythonstan.analysis.domains.reabstract import (
    IdentitySubstitution,
    ReferenceKey,
    resolve_mapping,
)
from pythonstan.analysis.domains.values import Value, ValueOperations

from .bindings import BindingRef
from .boundary import BoundaryObject, BoundaryState, HostStateBoundary
from .continuation import (
    AssignResume,
    AttachmentValue,
    CallResumeContext,
    Continuation,
    DelegateResume,
    ExceptionContext,
    ExceptionRestoreRecord,
    ExprResume,
    FinallyResume,
    ForResume,
    GraphAttachmentCommand,
    HandlerContext,
    OpaqueResume,
    PendingCompletion,
    ReturnResume,
    TryResume,
    WhileResume,
    WithResume,
)
from .dependencies import SummaryDependency
from .effects import (
    CellWrite,
    Havoc,
    HeapDefaultWrite,
    HeapDelete,
    HeapWrite,
    MetadataUpdate,
    WriteEffect,
    remap_effect,
)
from .flow import (
    BreakFlow,
    ContinueFlow,
    ExceptionValue,
    FlowTail,
    NormalExprFlow,
    NormalStmtFlow,
    RaiseFlow,
    ReturnFlow,
    SuspendFlow,
)
from .guards import MembershipGuard
from .handles import ObjectHandle, StateHandle
from .keys import FieldKey
from .metadata import (
    BindingsField,
    BooleanField,
    ComponentField,
    ComponentPayload,
    ContinuationField,
    ExceptionField,
    FrameField,
    GeneratorMeta,
    IntegerField,
    KeySequenceField,
    KeysField,
    LiteralGraphField,
    Metadata,
    ReferenceField,
    ReferencesField,
    StateHandleField,
    StatementsField,
    StoreField,
    SuspensionStatesField,
    TextField,
    TextSequenceField,
    ValueField,
    ValueSequenceField,
    validate_component,
)
from .store import Frame, FunctionInfo, HeapObject, Store, Unit

type GraphRecord = (
    BoundaryObject
    | Store
    | GeneratorMeta
    | HeapObject
    | Frame
    | FunctionInfo
    | Unit
    | StateHandle
    | ObjectHandle
    | BindingRef
    | MembershipGuard
    | BoundaryState
    | HostStateBoundary
    | SummaryDependency
    | FlowTail
    | ExceptionValue
    | NormalExprFlow
    | NormalStmtFlow
    | ReturnFlow
    | RaiseFlow
    | BreakFlow
    | ContinueFlow
    | SuspendFlow
    | Continuation
    | ExceptionContext
    | ExceptionRestoreRecord
    | CallResumeContext
    | PendingCompletion
    | HandlerContext
    | AssignResume
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
    | CellWrite
    | HeapWrite
    | HeapDelete
    | HeapDefaultWrite
    | MetadataUpdate
    | Havoc
)
type GraphPayload = (
    ComponentPayload
    | AttachmentValue
    | GraphRecord
    | complex
    | Sequence[GraphPayload]
    | set[GraphPayload]
    | frozenset[GraphPayload]
    | set[FieldKey]
    | set[WriteEffect]
    | set[str]
    | set[UnitId]
    | dict[FieldKey, GraphPayload]
    | dict[FieldKey, Value]
    | dict[CellId, Value]
    | dict[ObjectId, HeapObject]
    | dict[ObjectId, BoundaryObject]
    | dict[str, str]
    | dict[str, MembershipGuard]
    | dict[UnitId, Unit]
    | dict[ObjectId, FunctionInfo]
    | dict[UnitId, SummaryDependency]
)
type PayloadMemo = dict[int, GraphPayload]
type DictionaryRole = Literal["plain", "cells", "heap", "values", "guards"]
type SequenceRole = Literal["objects", "bindings", "binding"]
type Signature = None | bool | int | float | str | bytes | Enum | Value | tuple[Signature, ...]


def _record(value: object) -> TypeGuard[GraphRecord]:
    return type(value) in (
        BoundaryObject,
        Store,
        GeneratorMeta,
        HeapObject,
        Frame,
        FunctionInfo,
        Unit,
        StateHandle,
        ObjectHandle,
        BindingRef,
        MembershipGuard,
        BoundaryState,
        HostStateBoundary,
        SummaryDependency,
        FlowTail,
        ExceptionValue,
        NormalExprFlow,
        NormalStmtFlow,
        ReturnFlow,
        RaiseFlow,
        BreakFlow,
        ContinueFlow,
        SuspendFlow,
        Continuation,
        ExceptionContext,
        ExceptionRestoreRecord,
        CallResumeContext,
        PendingCompletion,
        HandlerContext,
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
        CellWrite,
        HeapWrite,
        HeapDelete,
        HeapDefaultWrite,
        MetadataUpdate,
        Havoc,
    )


def _dictionary(value: object) -> TypeGuard[dict[object, object]]:
    return type(value) is dict


def _list(value: object) -> TypeGuard[list[object]]:
    return type(value) is list


def _tuple(value: object) -> TypeGuard[tuple[object, ...]]:
    return type(value) is tuple


def _set(value: object) -> TypeGuard[set[object]]:
    return type(value) is set


def _frozen(value: object) -> TypeGuard[frozenset[object]]:
    return type(value) is frozenset


def _field_key(value: object) -> TypeGuard[FieldKey]:
    if (
        value is None
        or type(value) in (bool, int, float, str, bytes, ReferenceKey)
        or isinstance(value, Enum)
    ):
        return True
    return _tuple(value) and all(_field_key(item) for item in value)


def _fields(value: GraphRecord) -> tuple[tuple[str, GraphPayload], ...]:
    """The actual finite schema; no reflective dataclass fallback is permitted."""
    if isinstance(value, BoundaryObject):
        return (
            ("kind", value.kind),
            ("fields", value.fields),
            ("default", value.default),
            ("maybe_missing", value.maybe_missing),
            ("singleton", value.singleton),
            ("class_ids", value.class_ids),
            ("_metadata", value._metadata),
            ("havoc", value.havoc),
            ("_runtime_root", value._runtime_root),
        )
    if isinstance(value, SummaryDependency):
        return (
            ("producer", value.producer),
            ("observed_revision", value.observed_revision),
            ("input_scope", value.input_scope),
            ("policy", value.policy),
            ("epoch", value.epoch),
        )
    if isinstance(value, HostStateBoundary):
        return (
            ("units", value.units),
            ("functions", value.functions),
            ("extras", value.extras),
            ("events", value.events),
        )
    if isinstance(value, GeneratorMeta):
        return (
            ("function", value.function),
            ("frame", value.frame),
            ("entry", value.entry),
            ("body", value.body),
            ("stage", value.stage),
            ("index", value.index),
            ("remaining", value.remaining),
            ("resume_count", value.resume_count),
            ("owned_exception", value.owned_exception),
            ("alternatives", value.alternatives),
            ("overflow", value.overflow),
        )
    if isinstance(value, Store):
        return (
            ("cells", value.cells),
            ("heap", value.heap),
            ("initialized", value.initialized),
            ("writes", value.writes),
            ("events", value.events),
            ("obligations", value.obligations),
            ("havoc_namespaces", value.havoc_namespaces),
            ("havoc_imports", value.havoc_imports),
            ("guards", value.guards),
            ("identity_epoch", value.identity_epoch),
            ("state_revision", value.state_revision),
        )
    if isinstance(value, HeapObject):
        return (
            ("kind", value.kind),
            ("fields", value.fields),
            ("default", value.default),
            ("maybe_missing", value.maybe_missing),
            ("singleton", value.singleton),
            ("class_ids", value.class_ids),
            ("_metadata", value._metadata),
            ("havoc", value.havoc),
        )
    if isinstance(value, Frame):
        return (
            ("module", value.module),
            ("qualname", value.qualname),
            ("context", value.context),
            ("identity", value.identity),
            ("locals", value.locals),
            ("closure", value.closure),
            ("globals", value.globals),
            ("nonlocals", value.nonlocals),
            ("is_module", value.is_module),
            ("is_class", value.is_class),
            # Runtime operation authority is retained by identity on copy;
            # its immutable policy digest is the semantic graph projection.
            ("execution", None if value.execution is None else value.execution.policy.digest),
        )
    if isinstance(value, FunctionInfo):
        return (
            ("identifier", value.identifier),
            ("node", value.node),
            ("module", value.module),
            ("qualname", value.qualname),
            ("closure", value.closure),
            ("defaults", value.defaults),
            ("kw_defaults", value.kw_defaults),
            ("locals", value.locals),
            ("globals", value.globals),
            ("nonlocals", value.nonlocals),
            ("is_generator", value.is_generator),
            ("is_async", value.is_async),
        )
    if isinstance(value, Unit):
        return (
            ("key", value.key),
            ("frame", value.frame),
            ("body", value.body),
            ("entry", value.entry),
            ("outcomes", value.outcomes),
            ("consumers", value.consumers),
            ("dependencies", value.dependencies),
            ("revision", value.revision),
            ("visits", value.visits),
            ("is_module", value.is_module),
        )
    if isinstance(value, StateHandle):
        return (
            ("owner", value.owner),
            ("epoch", value.epoch),
            ("revision", value.revision),
            ("root", value.root),
        )
    if isinstance(value, ObjectHandle):
        return (
            ("identity", value.identity),
            ("observed_epoch", value.observed_epoch),
        )
    if isinstance(value, BindingRef):
        return (
            ("name", value.name),
            ("cell", value.cell),
            ("scope", value.scope),
        )
    if isinstance(value, MembershipGuard):
        return (
            ("owner", value.owner),
            ("key", value.key),
            ("positive", value.positive),
        )
    if isinstance(value, BoundaryState):
        return (
            ("cells", value.cells),
            ("heap", value.heap),
            ("initialized", value.initialized),
            ("writes", value.writes),
            ("events", value.events),
            ("obligations", value.obligations),
            ("completion", value.completion),
            ("value", value.value),
            ("exception", value.exception),
            ("continuation", value.continuation),
            ("cleanups", value.cleanups),
            ("resume_target", value.resume_target),
            ("havoc_namespaces", value.havoc_namespaces),
            ("havoc_imports", value.havoc_imports),
            ("guards", value.guards),
            ("identity_epoch", value.identity_epoch),
            ("_store_root", value._store_root),
            ("state_revision", value.state_revision),
        )
    if isinstance(value, FlowTail):
        return (
            ("continuation", value.continuation),
            ("cleanups", value.cleanups),
            ("resume_target", value.resume_target),
        )
    if isinstance(value, ExceptionValue):
        return (
            ("value", value.value),
            ("family", value.family),
        )
    if isinstance(value, NormalExprFlow):
        return (
            ("state", value.state),
            ("value", value.value),
            ("tail", value.tail),
            ("kind", value.kind),
            ("form", value.form),
        )
    if isinstance(value, NormalStmtFlow):
        return (
            ("state", value.state),
            ("tail", value.tail),
            ("kind", value.kind),
            ("form", value.form),
        )
    if isinstance(value, ReturnFlow):
        return (
            ("state", value.state),
            ("value", value.value),
            ("tail", value.tail),
            ("kind", value.kind),
        )
    if isinstance(value, RaiseFlow):
        return (
            ("state", value.state),
            ("exception_value", value.exception_value),
            ("tail", value.tail),
            ("kind", value.kind),
        )
    if isinstance(value, BreakFlow):
        return (
            ("state", value.state),
            ("tail", value.tail),
            ("kind", value.kind),
        )
    if isinstance(value, ContinueFlow):
        return (
            ("state", value.state),
            ("tail", value.tail),
            ("kind", value.kind),
        )
    if isinstance(value, SuspendFlow):
        return (
            ("state", value.state),
            ("value", value.value),
            ("tail", value.tail),
            ("kind", value.kind),
        )
    if isinstance(value, Continuation):
        return (
            ("commands", value.commands),
            ("identity", value.identity),
            ("source_point", value.source_point),
        )
    if isinstance(value, ExceptionContext):
        return (
            ("value", value.value),
            ("type_value", value.type_value),
            ("owner", value.owner),
        )
    if isinstance(value, ExceptionRestoreRecord):
        return (
            ("previous", value.previous),
            ("restoring_frame", value.restoring_frame),
        )
    if isinstance(value, CallResumeContext):
        return (
            ("caller_exception", value.caller_exception),
            ("generator_frame", value.generator_frame),
        )
    if isinstance(value, PendingCompletion):
        return (
            ("kind", value.kind),
            ("value", value.value),
            ("exception", value.exception),
        )
    if isinstance(value, HandlerContext):
        return (
            ("name", value.name),
            ("previous", value.previous),
        )
    if isinstance(value, AssignResume):
        return (
            ("targets", value.targets),
            ("inner", value.inner),
        )
    if isinstance(value, ReturnResume):
        return (("inner", value.inner),)
    if isinstance(value, ExprResume):
        return (("inner", value.inner),)
    if isinstance(value, OpaqueResume):
        return (("reason", value.reason),)
    if isinstance(value, TryResume):
        return (
            ("node", value.node),
            ("phase", value.phase),
            ("handler", value.handler),
            ("inner", value.inner),
        )
    if isinstance(value, FinallyResume):
        return (
            ("node", value.node),
            ("pending", value.pending),
            ("previous", value.previous),
            ("inner", value.inner),
        )
    if isinstance(value, ForResume):
        return (
            ("node", value.node),
            ("iterator", value.iterator),
            ("inner", value.inner),
        )
    if isinstance(value, WhileResume):
        return (
            ("node", value.node),
            ("inner", value.inner),
        )
    if isinstance(value, WithResume):
        return (
            ("node", value.node),
            ("exits", value.exits),
            ("inner", value.inner),
        )
    if isinstance(value, DelegateResume):
        return (
            ("node", value.node),
            ("iterator", value.iterator),
            ("inner", value.inner),
        )
    if isinstance(value, GraphAttachmentCommand):
        return (
            ("name", value.name),
            ("payload", value.payload),
        )
    if isinstance(value, CellWrite):
        return (("cell", value.cell), ("weak", value.weak))
    if isinstance(value, HeapWrite):
        return (
            ("owner", value.owner),
            ("key", value.key),
        )
    if isinstance(value, HeapDelete):
        return (
            ("owner", value.owner),
            ("key", value.key),
        )
    if isinstance(value, HeapDefaultWrite):
        return (("owner", value.owner),)
    if isinstance(value, MetadataUpdate):
        return (
            ("owner", value.owner),
            ("aspect", value.aspect),
        )
    if isinstance(value, Havoc):
        return (
            ("objects", value.objects),
            ("cells", value.cells),
            ("namespaces", value.namespaces),
            ("imports", value.imports),
        )
    assert_never(value)


def _semantic_fields(value: GraphRecord) -> tuple[tuple[str, GraphPayload], ...]:
    if isinstance(value, StateHandle):
        return (("root", value.root),)
    if isinstance(value, ObjectHandle):
        return (("identity", value.identity),)
    fields = _fields(value)
    if isinstance(value, (Store, BoundaryState)):
        return tuple(
            (name, item)
            for name, item in fields
            if name not in {"identity_epoch", "state_revision"}
        )
    return fields


def _valid_node(value: object) -> bool:
    return (
        value is None
        or type(value) in (str, int, float, complex, bool, bytes, Value, ReferenceKey, Metadata)
        or isinstance(value, (Enum, ast.AST))
        or _record(value)
        or _dictionary(value)
        or _list(value)
        or _tuple(value)
        or _set(value)
        or _frozen(value)
    )


def semantic_payload_equal(
    left: object, right: object, seen: set[tuple[int, int]] | None = None
) -> bool:
    if not _valid_node(left) or not _valid_node(right):
        raise TypeError("Unregistered semantic payload in equality")
    if type(left) is not type(right):
        return False
    if type(left) is Value:
        return left == right
    if left is right:
        return True
    if isinstance(left, complex) and isinstance(right, complex):
        return struct.pack("!dd", left.real, left.imag) == struct.pack(
            "!dd", right.real, right.imag
        )
    seen = set() if seen is None else seen
    pair = id(left), id(right)
    if pair in seen:
        return True
    seen.add(pair)
    if isinstance(left, Metadata) and isinstance(right, Metadata):
        lf, rf = left.transformation_entries(), right.transformation_entries()
        return (
            left.kind == right.kind
            and lf.keys() == rf.keys()
            and all(
                type(lf[key]) is type(rf[key])
                and semantic_payload_equal(lf[key].value, rf[key].value, seen)
                for key in lf
            )
        )
    if _dictionary(left) and _dictionary(right):
        return left.keys() == right.keys() and all(
            semantic_payload_equal(left[key], right[key], seen) for key in left
        )
    if (_tuple(left) and _tuple(right)) or (_list(left) and _list(right)):
        return len(left) == len(right) and all(
            semantic_payload_equal(a, b, seen) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, ast.AST) and isinstance(right, ast.AST):
        return ast.dump(left, include_attributes=True) == ast.dump(right, include_attributes=True)
    if _record(left) and _record(right):
        return all(
            semantic_payload_equal(a, b, seen)
            for (_, a), (_, b) in zip(_semantic_fields(left), _semantic_fields(right), strict=True)
        )
    return left == right


def _key_repr(pair: tuple[object, object]) -> str:
    return repr(pair[0])


def payload_signature(value: object, seen: dict[int, int] | None = None) -> Signature:
    """Hashable finite graph identity excluding physical owner/version state."""
    if not _valid_node(value):
        raise TypeError("Unrepresentable semantic payload: " + type(value).__name__)
    seen = {} if seen is None else seen
    if isinstance(value, Value):
        return ("value", value)
    if value is None or isinstance(value, (str, int, bool, bytes, Enum)):
        return (type(value).__name__, value)
    if isinstance(value, float):
        return ("float", struct.pack("!d", value))
    if isinstance(value, complex):
        return ("complex", struct.pack("!dd", value.real, value.imag))
    if isinstance(value, ReferenceKey):
        return ("ReferenceKey", value.identifier)
    if isinstance(value, ast.AST):
        return ("ast", ast.dump(value, include_attributes=True))
    if id(value) in seen:
        return ("cycle", seen[id(value)])
    seen[id(value)] = len(seen)
    if isinstance(value, Metadata):
        return (
            "metadata",
            value.kind,
            tuple(
                (name, type(field).__name__, payload_signature(field.value, seen))
                for name, field in value.transformation_entries().items()
            ),
        )
    if _dictionary(value):
        return (
            "dict",
            tuple(
                (payload_signature(key, seen), payload_signature(item, seen))
                for key, item in sorted(value.items(), key=_key_repr)
            ),
        )
    if _tuple(value) or _list(value):
        return (type(value).__name__, tuple(payload_signature(item, seen) for item in value))
    if _set(value) or _frozen(value):
        return (
            type(value).__name__,
            tuple(sorted((payload_signature(item, seen) for item in value), key=repr)),
        )
    if _record(value):
        return (
            type(value).__name__,
            tuple((name, payload_signature(item, seen)) for name, item in _semantic_fields(value)),
        )
    raise TypeError("Unrepresentable semantic payload: " + type(value).__name__)


def payload_values_leq(left: object, right: object, ops: ValueOperations) -> bool:
    """Order Value leaves while requiring the same explicit non-value shape."""
    seen: set[tuple[int, int]] = set()

    def compare(a: object, b: object) -> bool:
        if isinstance(a, Value) and isinstance(b, Value):
            return ops.leq(a, b)
        if type(a) is not type(b):
            return False
        if a is b or (id(a), id(b)) in seen:
            return True
        seen.add((id(a), id(b)))
        if isinstance(a, Metadata) and isinstance(b, Metadata):
            af, bf = a.transformation_entries(), b.transformation_entries()
            return af.keys() == bf.keys() and all(
                type(af[key]) is type(bf[key]) and compare(af[key].value, bf[key].value)
                for key in af
            )
        if _record(a) and _record(b):
            return all(
                compare(x, y)
                for (_, x), (_, y) in zip(_semantic_fields(a), _semantic_fields(b), strict=True)
            )
        if _dictionary(a) and _dictionary(b):
            return a.keys() == b.keys() and all(compare(a[key], b[key]) for key in a)
        if (_tuple(a) or _list(a)) and (_tuple(b) or _list(b)):
            return len(a) == len(b) and all(compare(x, y) for x, y in zip(a, b, strict=True))
        return semantic_payload_equal(a, b)

    return compare(left, right)


def merge_metadata_values(
    left: Metadata, right: Metadata, merge: Callable[[Value, Value], Value]
) -> bool:
    """Join Value leaves of equal-shaped, already detached metadata graphs.

    Non-value shape differences stay explicit incompatibilities. Record fields
    are enumerated by the same finite schema as copy/quotient. A shared pair
    memo handles cycles; no new clone or root owner is introduced.
    """
    updated: dict[tuple[int, int], object] = {}

    def compatible(a: object, b: object, pairs: set[tuple[int, int]]) -> bool:
        if isinstance(a, Value) and isinstance(b, Value):
            return True
        if type(a) is not type(b):
            return False
        if a is b or (id(a), id(b)) in pairs:
            return True
        pairs.add((id(a), id(b)))
        if isinstance(a, Metadata) and isinstance(b, Metadata):
            af, bf = a.transformation_entries(), b.transformation_entries()
            return af.keys() == bf.keys() and all(
                type(af[key]) is type(bf[key]) and compatible(af[key].value, bf[key].value, pairs)
                for key in af
            )
        if _record(a) and _record(b):
            return all(
                compatible(x, y, pairs)
                for (_, x), (_, y) in zip(_fields(a), _fields(b), strict=True)
            )
        if _dictionary(a) and _dictionary(b):
            return a.keys() == b.keys() and all(compatible(a[key], b[key], pairs) for key in a)
        if (_tuple(a) or _list(a)) and (_tuple(b) or _list(b)):
            return len(a) == len(b) and all(
                compatible(x, y, pairs) for x, y in zip(a, b, strict=True)
            )
        return semantic_payload_equal(a, b)

    if not compatible(left, right, set()):
        return False

    def update(a: object, b: object) -> object:
        if isinstance(a, Value) and isinstance(b, Value):
            return merge(a, b)
        if a is b:
            return a
        pair = id(a), id(b)
        if pair in updated:
            return updated[pair]
        if _tuple(a) and _tuple(b):
            items = tuple(update(x, y) for x, y in zip(a, b, strict=True))
            if pair in updated:
                return updated[pair]
            updated[pair] = items
            return items
        updated[pair] = a
        if isinstance(a, Metadata) and isinstance(b, Metadata):
            af, bf = a.transformation_entries(), b.transformation_entries()
            for key in af:
                a[key] = validate_component(update(af[key].value, bf[key].value))
        elif _record(a) and _record(b):
            for (name, x), (_, y) in zip(_fields(a), _fields(b), strict=True):
                object.__setattr__(a, name, update(x, y))
        elif _dictionary(a) and _dictionary(b):
            for dictionary_key in a:
                a[dictionary_key] = update(a[dictionary_key], b[dictionary_key])
        elif _list(a) and _list(b):
            for index, item in enumerate(a):
                a[index] = update(item, b[index])
        return a

    update(left, right)
    return True


def _summarized_keys(metadata: Metadata) -> frozenset[FieldKey]:
    keys = metadata.lookup("summarized_keys")
    return frozenset() if keys is None else frozenset(keys)


class PayloadTransformer:
    def __init__(
        self,
        mapping: IdentitySubstitution | Mapping[ObjectId, ObjectId] | None = None,
        ops: ValueOperations | None = None,
        scalar: Callable[[Value], Value] | None = None,
        epoch: int | None = None,
        memo: PayloadMemo | None = None,
        charge: Callable[[int], None] | None = None,
    ) -> None:
        if isinstance(mapping, IdentitySubstitution):
            self.mapping = mapping.object_aliases
            self.cell_mapping = mapping.cell_aliases
        else:
            self.mapping = {} if mapping is None else mapping
            self.cell_mapping = {CellId(old): CellId(new) for old, new in self.mapping.items()}
        self.ops = ops
        self.scalar = scalar
        self.epoch = epoch
        self.memo = {} if memo is None else memo
        self._memo_anchors = frozenset(self.memo)
        self.charge = charge
        self.work = 0
        self.preparation_work = 0
        self._depth = 0
        self._prepared = False
        self._dict_roles: dict[int, DictionaryRole] = {}
        self._dict_active: set[int] = set()
        self._sequence_roles: dict[int, SequenceRole] = {}
        self._heap_groups: dict[int, HeapObject] = {}
        self._heap_active: set[int] = set()
        self._heap_finished: set[int] = set()
        self._heap_started: set[int] = set()
        self._heap_collisions: deque[tuple[HeapObject, HeapObject]] = deque()

    def _charge(self, amount: int = 1) -> None:
        self.work += amount
        if self.charge is not None:
            self.charge(amount)

    def address(self, identifier: ObjectId) -> ObjectId:
        return resolve_mapping(identifier, self.mapping)

    def cell_address(self, identifier: CellId) -> CellId:
        return resolve_mapping(identifier, self.cell_mapping)

    def key(self, key: FieldKey) -> FieldKey:
        if type(key) is ReferenceKey:
            return ReferenceKey(self.address(key.identifier))
        if isinstance(key, tuple):
            return tuple(self.key(item) for item in key)
        return key

    def _sequence_role(self, value: object, role: SequenceRole) -> None:
        if (_tuple(value) or _list(value)) and not value:
            return
        previous = self._sequence_roles.get(id(value))
        if previous is not None and previous != role:
            raise ValueError("One graph sequence has incompatible reference roles")
        self._sequence_roles[id(value)] = role
        if role == "bindings" and (_tuple(value) or _list(value)):
            for pair in value:
                self._sequence_role(pair, "binding")

    def _prepare(self, value: object) -> None:
        seen: set[int] = set()
        sources: dict[int, HeapObject] = {}
        parents: dict[int, int] = {}

        def representative(identity: int) -> int:
            while parents[identity] != identity:
                identity = parents[identity]
            return identity

        def register_heap(heap: Mapping[ObjectId, HeapObject]) -> None:
            groups: dict[tuple[type[HeapObject], ObjectId], int] = {}
            for identifier, obj in heap.items():
                identity = id(obj)
                sources[identity] = obj
                parents.setdefault(identity, identity)
                key = type(obj), self.address(identifier)
                previous = groups.get(key)
                if previous is None:
                    groups[key] = identity
                else:
                    parents[representative(identity)] = representative(previous)

        def discover(node: object) -> None:
            self.preparation_work += 1
            self._charge()
            if not _valid_node(node):
                raise TypeError("Unrepresentable semantic payload: " + type(node).__name__)
            if node is None or isinstance(
                node, (bool, int, float, complex, str, bytes, Enum, ast.AST, Value, ReferenceKey)
            ):
                return
            if id(node) in seen:
                return
            seen.add(id(node))
            if isinstance(node, Metadata):
                for field in node.transformation_entries().values():
                    if isinstance(field, ReferencesField):
                        self._sequence_role(field.value, "objects")
                    elif isinstance(field, BindingsField):
                        self._sequence_role(field.value, "bindings")
                    discover(field.value)
            elif _record(node):
                if isinstance(node, (Store, BoundaryState)):
                    self._dict_roles[id(node.cells)] = "cells"
                    self._dict_roles[id(node.heap)] = "heap"
                    if self.mapping:
                        self._dict_roles[id(node.guards)] = "guards"
                    register_heap(node.heap)
                if isinstance(node, (Frame, FunctionInfo)):
                    self._sequence_role(node.closure, "bindings")
                for _, item in _fields(node):
                    discover(item)
            elif _dictionary(node):
                for key, child in node.items():
                    if not _field_key(key):
                        raise TypeError("Unregistered graph dictionary key")
                    discover(child)
            elif _tuple(node) or _list(node) or _set(node) or _frozen(node):
                for element in node:
                    discover(element)

        discover(value)
        shells: dict[int, HeapObject] = {}
        for identity, source in sources.items():
            group = representative(identity)
            shell = shells.get(group)
            if shell is None:
                shell = object.__new__(type(source))
                shells[group] = shell
            self._heap_groups[identity] = shell
            self.memo[identity] = shell

    @overload
    def visit(self, value: None) -> None: ...
    @overload
    def visit[T: GraphPayload](self, value: T) -> T: ...
    def visit(self, value: object) -> object:
        """Preserve admitted types when any supplied memo is coherent.

        Memo entries must originate from this graph copy or explicit same-shape
        record anchors. Erased container element types and source provenance
        remain a caller precondition; shallow node-shape poisoning is rejected.
        """
        return self._transform(value)

    @staticmethod
    def _memo_result(source: object, cached: GraphPayload) -> GraphPayload:
        # Do not inspect unfinished record/container shells: recursive copying
        # registers them before their fields and contents can be populated.
        if type(source) is not type(cached) or not _valid_node(cached):
            raise TypeError(
                "Incoherent payload memo: "
                + type(source).__name__
                + " cannot reuse "
                + type(cached).__name__
            )
        return cached

    def _transform(self, value: object) -> GraphPayload:
        if self._depth == 0 and not self._prepared and (self.mapping or self.cell_mapping):
            self._prepare(value)
            self._prepared = True
        self._depth += 1
        try:
            result = self._visit(value)
        finally:
            self._depth -= 1
        if self._depth == 0:
            self._finish_collisions()
        return result

    def metadata(self, value: Metadata) -> Metadata:
        return self.visit(value)

    def metadata_field(self, field: ComponentField) -> ComponentField:
        match field:
            case ReferenceField(value):
                return ReferenceField(self.address(value))
            case ReferencesField(value):
                return ReferencesField(self.visit(value))
            case BindingsField(value):
                return BindingsField(self.visit(value))
            case IntegerField(value):
                return IntegerField(value)
            case BooleanField(value):
                return BooleanField(value)
            case TextField(value):
                return TextField(value)
            case ValueField(value):
                return ValueField(self.visit(value))
            case FrameField(value):
                return FrameField(self.visit(value))
            case StoreField(value):
                return StoreField(self.visit(value))
            case StateHandleField(value):
                return StateHandleField(self.visit(value))
            case SuspensionStatesField(value):
                return SuspensionStatesField(self.visit(value))
            case ContinuationField(value):
                return ContinuationField(self.visit(value))
            case StatementsField(value):
                return StatementsField(self.visit(value))
            case ExceptionField(value):
                return ExceptionField(self.visit(value))
            case KeysField(value):
                return KeysField(self.visit(value))
            case KeySequenceField(value):
                return KeySequenceField(self.visit(value))
            case ValueSequenceField(value):
                return ValueSequenceField(self.visit(value))
            case TextSequenceField(value):
                return TextSequenceField(self.visit(value))
            case LiteralGraphField(value):
                return LiteralGraphField(self.visit(value))
            case _:
                assert_never(field)

    def _role_sequence(self, value: object, role: SequenceRole) -> GraphPayload:
        if not (_tuple(value) or _list(value)):
            raise TypeError("Reference role requires a tuple/list")
        items: list[GraphPayload] = []
        if role == "objects":
            for item in value:
                if type(item) is not str:
                    raise TypeError("Object sequence contains a non-identity")
                items.append(self.address(ObjectId(item)))
        elif role == "binding":
            if len(value) != 2 or not isinstance(value[0], str) or not isinstance(value[1], str):
                raise TypeError("A closure binding requires a name and cell identity")
            items.extend((value[0], self.cell_address(CellId(value[1]))))
        else:
            items.extend(self._visit(item) for item in value)
        result: GraphPayload = items if _list(value) else tuple(items)
        self.memo[id(value)] = result
        return result

    def _mapped_dict(
        self,
        data: dict[object, object],
        role: DictionaryRole = "plain",
        collisions: set[FieldKey] | None = None,
    ) -> dict[FieldKey, GraphPayload]:
        identity = id(data)
        if role != "plain":
            self._dict_roles[identity] = role
        role = self._dict_roles.get(identity, role)
        existing = self.memo.get(identity)
        if identity not in self.memo:
            result: dict[FieldKey, GraphPayload] = {}
            self.memo[identity] = result
        else:
            existing = self._memo_result(data, existing)
            if not _graph_map(existing):
                raise TypeError("Memoized graph node changed dictionary shape")
            result = existing
        if identity in self._dict_active or identity in self._memo_anchors:
            return result
        result.clear()
        if role == "guards":
            return result
        self._dict_active.add(identity)
        started = role
        try:
            for key, value in data.items():
                mapped = self._visit(value)
                role = self._dict_roles.get(identity, role)
                if not _field_key(key):
                    raise TypeError("Unregistered graph dictionary key")
                if role == "cells":
                    if not isinstance(key, str):
                        raise TypeError("Cell identity must be text")
                    mapped_key: FieldKey = self.cell_address(CellId(key))
                elif role == "heap":
                    if not isinstance(key, str):
                        raise TypeError("Object identity must be text")
                    mapped_key = self.address(ObjectId(key))
                else:
                    mapped_key = self.key(key)
                if role == "heap" and mapped_key != key:
                    if not isinstance(mapped, HeapObject):
                        raise TypeError("Heap contains a non-object")
                    mapped.singleton = False
                if mapped_key in result:
                    if collisions is not None:
                        collisions.add(mapped_key)
                    previous = result[mapped_key]
                    if previous is not mapped:
                        if self.ops is None:
                            raise ValueError("Key collision requires domain operations")
                        if isinstance(previous, Value) and isinstance(mapped, Value):
                            mapped = self.ops.join(previous, mapped)
                        elif (
                            role == "heap"
                            and isinstance(previous, HeapObject)
                            and isinstance(mapped, HeapObject)
                        ):
                            self._heap_collisions.append((previous, mapped))
                            mapped = previous
                        else:
                            raise ValueError("Unrepresentable non-Value key collision")
                result[mapped_key] = mapped
        finally:
            self._dict_active.remove(identity)
        if self._dict_roles.get(identity, started) != started:
            return self._mapped_dict(data, self._dict_roles[identity], collisions)
        return result

    def heap_contribution(self, value: HeapObject, *, into: HeapObject | None = None) -> HeapObject:
        """Copy one contribution while memoized owner edges keep their anchor."""
        result = object.__new__(type(value)) if into is None else into
        self._fill_record(value, result)
        return result

    def store_contribution(self, value: Store, *, into: Store) -> Store:
        """Fill a store shell whose input owner roots are already anchored."""
        self._fill_record(value, into)
        return into

    def _visit_record(self, value: GraphRecord) -> GraphRecord:
        result = object.__new__(type(value))
        self.memo[id(value)] = result
        self._fill_record(value, result)
        return result

    def _fill_record(self, value: GraphRecord, result: GraphRecord) -> None:
        collisions: set[FieldKey] = set()
        for name, item in _fields(value):
            if isinstance(value, StateHandle):
                if name == "owner":
                    root = self.visit(value.root)
                    mapped: GraphPayload = StateOwnerId(f"store:{id(root)}")
                elif name == "epoch" and self.epoch is not None:
                    mapped = IdentityEpoch(self.epoch)
                else:
                    mapped = self._visit(item)
            elif isinstance(value, BindingRef) and name == "cell":
                mapped = self.cell_address(value.cell)
            elif isinstance(value, ObjectHandle) and name == "identity":
                mapped = self.address(value.identity)
            elif (
                isinstance(value, ObjectHandle)
                and name == "observed_epoch"
                and self.epoch is not None
            ):
                mapped = IdentityEpoch(self.epoch)
            elif isinstance(value, (Store, BoundaryState)) and name == "cells":
                mapped = self._mapped_dict(_objects(value.cells), "cells")
            elif isinstance(value, (Store, BoundaryState)) and name == "heap":
                mapped = self._mapped_dict(_objects(value.heap), "heap")
            elif isinstance(value, (Store, BoundaryState)) and name == "guards" and self.mapping:
                mapped = self._mapped_dict(_objects(value.guards), "guards")
            elif (
                isinstance(value, (Store, BoundaryState))
                and name == "identity_epoch"
                and self.epoch is not None
            ):
                mapped = self.epoch
            elif isinstance(value, (Frame, FunctionInfo)) and name == "closure":
                mapped = self._visit(value.closure)
            elif isinstance(value, FunctionInfo) and name == "identifier":
                mapped = self.address(value.identifier)
            elif isinstance(value, GeneratorMeta) and name == "function":
                mapped = None if value.function is None else self.address(value.function)
            elif isinstance(value, HeapObject) and name == "class_ids":
                mapped = frozenset(self.address(identifier) for identifier in value.class_ids)
            elif isinstance(value, HeapObject) and name == "fields":
                mapped = self._mapped_dict(_objects(value.fields), "values", collisions)
            else:
                mapped = self._visit(item)
            object.__setattr__(result, name, mapped)
        if isinstance(value, Frame) and isinstance(result, Frame):
            object.__setattr__(result, "execution", value.execution)
        if isinstance(result, HeapObject) and collisions:
            result.metadata["summarized_keys"] = _summarized_keys(result.metadata) | frozenset(
                collisions
            )
            result.metadata.pop("length", None)
            result.metadata.pop("length_value", None)

    def _visit_heap(self, value: HeapObject) -> HeapObject:
        identity = id(value)
        result = self._heap_groups[identity]
        if identity in self._heap_active or identity in self._heap_finished:
            return result
        self._heap_active.add(identity)
        if id(result) in self._heap_started:
            contribution = object.__new__(type(value))
        else:
            self._heap_started.add(id(result))
            contribution = result
        try:
            self._fill_record(value, contribution)
        finally:
            self._heap_active.remove(identity)
        self._heap_finished.add(identity)
        if contribution is not result:
            self._heap_collisions.append((result, contribution))
        return result

    def _visit(self, value: object) -> GraphPayload:
        self._charge()
        if not _valid_node(value):
            raise TypeError("Unrepresentable semantic payload: " + type(value).__name__)
        if isinstance(value, ReferenceKey):
            return ReferenceKey(self.address(value.identifier))
        if isinstance(value, MembershipGuard):
            return MembershipGuard(self.address(value.owner), self.key(value.key), value.positive)
        if isinstance(
            value, (CellWrite, HeapWrite, HeapDelete, HeapDefaultWrite, MetadataUpdate, Havoc)
        ):
            return remap_effect(value, self.mapping, self.cell_mapping)
        if isinstance(value, Value):
            result = (
                self.ops.remap(value, self.mapping)
                if self.ops is not None and self.mapping
                else value
            )
            return self.scalar(result) if self.scalar is not None else result
        if value is None or isinstance(
            value, (str, int, float, complex, bool, bytes, Enum, ast.AST)
        ):
            return value
        if isinstance(value, HeapObject) and id(value) in self._heap_groups:
            return self._visit_heap(value)
        if id(value) in self.memo:
            return self._memo_result(value, self.memo[id(value)])
        if id(value) in self._sequence_roles:
            return self._role_sequence(value, self._sequence_roles[id(value)])
        if isinstance(value, Metadata):
            result_metadata = value.new_shell()
            self.memo[id(value)] = result_metadata
            result_metadata.replace_entries(
                {
                    name: self.metadata_field(field)
                    for name, field in value.transformation_entries().items()
                }
            )
            return result_metadata
        if _record(value):
            return self._visit_record(value)
        if _dictionary(value):
            return self._mapped_dict(value)
        if _list(value):
            result_list: list[GraphPayload] = []
            self.memo[id(value)] = result_list
            result_list.extend(self._visit(item) for item in value)
            return result_list
        if _tuple(value):
            items = tuple(self._visit(item) for item in value)
            if id(value) in self.memo:
                return self._memo_result(value, self.memo[id(value)])
            self.memo[id(value)] = items
            return items
        if _set(value):
            result_set: set[GraphPayload] = set()
            self.memo[id(value)] = result_set
            result_set.update(self._visit(item) for item in value)
            return result_set
        if _frozen(value):
            result_frozen = frozenset(self._visit(item) for item in value)
            self.memo[id(value)] = result_frozen
            return result_frozen
        raise TypeError("Unrepresentable semantic payload: " + type(value).__name__)

    def _finish_collisions(self) -> None:
        while self._heap_collisions:
            left, right = self._heap_collisions.popleft()
            if self.ops is None:
                raise ValueError("Heap collision requires domain operations")
            self._charge(1 + len(left.fields.keys() | right.fields.keys()))
            left._merge_from(right, self.ops)
            left.singleton = False


def walk_graph(
    payload: GraphPayload,
    *,
    on_value: Callable[[Value], None] | None = None,
    on_store: Callable[[Store], None] | None = None,
    charge: Callable[[int], None] | None = None,
) -> int:
    """Visit finite graph values/stores without reflection or implicit copying.

    Store callbacks operate on the caller's explicitly owned graph. Each visit
    is charged, including a repeated edge stopped by the cycle memo.
    """
    seen: set[int] = set()
    work = 0

    def visit(node: object) -> None:
        nonlocal work
        work += 1
        if charge is not None:
            charge(1)
        if not _valid_node(node):
            raise TypeError("Unrepresentable semantic payload: " + type(node).__name__)
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, Value):
            if on_value is not None:
                on_value(node)
            return
        if isinstance(node, Store) and on_store is not None:
            on_store(node)
        if isinstance(node, Metadata):
            for field in node.transformation_entries().values():
                visit(field.value)
        elif _record(node):
            for _, item in _fields(node):
                visit(item)
        elif _dictionary(node):
            for key, member in node.items():
                if not _field_key(key):
                    raise TypeError("Unregistered graph dictionary key")
                visit(key)
                visit(member)
        elif _tuple(node) or _list(node) or _set(node) or _frozen(node):
            for child in node:
                visit(child)

    visit(payload)
    return work


def _application_dictionary(value: GraphPayload) -> TypeGuard[dict[FieldKey, GraphPayload]]:
    # GraphPayload already restricts dictionary keys and values to admitted roles.
    return type(value) is dict


def _application_sequence(value: GraphPayload) -> TypeGuard[Sequence[GraphPayload]]:
    return type(value) in (tuple, list)


def _application_children(value: GraphPayload) -> tuple[GraphPayload, ...]:
    if isinstance(value, Metadata):
        return tuple(field.value for field in value.transformation_entries().values())
    if _record(value):
        return tuple(item for _, item in _fields(value))
    if _application_dictionary(value):
        return tuple(value.values())
    if _application_sequence(value):
        return tuple(value)
    return ()


def _application_mutable(value: GraphPayload) -> bool:
    return type(value) in (dict, list, set) or isinstance(
        value, (Metadata, Store, HeapObject, Unit, FunctionInfo, BoundaryState, BoundaryObject)
    )


class ApplicationTransport:
    """One detached application, with explicit active-root and footprint roles.

    Unwritten components retain the current caller's mutable owners. A written
    component intersecting inconsistent caller/poststate versions cannot prove
    an alias-closed footprint; all affected named owners receive local guards.
    Immutable records with different values are never rebound to caller records.
    """

    def __init__(self, caller: Store, poststate: Store) -> None:
        self.memo: PayloadMemo = {}
        self.result = detached_payload(caller, self.memo)
        self.poststate = poststate
        self.uncertain_owners: set[ObjectId] = set()
        self.ambiguous: set[int] = set()
        self._mismatched: set[int] = set()
        self._owners: dict[int, set[ObjectId]] = {}
        self._authority = set(self.memo)
        self._aligned: set[tuple[int, int, ObjectId]] = set()
        self._heap_bindings: list[tuple[ObjectId, HeapObject, HeapObject]] = []
        self._transform: PayloadTransformer | None = None
        self._bind(poststate, self.result)
        self._bind(poststate.cells, self.result.cells)
        self._bind(poststate.heap, self.result.heap)
        self._bind(poststate.initialized, self.result.initialized)
        self._bind(poststate.writes, self.result.writes)
        self._bind(poststate.guards, self.result.guards)

    def _bind(self, source: GraphPayload, target: GraphPayload) -> bool:
        identity = id(source)
        if identity in self.memo and self.memo[identity] is not target:
            self.ambiguous.add(identity)
            return False
        self.memo[identity] = target
        self._authority.add(identity)
        return True

    def bind_heap_owner(self, owner: ObjectId, source: HeapObject, target: HeapObject) -> None:
        self._heap_bindings.append((owner, source, target))
        self._bind(source, target)
        self._bind(source.fields, target.fields)
        self._bind(source.maybe_missing, target.maybe_missing)

    def guard_owner_alias_conflicts(
        self, metadata_writers: set[ObjectId], slot_writers: set[ObjectId]
    ) -> None:
        # Both directions matter: source sharing may be split by caller owners,
        # or two written source owners may collapse onto one caller object.
        for role in ("owner", "fields", "missing"):
            forward: dict[int, dict[int, set[ObjectId]]] = {}
            reverse: dict[int, dict[int, set[ObjectId]]] = {}
            for owner, source, target in self._heap_bindings:
                a = (
                    source
                    if role == "owner"
                    else source.fields
                    if role == "fields"
                    else source.maybe_missing
                )
                b = (
                    target
                    if role == "owner"
                    else target.fields
                    if role == "fields"
                    else target.maybe_missing
                )
                forward.setdefault(id(a), {}).setdefault(id(b), set()).add(owner)
                reverse.setdefault(id(b), {}).setdefault(id(a), set()).add(owner)
            writers = metadata_writers if role == "owner" else slot_writers
            for groups in (forward, reverse):
                for alternatives in groups.values():
                    if len(alternatives) < 2:
                        continue
                    affected: set[ObjectId] = set()
                    for owners in alternatives.values():
                        affected.update(owners)
                    if affected & writers:
                        self.uncertain_owners.update(affected)

    def align_unchanged(self, source: GraphPayload, target: GraphPayload, owner: ObjectId) -> None:
        if (
            type(source) is not type(target)
            or source is None
            or isinstance(
                source, (Value, str, bytes, int, float, complex, bool, Enum, ast.AST, ReferenceKey)
            )
        ):
            return
        identity = id(source)
        self._owners.setdefault(identity, set()).add(owner)
        pair = identity, id(target), owner
        if pair in self._aligned:
            return
        self._aligned.add(pair)
        equal = semantic_payload_equal(source, target)
        if _application_mutable(source) or equal:
            if not self._bind(source, target):
                return
            if _application_mutable(source) and not equal:
                self._mismatched.add(identity)
        if isinstance(source, Metadata) and isinstance(target, Metadata):
            first, second = source.transformation_entries(), target.transformation_entries()
            for name in first.keys() & second.keys():
                if type(first[name]) is type(second[name]):
                    self.align_unchanged(first[name].value, second[name].value, owner)
        elif _record(source) and _record(target):
            for (_, a), (_, b) in zip(_fields(source), _fields(target), strict=True):
                self.align_unchanged(a, b, owner)
        elif _application_dictionary(source) and _application_dictionary(target):
            for key in source.keys() & target.keys():
                self.align_unchanged(source[key], target[key], owner)
        elif _application_sequence(source) and _application_sequence(target):
            for a, b in zip(source, target, strict=False):
                self.align_unchanged(a, b, owner)

    def needs_residual(self, payload: GraphPayload) -> bool:
        seen: set[int] = set()

        def visit(node: GraphPayload) -> bool:
            identity = id(node)
            if identity in self.ambiguous or identity in self._mismatched:
                self.uncertain_owners.update(self._owners.get(identity, ()))
                return True
            if identity in self._authority or identity in seen:
                return False
            seen.add(identity)
            # Do not short-circuit: every affected owner must be attributed.
            outcomes = [visit(child) for child in _application_children(node)]
            return any(outcomes)

        return visit(payload)

    def captured_poststate_ambiguity(self, obj: HeapObject) -> bool:
        record = obj.metadata.record
        if not isinstance(record, GeneratorMeta):
            return False
        pending = [record]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            if current.entry is not None:
                captured = current.entry.read()
                # Shared heap alone is a valid live-heap/private-cell view.
                if captured is self.poststate or captured.cells is self.poststate.cells:
                    return True
            pending.extend(current.alternatives)
        return False

    def visit[T: GraphPayload](self, payload: T) -> T:
        # All projection/unchanged bindings precede first copying. Canonical
        # transformer dictionary anchors must include this complete authority.
        if self._transform is None:
            self._transform = PayloadTransformer(memo=self.memo)
        return self._transform.visit(payload)


def _objects(value: object) -> dict[object, object]:
    if _dictionary(value):
        return value
    raise TypeError("Expected a graph dictionary")


def _graph_map(
    value: object, seen: set[int] | None = None
) -> TypeGuard[dict[FieldKey, GraphPayload]]:
    if not _dictionary(value):
        return False
    seen = set() if seen is None else seen
    if id(value) in seen:
        return True
    seen.add(id(value))
    return all(_field_key(key) and _graph_payload(item, seen) for key, item in value.items())


def _graph_payload(value: object, seen: set[int]) -> TypeGuard[GraphPayload]:
    if not _valid_node(value):
        return False
    if (
        value is None
        or isinstance(
            value,
            (bool, int, float, complex, str, bytes, Enum, ast.AST, Value, ReferenceKey, Metadata),
        )
        or _record(value)
    ):
        return True
    if id(value) in seen:
        return True
    if _dictionary(value):
        return _graph_map(value, seen)
    seen.add(id(value))
    if _list(value) or _tuple(value) or _set(value) or _frozen(value):
        return all(_graph_payload(item, seen) for item in value)
    return False


@overload
def detached_payload(value: None, memo: PayloadMemo | None = None) -> None: ...
@overload
def detached_payload[T: GraphPayload](value: T, memo: PayloadMemo | None = None) -> T: ...
def detached_payload(value: object, memo: PayloadMemo | None = None) -> object:
    return PayloadTransformer(memo=memo)._transform(value)


@overload
def remap_payload(
    value: None,
    mapping: IdentitySubstitution | Mapping[ObjectId, ObjectId],
    ops: ValueOperations,
    *,
    epoch: int | None = None,
    memo: PayloadMemo | None = None,
) -> None: ...
@overload
def remap_payload[T: GraphPayload](
    value: T,
    mapping: IdentitySubstitution | Mapping[ObjectId, ObjectId],
    ops: ValueOperations,
    *,
    epoch: int | None = None,
    memo: PayloadMemo | None = None,
) -> T: ...
def remap_payload(
    value: object,
    mapping: IdentitySubstitution | Mapping[ObjectId, ObjectId],
    ops: ValueOperations,
    *,
    epoch: int | None = None,
    memo: PayloadMemo | None = None,
) -> object:
    return PayloadTransformer(mapping, ops, epoch=epoch, memo=memo)._transform(value)


def remap_store(
    store: Store,
    mapping: IdentitySubstitution | Mapping[ObjectId, ObjectId],
    ops: ValueOperations,
    *,
    epoch: int | None = None,
    memo: PayloadMemo | None = None,
) -> Store:
    return remap_payload(store, mapping, ops, epoch=epoch, memo=memo)


def equivalent(left: object, right: object) -> bool:
    return semantic_payload_equal(left, right)


__all__ = [
    "GraphPayload",
    "PayloadMemo",
    "PayloadTransformer",
    "detached_payload",
    "equivalent",
    "payload_signature",
    "remap_payload",
    "remap_store",
    "semantic_payload_equal",
    "walk_graph",
]
