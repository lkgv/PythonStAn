"""Typed heap metadata and a validated compatibility view for graph fixtures.

Normal runtime metadata lives in per-kind records. The mapping facade is a
migration boundary over those records, not a second dictionary of facts.
Component attachments have explicit semantic roles and a finite recursive
payload language; validation preserves admitted mutable graph identities.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Literal, TypeGuard, overload, override

from pythonstan.analysis.contracts.identity import CellId, ObjectId, ProgramPointId
from pythonstan.analysis.domains.reabstract import ReferenceKey
from pythonstan.analysis.domains.values import Value

from .bindings import BindingRef
from .continuation import (
    Continuation,
    ExceptionContext,
    ExceptionRestoreRecord,
    parse_legacy_continuation,
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
)
from .handles import ObjectHandle, StateHandle
from .keys import FieldKey

if TYPE_CHECKING:
    from .store import Flow, Frame, FunctionInfo, HeapObject, Store, Unit

type GeneratorStage = Literal["created", "suspended", "closed"]
type NamespaceKind = Literal["globals", "class_locals", "locals_snapshot", "object"]
type DictViewKind = Literal["keys", "values", "items"]
type ClosureBinding = tuple[str, CellId]
type ClosureBindings = tuple[ClosureBinding, ...] | list[ClosureBinding]
type ObjectSequence = tuple[ObjectId, ...] | list[ObjectId]
type ValueSequence = tuple[Value, ...] | list[Value]
type StatementBody = tuple[ast.stmt, ...] | list[ast.stmt]
type KeySequence = tuple[FieldKey, ...] | list[FieldKey]
type TextSequence = tuple[str, ...] | list[str]
type KeySet = frozenset[FieldKey] | set[FieldKey]
type ComponentPayload = (
    None
    | bool
    | int
    | float
    | complex
    | str
    | bytes
    | Enum
    | ast.AST
    | Value
    | ReferenceKey
    | Store
    | StateHandle
    | ObjectHandle
    | BindingRef
    | SummaryDependency
    | Frame
    | Flow
    | HeapObject
    | FunctionInfo
    | Unit
    | Continuation
    | ExceptionContext
    | ExceptionRestoreRecord
    | WriteEffect
    | Metadata
    | ObjectSequence
    | ClosureBindings
    | ValueSequence
    | StatementBody
    | KeySequence
    | TextSequence
    | KeySet
    | list[ComponentPayload]
    | tuple[ComponentPayload, ...]
    | dict[FieldKey, ComponentPayload]
    | set[ComponentPayload]
    | frozenset[ComponentPayload]
)


@dataclass(frozen=True, slots=True)
class IntegerField:
    value: int


@dataclass(frozen=True, slots=True)
class BooleanField:
    value: bool


@dataclass(frozen=True, slots=True)
class TextField:
    value: str


@dataclass(frozen=True, slots=True)
class ReferenceField:
    value: ObjectId


@dataclass(frozen=True, slots=True)
class ReferencesField:
    value: ObjectSequence


@dataclass(frozen=True, slots=True)
class BindingsField:
    value: ClosureBindings


@dataclass(frozen=True, slots=True)
class ValueField:
    value: Value


@dataclass(frozen=True, slots=True)
class FrameField:
    value: Frame


@dataclass(frozen=True, slots=True)
class StoreField:
    value: Store


@dataclass(frozen=True, slots=True)
class StateHandleField:
    value: StateHandle


@dataclass(frozen=True, slots=True)
class ContinuationField:
    value: Continuation


@dataclass(frozen=True, slots=True)
class StatementsField:
    value: StatementBody


@dataclass(frozen=True, slots=True)
class ExceptionField:
    value: ExceptionContext | None


@dataclass(frozen=True, slots=True)
class KeysField:
    value: KeySet


@dataclass(frozen=True, slots=True)
class KeySequenceField:
    value: KeySequence


@dataclass(frozen=True, slots=True)
class ValueSequenceField:
    value: ValueSequence


@dataclass(frozen=True, slots=True)
class TextSequenceField:
    value: TextSequence


@dataclass(frozen=True, slots=True)
class LiteralGraphField:
    value: ComponentPayload


type ComponentField = (
    IntegerField
    | BooleanField
    | TextField
    | ReferenceField
    | ReferencesField
    | BindingsField
    | ValueField
    | FrameField
    | StoreField
    | StateHandleField
    | ContinuationField
    | StatementsField
    | ExceptionField
    | KeysField
    | KeySequenceField
    | ValueSequenceField
    | TextSequenceField
    | LiteralGraphField
)


@dataclass
class ComponentGraph:
    """Validated fixture/adapter attachments, retaining shared mutable values."""

    entries: dict[str, ComponentField] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmptyMeta:
    """No exact normal fields for this metadata kind."""


@dataclass(frozen=True, slots=True)
class ContainerMeta:
    fresh_source_call: ProgramPointId | None = None
    length: int | None = None
    length_value: Value | None = None
    summarized_keys: KeySet | None = None


@dataclass(frozen=True, slots=True)
class FunctionMeta:
    closure: ClosureBindings | None = None
    module: str | None = None
    name: str | None = None
    qualname: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratorMeta:
    function: ObjectId | None = None
    frame: Frame | None = None
    entry: StateHandle | None = None
    body: StatementBody | None = None
    stage: GeneratorStage | None = None
    index: int | None = None
    remaining: Continuation | None = None
    resume_count: int | None = None
    owned_exception: ExceptionContext | None = None


@dataclass(frozen=True, slots=True)
class ClassMeta:
    name: str | None = None
    module: str | None = None
    qualname: str | None = None
    bases: ObjectSequence | None = None
    mro: ObjectSequence | None = None


@dataclass(frozen=True, slots=True)
class SequenceIteratorMeta:
    container: Value | None = None
    sequence: ValueSequence | None = None
    index: int | None = None
    sequence_protocol: bool | None = None


@dataclass(frozen=True, slots=True)
class DictionaryIteratorMeta:
    container: Value | None = None
    dictionary_keys: KeySequence | None = None
    dictionary_iteration: DictViewKind | None = None
    dictionary_exhausted: bool | None = None
    index: int | None = None


@dataclass(frozen=True, slots=True)
class ModuleMeta:
    module: str | None = None


@dataclass(frozen=True, slots=True)
class NamespaceMeta:
    namespace_kind: NamespaceKind | None = None
    module: str | None = None
    prefix: str | None = None
    owner: ObjectId | None = None
    readonly: bool | None = None


@dataclass(frozen=True, slots=True)
class BoundMethodMeta:
    callable: Value | None = None
    receiver: Value | None = None


@dataclass(frozen=True, slots=True)
class BuiltinMethodMeta:
    name: str | None = None
    receiver: Value | None = None


@dataclass(frozen=True, slots=True)
class DictViewMeta:
    owner: Value | None = None
    view_kind: DictViewKind | None = None


@dataclass(frozen=True, slots=True)
class ExceptionMeta:
    exception_name: str | None = None
    exception_types: TextSequence | None = None


@dataclass(frozen=True, slots=True)
class SuperMeta:
    class_value: Value | None = None
    receiver: Value | None = None


@dataclass(frozen=True, slots=True)
class SummaryMeta:
    """No exact normal fields for this metadata kind."""


type IteratorMeta = SequenceIteratorMeta | DictionaryIteratorMeta
type HeapMetadata = (
    EmptyMeta
    | ContainerMeta
    | FunctionMeta
    | GeneratorMeta
    | ClassMeta
    | SequenceIteratorMeta
    | DictionaryIteratorMeta
    | ModuleMeta
    | NamespaceMeta
    | BoundMethodMeta
    | BuiltinMethodMeta
    | DictViewMeta
    | ExceptionMeta
    | SuperMeta
    | SummaryMeta
)


def _typed[T](value: object, expected: type[T]) -> T:
    if not isinstance(value, expected) or type(value) is not expected:
        raise TypeError(f"Expected registered {expected.__name__} in heap metadata")
    return value


def _is_key(value: object) -> TypeGuard[FieldKey]:
    if (
        value is None
        or type(value) in (bool, int, float, str, bytes, ReferenceKey)
        or isinstance(value, Enum)
    ):
        return True
    if isinstance(value, tuple) and type(value) is tuple:
        items: Sequence[object] = value
        return all(_is_key(item) for item in items)
    return False


def _component(value: object, seen: set[int]) -> TypeGuard[ComponentPayload]:
    from .boundary import BoundaryObject
    from .store import Frame, FunctionInfo, HeapObject, Store, Unit, is_flow

    if (
        value is None
        or isinstance(value, (Enum, ast.AST))
        or type(value)
        in (
            bool,
            int,
            float,
            complex,
            str,
            bytes,
            Value,
            ReferenceKey,
            Frame,
            FunctionInfo,
            HeapObject,
            Store,
            Unit,
            Continuation,
            ExceptionContext,
            ExceptionRestoreRecord,
            Metadata,
            StateHandle,
            ObjectHandle,
            BindingRef,
            SummaryDependency,
            BoundaryObject,
            CellWrite,
            HeapWrite,
            HeapDelete,
            HeapDefaultWrite,
            MetadataUpdate,
            Havoc,
        )
        or is_flow(value)
    ):
        return True
    identity = id(value)
    if identity in seen:
        return True
    seen.add(identity)
    if isinstance(value, dict) and type(value) is dict:
        mapping: Mapping[object, object] = value
        return all(_is_key(key) and _component(item, seen) for key, item in mapping.items())
    if type(value) in (list, tuple, set, frozenset):
        if not isinstance(value, (list, tuple, set, frozenset)):
            return False
        items: Sequence[object] | set[object] | frozenset[object] = value
        return all(_component(item, seen) for item in items)
    return False


def validate_component(value: object) -> ComponentPayload:
    """Validate without copying, so an existing shared-list/root alias survives."""
    if not _component(value, set()):
        raise TypeError("Unregistered heap metadata graph payload")
    return value


def _objects(value: object) -> TypeGuard[ObjectSequence]:
    if not isinstance(value, (tuple, list)) or type(value) not in (tuple, list):
        return False
    items: Sequence[object] = value
    return all(type(item) is str for item in items)


def _bindings(value: object) -> TypeGuard[ClosureBindings]:
    if not isinstance(value, (tuple, list)) or type(value) not in (tuple, list):
        return False
    items: Sequence[object] = value
    for item in items:
        if not isinstance(item, tuple) or type(item) is not tuple or len(item) != 2:
            return False
        pair: tuple[object, ...] = item
        if type(pair[0]) is not str or type(pair[1]) is not str:
            return False
    return True


def _values(value: object) -> TypeGuard[ValueSequence]:
    if not isinstance(value, (tuple, list)) or type(value) not in (tuple, list):
        return False
    items: Sequence[object] = value
    return all(type(item) is Value for item in items)


def _statements(value: object) -> TypeGuard[StatementBody]:
    if not isinstance(value, (tuple, list)) or type(value) not in (tuple, list):
        return False
    items: Sequence[object] = value
    return all(isinstance(item, ast.stmt) for item in items)


def _keys(value: object) -> TypeGuard[KeySequence]:
    if not isinstance(value, (tuple, list)) or type(value) not in (tuple, list):
        return False
    items: Sequence[object] = value
    return all(_is_key(item) for item in items)


def _key_set(value: object) -> TypeGuard[KeySet]:
    if not isinstance(value, (set, frozenset)) or type(value) not in (set, frozenset):
        return False
    items: set[object] | frozenset[object] = value
    return all(_is_key(item) for item in items)


def _texts(value: object) -> TypeGuard[TextSequence]:
    if not isinstance(value, (tuple, list)) or type(value) not in (tuple, list):
        return False
    items: Sequence[object] = value
    return all(type(item) is str for item in items)


def _stage(value: str) -> GeneratorStage:
    match value:
        case "created" | "suspended" | "closed":
            return value
        case _:
            raise ValueError("Unknown generator metadata stage")


def _namespace(value: str) -> NamespaceKind:
    match value:
        case "globals" | "class_locals" | "locals_snapshot" | "object":
            return value
        case _:
            raise ValueError("Unknown namespace metadata kind")


def _view(value: str) -> DictViewKind:
    match value:
        case "keys" | "values" | "items":
            return value
        case _:
            raise ValueError("Unknown dictionary view kind")


def _field(kind: str, key: str, value: object) -> ComponentField:
    from .store import Frame, Store

    if key in {"length", "index", "resume_count"}:
        if type(value) is not int:
            raise TypeError("Heap metadata cursor/length must be an integer")
        return IntegerField(value)
    if key in {"readonly", "sequence_protocol", "dictionary_exhausted"}:
        return BooleanField(_typed(value, bool))
    if key in {"module", "prefix", "name", "qualname", "exception_name", "fresh_source_call"}:
        return TextField(_typed(value, str))
    if key == "stage":
        return TextField(_stage(_typed(value, str)))
    if key == "namespace_kind":
        return TextField(_namespace(_typed(value, str)))
    if key in {"view_kind", "dictionary_iteration"}:
        return TextField(_view(_typed(value, str)))
    if key == "function":
        return ReferenceField(ObjectId(_typed(value, str)))
    if key == "owner":
        if kind == "dict_view" or isinstance(value, Value):
            return ValueField(_typed(value, Value))
        return ReferenceField(ObjectId(_typed(value, str)))
    if key in {"bases", "mro"} and _objects(value):
        return ReferencesField(value)
    if key == "closure" and _bindings(value):
        return BindingsField(value)
    if key in {"callable", "receiver", "class", "container", "length_value"}:
        return ValueField(_typed(value, Value))
    if key == "frame":
        return FrameField(_typed(value, Frame))
    if key == "entry":
        entry = (
            _typed(value, StateHandle)
            if isinstance(value, StateHandle)
            else StateHandle.capture(_typed(value, Store))
        )
        return StateHandleField(entry)
    if key == "remaining":
        return ContinuationField(parse_legacy_continuation(value))
    if key == "body" and _statements(value):
        return StatementsField(value)
    if key == "owned_exception":
        return ExceptionField(None if value is None else _typed(value, ExceptionContext))
    if key == "dictionary_keys" and _keys(value):
        return KeySequenceField(value)
    if key == "summarized_keys" and _key_set(value):
        return KeysField(value)
    if key == "sequence" and _values(value):
        return ValueSequenceField(value)
    if key == "exception_types" and _texts(value):
        return TextSequenceField(value)
    if key in {
        "bases",
        "mro",
        "closure",
        "body",
        "dictionary_keys",
        "summarized_keys",
        "sequence",
        "exception_types",
    }:
        raise TypeError(f"Malformed typed heap metadata field: {key}")
    return LiteralGraphField(validate_component(value))


def _optional[T](entries: Mapping[str, ComponentField], key: str, expected: type[T]) -> T | None:
    item = entries.get(key)
    if item is None:
        return None
    return _typed(item, expected)


def _decode_record(kind: str, entries: Mapping[str, ComponentField]) -> HeapMetadata:
    if kind == "iterator" and "dictionary_iteration" in entries:
        return _dictionary_record(entries)
    if kind in {"dict", "tuple", "list", "frozenset", "set"}:
        fresh_source_call_field = _optional(entries, "fresh_source_call", TextField)
        length_field = _optional(entries, "length", IntegerField)
        length_value_field = _optional(entries, "length_value", ValueField)
        summarized_keys_field = _optional(entries, "summarized_keys", KeysField)
        return ContainerMeta(
            fresh_source_call=None
            if fresh_source_call_field is None
            else ProgramPointId(fresh_source_call_field.value),
            length=None if length_field is None else length_field.value,
            length_value=None if length_value_field is None else length_value_field.value,
            summarized_keys=None if summarized_keys_field is None else summarized_keys_field.value,
        )
    if kind in {"function"}:
        closure_field = _optional(entries, "closure", BindingsField)
        module_field = _optional(entries, "module", TextField)
        name_field = _optional(entries, "name", TextField)
        qualname_field = _optional(entries, "qualname", TextField)
        return FunctionMeta(
            closure=None if closure_field is None else closure_field.value,
            module=None if module_field is None else module_field.value,
            name=None if name_field is None else name_field.value,
            qualname=None if qualname_field is None else qualname_field.value,
        )
    if kind in {"generator", "async_generator", "coroutine"}:
        function_field = _optional(entries, "function", ReferenceField)
        frame_field = _optional(entries, "frame", FrameField)
        entry_field = _optional(entries, "entry", StateHandleField)
        body_field = _optional(entries, "body", StatementsField)
        stage_field = _optional(entries, "stage", TextField)
        index_field = _optional(entries, "index", IntegerField)
        remaining_field = _optional(entries, "remaining", ContinuationField)
        resume_count_field = _optional(entries, "resume_count", IntegerField)
        owned_exception_field = _optional(entries, "owned_exception", ExceptionField)
        return GeneratorMeta(
            function=None if function_field is None else function_field.value,
            frame=None if frame_field is None else frame_field.value,
            entry=None if entry_field is None else entry_field.value,
            body=None if body_field is None else body_field.value,
            stage=None if stage_field is None else _stage(stage_field.value),
            index=None if index_field is None else index_field.value,
            remaining=None if remaining_field is None else remaining_field.value,
            resume_count=None if resume_count_field is None else resume_count_field.value,
            owned_exception=None if owned_exception_field is None else owned_exception_field.value,
        )
    if kind in {"class"}:
        name_field = _optional(entries, "name", TextField)
        module_field = _optional(entries, "module", TextField)
        qualname_field = _optional(entries, "qualname", TextField)
        bases_field = _optional(entries, "bases", ReferencesField)
        mro_field = _optional(entries, "mro", ReferencesField)
        return ClassMeta(
            name=None if name_field is None else name_field.value,
            module=None if module_field is None else module_field.value,
            qualname=None if qualname_field is None else qualname_field.value,
            bases=None if bases_field is None else bases_field.value,
            mro=None if mro_field is None else mro_field.value,
        )
    if kind in {"iterator"}:
        container_field = _optional(entries, "container", ValueField)
        sequence_field = _optional(entries, "sequence", ValueSequenceField)
        index_field = _optional(entries, "index", IntegerField)
        sequence_protocol_field = _optional(entries, "sequence_protocol", BooleanField)
        return SequenceIteratorMeta(
            container=None if container_field is None else container_field.value,
            sequence=None if sequence_field is None else sequence_field.value,
            index=None if index_field is None else index_field.value,
            sequence_protocol=None
            if sequence_protocol_field is None
            else sequence_protocol_field.value,
        )
    if kind in {"module"}:
        module_field = _optional(entries, "module", TextField)
        return ModuleMeta(module=None if module_field is None else module_field.value)
    if kind in {"namespace"}:
        namespace_kind_field = _optional(entries, "namespace_kind", TextField)
        module_field = _optional(entries, "module", TextField)
        prefix_field = _optional(entries, "prefix", TextField)
        owner_field = _optional(entries, "owner", ReferenceField)
        readonly_field = _optional(entries, "readonly", BooleanField)
        return NamespaceMeta(
            namespace_kind=None
            if namespace_kind_field is None
            else _namespace(namespace_kind_field.value),
            module=None if module_field is None else module_field.value,
            prefix=None if prefix_field is None else prefix_field.value,
            owner=None if owner_field is None else owner_field.value,
            readonly=None if readonly_field is None else readonly_field.value,
        )
    if kind in {"bound_method"}:
        callable_field = _optional(entries, "callable", ValueField)
        receiver_field = _optional(entries, "receiver", ValueField)
        return BoundMethodMeta(
            callable=None if callable_field is None else callable_field.value,
            receiver=None if receiver_field is None else receiver_field.value,
        )
    if kind in {"builtin_method"}:
        name_field = _optional(entries, "name", TextField)
        receiver_field = _optional(entries, "receiver", ValueField)
        return BuiltinMethodMeta(
            name=None if name_field is None else name_field.value,
            receiver=None if receiver_field is None else receiver_field.value,
        )
    if kind in {"dict_view"}:
        view_owner_field = _optional(entries, "owner", ValueField)
        view_kind_field = _optional(entries, "view_kind", TextField)
        return DictViewMeta(
            owner=None if view_owner_field is None else view_owner_field.value,
            view_kind=None if view_kind_field is None else _view(view_kind_field.value),
        )
    if kind == "exception" or (
        kind == "instance" and ({"exception_name", "exception_types"} & entries.keys())
    ):
        exception_name_field = _optional(entries, "exception_name", TextField)
        exception_types_field = _optional(entries, "exception_types", TextSequenceField)
        return ExceptionMeta(
            exception_name=None if exception_name_field is None else exception_name_field.value,
            exception_types=None if exception_types_field is None else exception_types_field.value,
        )
    if kind in {"super"}:
        class_value_field = _optional(entries, "class", ValueField)
        receiver_field = _optional(entries, "receiver", ValueField)
        return SuperMeta(
            class_value=None if class_value_field is None else class_value_field.value,
            receiver=None if receiver_field is None else receiver_field.value,
        )
    if kind in {"summary"}:
        return SummaryMeta()
    return EmptyMeta()


def _dictionary_record(entries: Mapping[str, ComponentField]) -> DictionaryIteratorMeta:
    container_field = _optional(entries, "container", ValueField)
    dictionary_keys_field = _optional(entries, "dictionary_keys", KeySequenceField)
    dictionary_iteration_field = _optional(entries, "dictionary_iteration", TextField)
    dictionary_exhausted_field = _optional(entries, "dictionary_exhausted", BooleanField)
    index_field = _optional(entries, "index", IntegerField)
    return DictionaryIteratorMeta(
        container=None if container_field is None else container_field.value,
        dictionary_keys=None if dictionary_keys_field is None else dictionary_keys_field.value,
        dictionary_iteration=None
        if dictionary_iteration_field is None
        else _view(dictionary_iteration_field.value),
        dictionary_exhausted=None
        if dictionary_exhausted_field is None
        else dictionary_exhausted_field.value,
        index=None if index_field is None else index_field.value,
    )


def _record_fields(record: HeapMetadata) -> dict[str, ComponentField]:
    if type(record) not in (
        EmptyMeta,
        ContainerMeta,
        FunctionMeta,
        GeneratorMeta,
        ClassMeta,
        SequenceIteratorMeta,
        DictionaryIteratorMeta,
        ModuleMeta,
        NamespaceMeta,
        BoundMethodMeta,
        BuiltinMethodMeta,
        DictViewMeta,
        ExceptionMeta,
        SuperMeta,
        SummaryMeta,
    ):
        raise TypeError("Unregistered per-kind metadata record")
    result: dict[str, ComponentField]
    if isinstance(record, ContainerMeta):
        result = {}
        if record.fresh_source_call is not None:
            result["fresh_source_call"] = TextField(record.fresh_source_call)
        if record.length is not None:
            result["length"] = IntegerField(record.length)
        if record.length_value is not None:
            result["length_value"] = ValueField(record.length_value)
        if record.summarized_keys is not None:
            result["summarized_keys"] = KeysField(record.summarized_keys)
        return result
    if isinstance(record, FunctionMeta):
        result = {}
        if record.closure is not None:
            result["closure"] = BindingsField(record.closure)
        if record.module is not None:
            result["module"] = TextField(record.module)
        if record.name is not None:
            result["name"] = TextField(record.name)
        if record.qualname is not None:
            result["qualname"] = TextField(record.qualname)
        return result
    if isinstance(record, GeneratorMeta):
        result = {}
        if record.function is not None:
            result["function"] = ReferenceField(record.function)
        if record.frame is not None:
            result["frame"] = FrameField(record.frame)
        if record.entry is not None:
            result["entry"] = StateHandleField(_typed(record.entry, StateHandle))
        if record.body is not None:
            result["body"] = StatementsField(record.body)
        if record.stage is not None:
            result["stage"] = TextField(record.stage)
        if record.index is not None:
            result["index"] = IntegerField(record.index)
        if record.remaining is not None:
            result["remaining"] = ContinuationField(record.remaining)
        if record.resume_count is not None:
            result["resume_count"] = IntegerField(record.resume_count)
        result["owned_exception"] = ExceptionField(record.owned_exception)
        return result
    if isinstance(record, ClassMeta):
        result = {}
        if record.name is not None:
            result["name"] = TextField(record.name)
        if record.module is not None:
            result["module"] = TextField(record.module)
        if record.qualname is not None:
            result["qualname"] = TextField(record.qualname)
        if record.bases is not None:
            result["bases"] = ReferencesField(record.bases)
        if record.mro is not None:
            result["mro"] = ReferencesField(record.mro)
        return result
    if isinstance(record, SequenceIteratorMeta):
        result = {}
        if record.container is not None:
            result["container"] = ValueField(record.container)
        if record.sequence is not None:
            result["sequence"] = ValueSequenceField(record.sequence)
        if record.index is not None:
            result["index"] = IntegerField(record.index)
        if record.sequence_protocol is not None:
            result["sequence_protocol"] = BooleanField(record.sequence_protocol)
        return result
    if isinstance(record, DictionaryIteratorMeta):
        result = {}
        if record.container is not None:
            result["container"] = ValueField(record.container)
        if record.dictionary_keys is not None:
            result["dictionary_keys"] = KeySequenceField(record.dictionary_keys)
        if record.dictionary_iteration is not None:
            result["dictionary_iteration"] = TextField(record.dictionary_iteration)
        if record.dictionary_exhausted is not None:
            result["dictionary_exhausted"] = BooleanField(record.dictionary_exhausted)
        if record.index is not None:
            result["index"] = IntegerField(record.index)
        return result
    if isinstance(record, ModuleMeta):
        result = {}
        if record.module is not None:
            result["module"] = TextField(record.module)
        return result
    if isinstance(record, NamespaceMeta):
        result = {}
        if record.namespace_kind is not None:
            result["namespace_kind"] = TextField(record.namespace_kind)
        if record.module is not None:
            result["module"] = TextField(record.module)
        if record.prefix is not None:
            result["prefix"] = TextField(record.prefix)
        if record.owner is not None:
            result["owner"] = ReferenceField(record.owner)
        if record.readonly is not None:
            result["readonly"] = BooleanField(record.readonly)
        return result
    if isinstance(record, BoundMethodMeta):
        result = {}
        if record.callable is not None:
            result["callable"] = ValueField(record.callable)
        if record.receiver is not None:
            result["receiver"] = ValueField(record.receiver)
        return result
    if isinstance(record, BuiltinMethodMeta):
        result = {}
        if record.name is not None:
            result["name"] = TextField(record.name)
        if record.receiver is not None:
            result["receiver"] = ValueField(record.receiver)
        return result
    if isinstance(record, DictViewMeta):
        result = {}
        if record.owner is not None:
            result["owner"] = ValueField(record.owner)
        if record.view_kind is not None:
            result["view_kind"] = TextField(record.view_kind)
        return result
    if isinstance(record, ExceptionMeta):
        result = {}
        if record.exception_name is not None:
            result["exception_name"] = TextField(record.exception_name)
        if record.exception_types is not None:
            result["exception_types"] = TextSequenceField(record.exception_types)
        return result
    if isinstance(record, SuperMeta):
        result = {}
        if record.class_value is not None:
            result["class"] = ValueField(record.class_value)
        if record.receiver is not None:
            result["receiver"] = ValueField(record.receiver)
        return result
    return {}


_KNOWN_KEYS = frozenset(
    {
        "bases",
        "body",
        "callable",
        "class",
        "closure",
        "container",
        "dictionary_exhausted",
        "dictionary_iteration",
        "dictionary_keys",
        "entry",
        "exception_name",
        "exception_types",
        "frame",
        "function",
        "fresh_source_call",
        "index",
        "length",
        "length_value",
        "module",
        "mro",
        "name",
        "namespace_kind",
        "owned_exception",
        "owner",
        "prefix",
        "qualname",
        "readonly",
        "receiver",
        "remaining",
        "resume_count",
        "sequence",
        "sequence_protocol",
        "stage",
        "summarized_keys",
        "view_kind",
    }
)


class Metadata(MutableMapping[str, ComponentPayload]):
    """One mutable owner of a typed record and explicitly admitted attachments."""

    kind: str
    record: HeapMetadata
    components: ComponentGraph
    _order: list[str]
    _allow_components: bool

    def __init__(
        self,
        kind: str = "object",
        data: Mapping[str, object] | None = None,
        *,
        allow_components: bool = False,
    ) -> None:
        self.kind = kind
        self.record = EmptyMeta()
        self.components = ComponentGraph()
        self._order = []
        self._allow_components = allow_components
        if data is not None:
            entries: dict[str, ComponentField] = {}
            for key, value in data.items():
                if not isinstance(key, str):
                    raise TypeError("Heap metadata field names must be strings")
                if key not in _KNOWN_KEYS and not allow_components:
                    raise KeyError(
                        "Graph attachment requires an explicit component metadata adapter"
                    )
                entries[key] = _field(kind, key, value)
            self.replace_entries(entries)

    @classmethod
    def from_legacy(cls, kind: str, data: Metadata | Mapping[str, object] | None) -> Metadata:
        if isinstance(data, Metadata):
            if type(data) is not Metadata:
                raise TypeError("Unregistered metadata facade subclass")
            if data.kind == kind:
                return data
            result = cls(kind, allow_components=data._allow_components)
            result.replace_entries(data.transformation_entries())
            return result
        return cls(kind, data, allow_components=True)

    @classmethod
    def from_component_graph(cls, kind: str, data: Mapping[str, object]) -> Metadata:
        """Explicit fixture/adapter entry, not an unrestricted production writer."""
        return cls(kind, data, allow_components=True)

    @classmethod
    def from_record(cls, kind: str, record: HeapMetadata) -> Metadata:
        """Normal producers publish their real typed record without a raw DTO."""
        entries = _record_fields(record)
        for key, entry in entries.items():
            decoded = _field(kind, key, entry.value)
            if type(decoded) is not type(entry):
                raise TypeError("Typed metadata field has an incompatible semantic role")
        if isinstance(record, GeneratorMeta) and record.owned_exception is None:
            entries.pop("owned_exception", None)
        if type(_decode_record(kind, entries)) is not type(record):
            raise ValueError("Heap kind and typed metadata record disagree")
        result = cls(kind)
        result.record = record
        result._order = list(entries)
        return result

    def new_shell(self) -> Metadata:
        return Metadata(self.kind, allow_components=self._allow_components)

    def reclassify(self, kind: str) -> None:
        """Reclassify an owned detached facade while preserving graph aliases."""
        if kind == self.kind:
            return
        prepared = Metadata.from_legacy(kind, self)
        self.kind = prepared.kind
        self.record = prepared.record
        self.components = prepared.components
        self._order = prepared._order

    def attach_graph(self, key: str, value: object) -> None:
        if key in _KNOWN_KEYS:
            raise ValueError("A semantic metadata field cannot be a literal graph attachment")
        entry = LiteralGraphField(validate_component(value))
        entries = self.transformation_entries()
        entries[key] = entry
        self.replace_entries(entries)
        self._allow_components = True

    def transformation_entries(self) -> dict[str, ComponentField]:
        normal = _record_fields(self.record)
        return {
            key: normal[key] if key in normal else self.components.entries[key]
            for key in self._order
        }

    def replace_entries(self, entries: Mapping[str, ComponentField]) -> None:
        record = _decode_record(self.kind, entries)
        normal = _record_fields(record)
        self.record = record
        self.components = ComponentGraph(
            {key: value for key, value in entries.items() if key not in normal}
        )
        self._order = list(entries)

    @overload
    def __getitem__(self, key: Literal["fresh_source_call"]) -> ProgramPointId: ...
    @overload
    def __getitem__(self, key: Literal["length", "index", "resume_count"]) -> int: ...
    @overload
    def __getitem__(
        self, key: Literal["readonly", "sequence_protocol", "dictionary_exhausted"]
    ) -> bool: ...
    @overload
    def __getitem__(
        self, key: Literal["module", "prefix", "name", "qualname", "exception_name"]
    ) -> str: ...
    @overload
    def __getitem__(self, key: Literal["stage"]) -> GeneratorStage: ...
    @overload
    def __getitem__(self, key: Literal["namespace_kind"]) -> NamespaceKind: ...
    @overload
    def __getitem__(self, key: Literal["view_kind", "dictionary_iteration"]) -> DictViewKind: ...
    @overload
    def __getitem__(self, key: Literal["function"]) -> ObjectId: ...
    @overload
    def __getitem__(self, key: Literal["bases", "mro"]) -> ObjectSequence: ...
    @overload
    def __getitem__(self, key: Literal["closure"]) -> ClosureBindings: ...
    @overload
    def __getitem__(
        self, key: Literal["callable", "receiver", "class", "container", "length_value"]
    ) -> Value: ...
    @overload
    def __getitem__(self, key: Literal["frame"]) -> Frame: ...
    @overload
    def __getitem__(self, key: Literal["entry"]) -> Store: ...
    @overload
    def __getitem__(self, key: Literal["remaining"]) -> Continuation: ...
    @overload
    def __getitem__(self, key: Literal["body"]) -> StatementBody: ...
    @overload
    def __getitem__(self, key: Literal["owned_exception"]) -> ExceptionContext | None: ...
    @overload
    def __getitem__(self, key: Literal["dictionary_keys"]) -> KeySequence: ...
    @overload
    def __getitem__(self, key: Literal["summarized_keys"]) -> KeySet: ...
    @overload
    def __getitem__(self, key: Literal["sequence"]) -> ValueSequence: ...
    @overload
    def __getitem__(self, key: Literal["exception_types"]) -> TextSequence: ...
    @overload
    def __getitem__(self, key: str) -> ComponentPayload: ...
    @override
    def __getitem__(self, key: str) -> ComponentPayload:
        if key not in self._order:
            raise KeyError(key)
        normal = _record_fields(self.record)
        item = normal.get(key)
        if item is None:
            item = self.components.entries[key]
        if key == "entry" and isinstance(item, StateHandleField):
            # Documented compatibility projection; the stored fact is one handle.
            return item.value.read()
        return item.value

    def entry_handle(self) -> StateHandle:
        """Return the stored checkpoint without capturing on read."""
        entry = self.transformation_entries().get("entry")
        if not isinstance(entry, StateHandleField):
            raise KeyError("Generator metadata has no canonical entry handle")
        return entry.value

    @overload
    def lookup(
        self, key: Literal["fresh_source_call"], default: None = None
    ) -> ProgramPointId | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["fresh_source_call"], default: T
    ) -> ProgramPointId | T: ...
    @overload
    def lookup(
        self, key: Literal["length", "index", "resume_count"], default: None = None
    ) -> int | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["length", "index", "resume_count"], default: T
    ) -> int | T: ...
    @overload
    def lookup(
        self,
        key: Literal["readonly", "sequence_protocol", "dictionary_exhausted"],
        default: None = None,
    ) -> bool | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["readonly", "sequence_protocol", "dictionary_exhausted"], default: T
    ) -> bool | T: ...
    @overload
    def lookup(
        self,
        key: Literal["module", "prefix", "name", "qualname", "exception_name"],
        default: None = None,
    ) -> str | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["module", "prefix", "name", "qualname", "exception_name"], default: T
    ) -> str | T: ...
    @overload
    def lookup(self, key: Literal["stage"], default: None = None) -> GeneratorStage | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["stage"], default: T
    ) -> GeneratorStage | T: ...
    @overload
    def lookup(
        self, key: Literal["namespace_kind"], default: None = None
    ) -> NamespaceKind | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["namespace_kind"], default: T
    ) -> NamespaceKind | T: ...
    @overload
    def lookup(
        self, key: Literal["view_kind", "dictionary_iteration"], default: None = None
    ) -> DictViewKind | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["view_kind", "dictionary_iteration"], default: T
    ) -> DictViewKind | T: ...
    @overload
    def lookup(self, key: Literal["function"], default: None = None) -> ObjectId | None: ...
    @overload
    def lookup[T: ComponentPayload](self, key: Literal["function"], default: T) -> ObjectId | T: ...
    @overload
    def lookup(
        self, key: Literal["bases", "mro"], default: None = None
    ) -> ObjectSequence | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["bases", "mro"], default: T
    ) -> ObjectSequence | T: ...
    @overload
    def lookup(self, key: Literal["closure"], default: None = None) -> ClosureBindings | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["closure"], default: T
    ) -> ClosureBindings | T: ...
    @overload
    def lookup(
        self,
        key: Literal["callable", "receiver", "class", "container", "length_value"],
        default: None = None,
    ) -> Value | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["callable", "receiver", "class", "container", "length_value"], default: T
    ) -> Value | T: ...
    @overload
    def lookup(self, key: Literal["frame"], default: None = None) -> Frame | None: ...
    @overload
    def lookup[T: ComponentPayload](self, key: Literal["frame"], default: T) -> Frame | T: ...
    @overload
    def lookup(self, key: Literal["entry"], default: None = None) -> Store | None: ...
    @overload
    def lookup[T: ComponentPayload](self, key: Literal["entry"], default: T) -> Store | T: ...
    @overload
    def lookup(self, key: Literal["remaining"], default: None = None) -> Continuation | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["remaining"], default: T
    ) -> Continuation | T: ...
    @overload
    def lookup(self, key: Literal["body"], default: None = None) -> StatementBody | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["body"], default: T
    ) -> StatementBody | T: ...
    @overload
    def lookup(
        self, key: Literal["owned_exception"], default: None = None
    ) -> ExceptionContext | None | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["owned_exception"], default: T
    ) -> ExceptionContext | None | T: ...
    @overload
    def lookup(
        self, key: Literal["dictionary_keys"], default: None = None
    ) -> KeySequence | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["dictionary_keys"], default: T
    ) -> KeySequence | T: ...
    @overload
    def lookup(self, key: Literal["summarized_keys"], default: None = None) -> KeySet | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["summarized_keys"], default: T
    ) -> KeySet | T: ...
    @overload
    def lookup(self, key: Literal["sequence"], default: None = None) -> ValueSequence | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["sequence"], default: T
    ) -> ValueSequence | T: ...
    @overload
    def lookup(
        self, key: Literal["exception_types"], default: None = None
    ) -> TextSequence | None: ...
    @overload
    def lookup[T: ComponentPayload](
        self, key: Literal["exception_types"], default: T
    ) -> TextSequence | T: ...
    @overload
    def lookup(self, key: str, default: None = None) -> ComponentPayload | None: ...
    @overload
    def lookup(self, key: str, default: ComponentPayload) -> ComponentPayload: ...

    @overload
    def lookup[T](self, key: str, default: T) -> ComponentPayload | T: ...
    def lookup[T](self, key: str, default: T | None = None) -> ComponentPayload | T | None:
        try:
            return self[key]
        except KeyError:
            return default

    @override
    def __setitem__(self, key: str, value: ComponentPayload) -> None:
        if key not in _KNOWN_KEYS and not self._allow_components:
            raise KeyError("Graph attachment requires an explicit component metadata adapter")
        entries = self.transformation_entries()
        entries[key] = _field(self.kind, key, value)
        self.replace_entries(entries)

    @override
    def __delitem__(self, key: str) -> None:
        entries = self.transformation_entries()
        del entries[key]
        self.replace_entries(entries)

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(tuple(self._order))

    @override
    def __len__(self) -> int:
        return len(self._order)

    def namespace_owner(self) -> ObjectId:
        return ObjectId(_typed(self["owner"], str))

    def view_owner(self) -> Value:
        return _typed(self["owner"], Value)

    def copy(self) -> Metadata:
        result = self.new_shell()
        result.replace_entries(self.transformation_entries())
        return result
