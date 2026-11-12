"""Context abstractions for context-sensitive pointer analysis.

This module defines context representations for different context sensitivity
policies including call-string, object, type, receiver, and hybrid contexts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Literal,
    Never,
    Protocol,
    TypeGuard,
    TypeVar,
    overload,
    override,
    runtime_checkable,
)

from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject,
    AllocSite,
)
from pythonstan.analysis.pointer.kcfa.object import (
    ClassObject as ClassObject,
)
from pythonstan.analysis.pointer.kcfa.object import (
    FunctionObject as FunctionObject,
)
from pythonstan.analysis.pointer.kcfa.object import (
    ModuleObject as ModuleObject,
)
from pythonstan.ir.ir_statements import IRModule, IRScope, IRStatement

if TYPE_CHECKING:
    from .protocol_context import ProtocolKeyContext

__all__ = [
    "CallSite",
    "Ctx",
    "Scope",
    "AbstractContext",
    "NativeContext",
    "is_native_context",
    "CallStringContext",
    "ObjectContext",
    "TypeContext",
    "ReceiverContext",
    "ParamContext",
    "HybridContext",
    "SummaryContext",
    "FiniteContextAtom",
    "SourceIdentity",
    "FiniteContextKey",
    "BindingAtom",
    "OrderedBindingToken",
    "finite_context_atom",
    "ordered_binding_token",
]


@dataclass(frozen=True)
class CallSite:
    """Call site identifier bound to an IR statement."""

    statement: IRStatement
    scope_name: str | None = None
    index: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.statement, IRStatement):
            raise ValueError(f"CallSite.statement must be IRStatement, got {type(self.statement)}")

    @property
    def site_id(self) -> str:
        scope = self.scope_name or "<unknown>"
        line, col = self._location()
        stmt_str = str(self.statement)
        if line is None or col is None:
            return f"{scope}:{stmt_str}#{self.index}"
        return f"{scope}:{line}:{col}:{stmt_str}#{self.index}"

    def short_id(self) -> str:
        line, col = self._location()
        if line is None or col is None:
            return f"{id(self.statement)}:{self.index}"
        return f"{line}:{col}:{self.index}"

    def _location(self) -> tuple[int | None, int | None]:
        decoded = _decode_native_metadata(self.statement.get_ast(), "location")
        if not isinstance(decoded, _MetadataLocation):
            raise TypeError("Malformed source location metadata")
        return decoded.line, decoded.column

    @override
    def __str__(self) -> str:
        return self.site_id


type SourceIdentity = (
    str
    | int
    | None
    | Enum
    | IRStatement
    | CallSite
    | AllocSite
    | tuple[SourceIdentity, ...]
    | frozenset[SourceIdentity]
)
type FiniteContextKey = (
    SourceIdentity
    | FiniteContextAtom
    | OrderedBindingToken
    | tuple[FiniteContextKey, ...]
    | frozenset[FiniteContextKey]
)
type BindingAtom = FiniteContextAtom | CallSite | str | int | None

type CallContextSite = CallSite | IRStatement
type ContextSeed = AbstractObject | FiniteContextKey
type ParameterValue = AbstractObject | Ctx[object] | FiniteContextKey
type ParameterContextInput = (
    CallSite | OrderedBindingToken | tuple[ParameterValue, ...] | frozenset[ParameterValue]
)


@runtime_checkable
class _AllocationCarrier(Protocol):
    @property
    def alloc_site(self) -> object: ...


@runtime_checkable
class _ContextCarrier(Protocol):
    @property
    def context(self) -> object: ...


@runtime_checkable
class _NamedSource(Protocol):
    @property
    def name(self) -> object: ...
    @property
    def kind(self) -> object: ...


@runtime_checkable
class _IndexedSource(Protocol):
    @property
    def index(self) -> object: ...


@runtime_checkable
class _TypedKeySource(Protocol):
    @property
    def key_type(self) -> object: ...


@runtime_checkable
class _WrappedContext(Protocol):
    @property
    def inner(self) -> object: ...


@runtime_checkable
class _CallOrigins(Protocol):
    @property
    def call_sites(self) -> object: ...


@runtime_checkable
class _AllocationOrigins(Protocol):
    @property
    def alloc_sites(self) -> object: ...


@runtime_checkable
class _TypeOrigins(Protocol):
    @property
    def types(self) -> object: ...


@runtime_checkable
class _ReceiverOrigins(Protocol):
    @property
    def receivers(self) -> object: ...


@runtime_checkable
class _ParameterOrigins(Protocol):
    @property
    def params(self) -> object: ...


@runtime_checkable
class _FieldCarrier(Protocol):
    @property
    def obj(self) -> object: ...
    @property
    def field(self) -> object: ...


@runtime_checkable
class _MetadataItems(Protocol):
    def items(self) -> object: ...


@runtime_checkable
class _MetadataReader(Protocol):
    def __call__(self) -> object: ...


@runtime_checkable
class _MetadataSequence(Protocol):
    def __getitem__(self, index: int) -> object: ...


@dataclass(frozen=True)
class _MetadataMissing:
    """No recognized metadata at the compatibility boundary."""


@dataclass(frozen=True)
class _MetadataLocation:
    line: int | None
    column: int | None


@dataclass(frozen=True)
class _MetadataCallHistory:
    values: tuple[CallSite, ...]


@dataclass(frozen=True)
class _MetadataOrigins:
    values: tuple[SourceIdentity, ...]


_METADATA_MISSING = _MetadataMissing()
type _MetadataDecoded = (
    SourceIdentity
    | FiniteContextAtom
    | OrderedBindingToken
    | _MetadataOrigins
    | _MetadataLocation
    | _MetadataCallHistory
    | _MetadataMissing
)


def _context_record(value: object) -> TypeGuard[Ctx[object]]:
    return isinstance(value, _CTX_RUNTIME)


def _valid_source_identity(value: object, depth: int = 8) -> TypeGuard[SourceIdentity]:
    """Validate finite projected data without traversing source IR internals."""
    if isinstance(value, (str, int, type(None), Enum, IRStatement)):
        return True
    if isinstance(value, CallSite):
        return isinstance(value.statement, IRStatement)
    if isinstance(value, AllocSite):
        return isinstance(value.stmt, (str, IRStatement))
    if depth > 0 and isinstance(value, (tuple, frozenset)):
        items: tuple[object, ...] | frozenset[object] = value
        return all(_valid_source_identity(item, depth - 1) for item in items)
    return False


def _source_payload(value: object) -> SourceIdentity:
    if _valid_source_identity(value):
        return value
    raise ValueError("FiniteContextAtom requires bounded flat source identities")


def _binding_atom(value: object) -> TypeGuard[BindingAtom]:
    return isinstance(value, (FiniteContextAtom, CallSite, str, int, type(None)))


@dataclass(frozen=True)
class FiniteContextAtom:
    """A source identity with a bounded flat allocation-origin history."""

    kind: Literal["object", "variable", "field", "value"]
    source: SourceIdentity
    origins: tuple[SourceIdentity, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.kind not in {"object", "variable", "field", "value"}
            or not _valid_source_identity(self.source)
            or type(self.origins) is not tuple
            or len(self.origins) > 2
            or not all(_valid_source_identity(origin) for origin in self.origins)
        ):
            raise ValueError("FiniteContextAtom requires bounded flat source identities")


@dataclass(frozen=True)
class OrderedBindingToken:
    """Positional and keyword parameter origins, preserving order/multiplicity."""

    bindings: tuple[tuple[str, BindingAtom], ...] = ()

    def __post_init__(self) -> None:
        if type(self.bindings) is not tuple or any(
            type(entry) is not tuple
            or len(entry) != 2
            or not isinstance(entry[0], str)
            or not entry[0].startswith(("pos:", "kw:"))
            or not _binding_atom(entry[1])
            for entry in self.bindings
        ):
            raise ValueError("OrderedBindingToken requires named finite atoms")


def _source_identity(item: object, depth: int = 3) -> SourceIdentity:
    """Project known and historical structural source carriers into finite data."""
    if isinstance(item, (str, int, type(None), CallSite, IRStatement)):
        return item
    if depth <= 0:
        return ("source_shape_summary", type(item).__name__)
    if isinstance(item, FiniteContextAtom):
        return _source_identity(item.source, depth - 1)
    if isinstance(item, AllocSite):
        if isinstance(item.stmt, (str, IRStatement)):
            return item
        return ("allocation", item.kind, _source_identity(item.stmt, depth - 1))
    if isinstance(item, _AllocationCarrier):
        return _source_identity(item.alloc_site, depth - 1)
    decoded = _decode_native_metadata(item, "allocation_source", depth=depth)
    if _valid_source_identity(decoded):
        return decoded
    if _context_record(item):
        decoded = _decode_native_metadata(item, "source", depth=depth)
        return _source_payload(decoded)
    if isinstance(item, (tuple, frozenset)):
        items: tuple[object, ...] | frozenset[object] = item
        values = tuple(_source_identity(value, depth - 1) for value in items)
        return frozenset(values) if isinstance(item, frozenset) else values
    if isinstance(item, Enum):
        return item
    if isinstance(item, _NamedSource):
        return _source_payload(_decode_native_metadata(item, "source", depth=depth))
    decoded = _decode_native_metadata(item, "source", depth=depth)
    if _valid_source_identity(decoded):
        return decoded
    return ("source_shape_summary", type(item).__name__)


def _origin_items(value: object) -> tuple[object, ...]:
    if isinstance(value, Iterable):
        items: Iterable[object] = value
        return tuple(items)
    if isinstance(value, _MetadataSequence):
        result: list[object] = []
        while True:
            try:
                result.append(value[len(result)])
            except (IndexError, StopIteration):
                return tuple(result)
    raise TypeError("Context origins must be iterable")


def _origin_sources(context: object, limit: int = 2) -> tuple[SourceIdentity, ...]:
    decoded = _decode_native_metadata(context, "origins", limit=limit)
    if isinstance(decoded, _MetadataOrigins):
        return decoded.values
    raise TypeError("Malformed Native origin metadata")


def native_call_history(context: object) -> tuple[CallSite, ...]:
    """Read only actual call sites in the old four-field heap projection."""
    decoded = _decode_native_metadata(context, "calls")
    if isinstance(decoded, _MetadataCallHistory):
        return decoded.values
    raise TypeError("Malformed Native call history metadata")


def _decode_native_metadata(
    item: object,
    mode: Literal[
        "location",
        "calls",
        "source",
        "allocation_source",
        "allocation",
        "field",
        "variable",
        "origins",
        "keywords",
    ],
    *,
    depth: int = 3,
    limit: int = 2,
) -> _MetadataDecoded:
    """Decode stable readonly native metadata, including dynamic attributes.

    Fixed field reads are an external compatibility boundary. Raw objects never
    leave it: every result is projected/validated into the finite vocabulary.
    """
    if mode == "location":
        line: object = getattr(item, "lineno", None)
        column: object = getattr(item, "col_offset", None)
        if not isinstance(line, (int, type(None))) or not isinstance(column, (int, type(None))):
            raise ValueError("Source coordinates must be integers or None")
        return _MetadataLocation(line, column)
    if mode in {"origins", "calls"}:
        seen: set[int] = set()
        while mode == "origins":
            inner: object = (
                item.inner
                if isinstance(item, _WrappedContext)
                else getattr(item, "inner", _METADATA_MISSING)
            )
            if inner is _METADATA_MISSING:
                break
            if id(item) in seen:
                raise ValueError("Cyclic Native origin wrapper")
            seen.add(id(item))
            item = inner
        calls: object = (
            item.call_sites if isinstance(item, _CallOrigins) else getattr(item, "call_sites", ())
        )
        allocations: object = (
            item.alloc_sites
            if isinstance(item, _AllocationOrigins)
            else getattr(item, "alloc_sites", ())
        )
        types: object = item.types if isinstance(item, _TypeOrigins) else getattr(item, "types", ())
        receivers: object = (
            item.receivers if isinstance(item, _ReceiverOrigins) else getattr(item, "receivers", ())
        )
        call_items = (
            _origin_items(calls)
            + _origin_items(allocations)
            + _origin_items(types)
            + _origin_items(receivers)
        )
        if mode == "calls":
            return _MetadataCallHistory(
                tuple(value for value in call_items if isinstance(value, CallSite))
            )
        parameters: object = (
            item.params if isinstance(item, _ParameterOrigins) else getattr(item, "params", ())
        )
        items = call_items + _origin_items(parameters)
        sources: list[SourceIdentity] = []
        for origin in items[-limit:]:
            if isinstance(origin, FiniteContextAtom):
                source: SourceIdentity = origin.source
            elif isinstance(origin, OrderedBindingToken):
                source = tuple(
                    (key, atom.source if isinstance(atom, FiniteContextAtom) else atom)
                    for key, atom in origin.bindings
                )
            elif isinstance(origin, (CallSite, str, int, type(None))):
                source = origin
            elif isinstance(origin, _AllocationCarrier):
                source = _source_identity(origin.alloc_site)
            else:
                source = _source_identity(origin)
            sources.append(_source_payload(source))
        return _MetadataOrigins(tuple(sources))
    if mode == "keywords":
        if isinstance(item, _MetadataItems):
            entries: object = item.items()
        else:
            reader: object = getattr(item, "items", _METADATA_MISSING)
            if reader is _METADATA_MISSING:
                entries = item
            elif isinstance(reader, _MetadataReader):
                entries = reader()
            else:
                raise TypeError("Keyword metadata items must be callable")
        bindings: list[tuple[str, BindingAtom]] = []
        for entry in _origin_items(entries):
            pair = _origin_items(entry)
            if len(pair) != 2:
                raise ValueError("Keyword entries must contain two values")
            keyword_name, keyword_value = pair
            atom = finite_context_atom(keyword_value)
            if not _binding_atom(atom):
                raise ValueError("OrderedBindingToken requires named finite atoms")
            bindings.append((f"kw:{keyword_name}", atom))
        return OrderedBindingToken(tuple(bindings))
    if mode == "variable":
        if not _context_record(item):
            return _METADATA_MISSING
        variable_name: object = getattr(item.content, "name", _METADATA_MISSING)
        variable_kind: object = getattr(item.content, "kind", _METADATA_MISSING)
        if variable_name is _METADATA_MISSING or variable_kind is _METADATA_MISSING:
            return FiniteContextAtom("value", _source_identity(item.content))
        if not isinstance(variable_name, str):
            raise TypeError("Context source name must support string projection")
        if variable_name.startswith("$"):
            variable_name = variable_name.split("@", 1)[0]
        scope = None if item.scope is None else _source_payload(item.scope.stmt)
        return FiniteContextAtom("variable", (scope, _source_payload(variable_kind), variable_name))
    allocation: object = getattr(item, "alloc_site", _METADATA_MISSING)
    if mode == "allocation_source":
        if allocation is not _METADATA_MISSING:
            return _source_identity(allocation, depth - 1)
        return _METADATA_MISSING
    if mode == "source":
        if allocation is not _METADATA_MISSING:
            return _source_identity(allocation, depth - 1)
        if _context_record(item):
            context_name: object = getattr(item.content, "name", "<value>")
            context_kind: object = getattr(item.content, "kind", None)
            if not isinstance(context_name, str):
                raise TypeError("Context source name must support string projection")
            scope = None if item.scope is None else _source_payload(item.scope.stmt)
            return (scope, _source_payload(context_kind), context_name.split("@", 1)[0])
        descriptor_kind: object = getattr(item, "kind", _METADATA_MISSING)
        descriptor_name: object = getattr(item, "name", _METADATA_MISSING)
        if descriptor_kind is not _METADATA_MISSING and descriptor_name is not _METADATA_MISSING:
            if isinstance(item, _IndexedSource):
                index: object = item.index
            else:
                dynamic_index: object = getattr(item, "index", None)
                index = dynamic_index
            if isinstance(item, _TypedKeySource):
                key_type: object = item.key_type
            else:
                dynamic_key_type: object = getattr(item, "key_type", None)
                key_type = dynamic_key_type
            return _source_payload((descriptor_kind, descriptor_name, index, key_type))
        return _METADATA_MISSING
    if mode == "allocation":
        context: object = getattr(item, "context", _METADATA_MISSING)
        if allocation is not _METADATA_MISSING and context is not _METADATA_MISSING:
            return FiniteContextAtom(
                "object", _source_identity(allocation), _origin_sources(context)
            )
        return _METADATA_MISSING
    owner: object = getattr(item, "obj", _METADATA_MISSING)
    field: object = getattr(item, "field", _METADATA_MISSING)
    if owner is not _METADATA_MISSING and field is not _METADATA_MISSING:
        projected = finite_context_atom(owner)
        owner_source = projected.source if isinstance(projected, FiniteContextAtom) else projected
        return FiniteContextAtom("field", _source_payload((owner_source, field)))
    return _METADATA_MISSING


def finite_context_atom(item: object) -> FiniteContextKey:
    """Forget runtime identities while preserving the old finite source key."""
    if isinstance(item, (FiniteContextAtom, CallSite, str, int, type(None))):
        return item
    if isinstance(item, OrderedBindingToken):
        pairs: list[tuple[str, BindingAtom]] = []
        for key, value in item.bindings:
            atom = finite_context_atom(value)
            if not _binding_atom(atom):
                raise ValueError("OrderedBindingToken requires named finite atoms")
            pairs.append((key, atom))
        return OrderedBindingToken(tuple(pairs))
    if _context_record(item):
        content: object = item.content
        if isinstance(content, _NamedSource):
            name = content.name
            if not isinstance(name, str):
                raise TypeError("Context source name must support string projection")
            if name.startswith("$"):
                name = name.split("@", 1)[0]
            scope = None if item.scope is None else _source_payload(item.scope.stmt)
            return FiniteContextAtom("variable", (scope, _source_payload(content.kind), name))
        decoded = _decode_native_metadata(item, "variable")
        if isinstance(decoded, FiniteContextAtom):
            return decoded
        raise TypeError("Malformed context variable metadata")
    if isinstance(item, _AllocationCarrier) and isinstance(item, _ContextCarrier):
        return FiniteContextAtom(
            "object", _source_identity(item.alloc_site), _origin_sources(item.context)
        )
    decoded = _decode_native_metadata(item, "allocation")
    if isinstance(decoded, FiniteContextAtom):
        return decoded
    if isinstance(item, tuple):
        items: tuple[object, ...] = item
        return tuple(finite_context_atom(value) for value in items)
    if isinstance(item, frozenset):
        members: frozenset[object] = item
        return frozenset(finite_context_atom(value) for value in members)
    if isinstance(item, _FieldCarrier):
        obj = finite_context_atom(item.obj)
        source = obj.source if isinstance(obj, FiniteContextAtom) else obj
        return FiniteContextAtom("field", _source_payload((source, item.field)))
    decoded = _decode_native_metadata(item, "field")
    if isinstance(decoded, FiniteContextAtom):
        return decoded
    if isinstance(item, AbstractContext):
        raise ValueError("A context is not a finite context atom")
    if isinstance(item, (AllocSite, IRStatement)):
        return _source_identity(item)
    raise TypeError(f"Unsupported context atom: {type(item).__name__}")


def ordered_binding_token(
    args: Iterable[object] = (),
    kwargs: object = (),
) -> OrderedBindingToken:
    pairs: list[tuple[str, BindingAtom]] = []
    for index, value in enumerate(args):
        atom = finite_context_atom(value)
        if not _binding_atom(atom):
            raise ValueError("OrderedBindingToken requires named finite atoms")
        pairs.append((f"pos:{index}", atom))
    decoded = _decode_native_metadata(kwargs, "keywords")
    if not isinstance(decoded, OrderedBindingToken):
        raise TypeError("Malformed keyword metadata")
    pairs.extend(decoded.bindings)
    return OrderedBindingToken(tuple(pairs))


max_idx: int = 0
context_pool: dict[Hashable, int] = {}


T = TypeVar("T")


class AbstractContext[InputT](ABC):
    """Base class for all context implementations."""

    def __post_init__(self) -> None:
        global max_idx
        if self not in context_pool:
            context_pool[self] = max_idx
            max_idx += 1

    @abstractmethod
    def to_string(self) -> str:
        """String representation for hashing/comparison."""

    @abstractmethod
    def is_empty(self) -> bool:
        """Check if context is empty."""

    @abstractmethod
    def append(self, call_site: InputT) -> AbstractContext[InputT]:
        """Append a call site to the context."""

    @override
    @abstractmethod
    def __hash__(self) -> int:
        """Hash for use in dictionaries/sets."""

    # @abstractmethod
    @override
    def __eq__(self, other: object) -> bool:
        """Equality comparison."""
        if not isinstance(other, AbstractContext):
            return False
        return context_pool[self] == context_pool[other]

    @override
    def __str__(self) -> str:
        return f"Ctx[{context_pool[self]}]"

    @override
    def __repr__(self) -> str:
        return str(self)


@dataclass(frozen=True)
class CallStringContext(AbstractContext[CallContextSite]):
    """Call-string sensitivity (k-CFA).

    Context is a sequence of call sites representing the call string.

    Attributes:
        call_sites: Call sites in calling order (most recent last)
        k: Maximum context length
    """

    call_sites: tuple[CallContextSite, ...] = ()
    k: int = 2

    @override
    def to_string(self) -> str:
        if not self.call_sites:
            return "[]"
        return "[" + " → ".join(str(cs) for cs in self.call_sites) + "]"

    @override
    def is_empty(self) -> bool:
        return len(self.call_sites) == 0

    @override
    def append(self, call_site: CallContextSite) -> CallStringContext:
        """Create new context by appending call site."""
        if self.k == 0:
            return self
        new_sites = (self.call_sites + (call_site,))[-self.k :]
        return CallStringContext(new_sites, self.k)

    def __len__(self) -> int:
        return len(self.call_sites)

    @override
    def __hash__(self) -> int:
        return hash((self.call_sites, self.k))

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CallStringContext):
            return False
        return self.call_sites == other.call_sites and self.k == other.k


@dataclass(frozen=True)
class ObjectContext(AbstractContext[ContextSeed]):
    """Object sensitivity: allocation site chain.

    Attributes:
        alloc_sites: Objects and call sites
        depth: Maximum depth for object context
    """

    alloc_sites: tuple[ContextSeed, ...] = ()
    depth: int = 2

    @override
    def __post_init__(self) -> None:
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        values = tuple(finite_context_atom(value) for value in self.alloc_sites)
        object.__setattr__(self, "alloc_sites", values[-self.depth :] if self.depth else ())
        super().__post_init__()

    @override
    def to_string(self) -> str:
        if not self.alloc_sites:
            return "<>"
        shortened = [str(s) for s in self.alloc_sites]
        return "<" + ",".join(shortened) + ">"

    @override
    def is_empty(self) -> bool:
        return len(self.alloc_sites) == 0

    @override
    def append(self, item: ContextSeed) -> ObjectContext:
        """Create new context by appending allocation site."""
        projected = finite_context_atom(item)
        if self.depth == 0:
            return self
        new_sites = (self.alloc_sites + (projected,))[-self.depth :]
        return ObjectContext(new_sites, self.depth)

    @override
    def __hash__(self) -> int:
        return hash((self.alloc_sites, self.depth))

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ObjectContext):
            return False
        return self.alloc_sites == other.alloc_sites and self.depth == other.depth


@dataclass(frozen=True)
class TypeContext(AbstractContext[ContextSeed]):
    """Type sensitivity: receiver type chain.

    Attributes:
        types: Type objects and call sites
        depth: Maximum depth for type context
    """

    types: tuple[ContextSeed, ...] = ()
    depth: int = 2

    @override
    def __post_init__(self) -> None:
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        values = tuple(finite_context_atom(value) for value in self.types)
        object.__setattr__(self, "types", values[-self.depth :] if self.depth else ())
        super().__post_init__()

    @override
    def to_string(self) -> str:
        if not self.types:
            return "<:>"
        return "<" + ":".join(str(t) for t in self.types) + ">"

    @override
    def is_empty(self) -> bool:
        return len(self.types) == 0

    @override
    def append(self, item: ContextSeed) -> TypeContext:
        """Create new context by appending type."""
        if self.depth == 0:
            return self
        new_types = (self.types + (finite_context_atom(item),))[-self.depth :]
        return TypeContext(new_types, self.depth)

    @override
    def __hash__(self) -> int:
        return hash((self.types, self.depth))

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TypeContext):
            return False
        return self.types == other.types and self.depth == other.depth


@dataclass(frozen=True)
class ReceiverContext(AbstractContext[ContextSeed]):
    """Receiver-object sensitivity: self/receiver allocation sites.

    Attributes:
        receivers: Receiver allocation sites and call sites
        depth: Maximum depth for receiver context
    """

    receivers: tuple[ContextSeed, ...] = ()
    depth: int = 2

    @override
    def __post_init__(self) -> None:
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        values = tuple(finite_context_atom(value) for value in self.receivers)
        object.__setattr__(self, "receivers", values[-self.depth :] if self.depth else ())
        super().__post_init__()

    @override
    def to_string(self) -> str:
        if not self.receivers:
            return "<rcv:>"
        shortened = [str(r).split(":")[-1] for r in self.receivers]
        return "<rcv:" + ",".join(shortened) + ">"

    @override
    def is_empty(self) -> bool:
        return len(self.receivers) == 0

    @override
    def append(self, item: ContextSeed) -> ReceiverContext:
        """Create new context by appending receiver allocation site."""
        if self.depth == 0:
            return self
        new_receivers = (self.receivers + (finite_context_atom(item),))[-self.depth :]
        return ReceiverContext(new_receivers, self.depth)

    @override
    def __hash__(self) -> int:
        return hash((self.receivers, self.depth))

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ReceiverContext):
            return False
        return self.receivers == other.receivers and self.depth == other.depth


@dataclass(frozen=True)
class ParamContext(AbstractContext[ParameterContextInput]):
    """Receiver-object sensitivity: self/receiver allocation sites.

    Attributes:
        params: Parameters
        depth: Maximum depth for receiver context
    """

    params: tuple[FiniteContextKey | ParameterContextInput, ...] = ()
    depth: int = 2

    @override
    def __post_init__(self) -> None:
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        values: list[FiniteContextKey] = []
        for raw_value in self.params:
            if isinstance(raw_value, (CallSite, OrderedBindingToken, frozenset)):
                projected = finite_context_atom(raw_value)
            else:
                projected = ordered_binding_token(_origin_items(raw_value))
            values.append(projected)
        normalized: tuple[FiniteContextKey, ...] = (
            tuple(values[-self.depth :]) if self.depth else ()
        )
        object.__setattr__(self, "params", normalized)
        super().__post_init__()

    @override
    def to_string(self) -> str:
        if not self.params:
            return "<param:>"
        return "<param:" + ",".join(str(p) for p in self.params) + ">"

    @override
    def is_empty(self) -> bool:
        return len(self.params) == 0

    @override
    def append(self, params: ParameterContextInput) -> ParamContext:
        """Create new context by appending parameters."""
        if self.depth == 0:
            return self
        normalized: FiniteContextKey | ParameterContextInput = params
        if not isinstance(params, (CallSite, OrderedBindingToken)):
            if isinstance(params, frozenset):
                normalized = finite_context_atom(params)
            else:
                normalized = ordered_binding_token(params)
        new_params = (self.params + (normalized,))[-self.depth :]
        return ParamContext(new_params, self.depth)

    @override
    def __hash__(self) -> int:
        return hash((self.params, self.depth))

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ParamContext):
            return False
        return self.params == other.params and self.depth == other.depth


class _MissingHybridReceiver(Enum):
    TOKEN = "missing_hybrid_receiver"


type HybridContextInput = tuple[CallContextSite, AbstractObject | None]


@dataclass(frozen=True)
class HybridContext(AbstractContext[HybridContextInput]):
    """Hybrid: Combine call-string + object sensitivity.

    Attributes:
        call_sites: Call sites
        alloc_sites: Allocation site IDs
        call_k: Maximum call-string length
        obj_depth: Maximum object context depth
    """

    call_sites: tuple[CallContextSite, ...] = ()
    alloc_sites: tuple[AbstractObject | FiniteContextKey, ...] = ()
    call_k: int = 1
    obj_depth: int = 1

    @override
    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in (self.call_k, self.obj_depth)):
            raise ValueError("context depths must be nonnegative integers")
        values = tuple(finite_context_atom(value) for value in self.alloc_sites)
        object.__setattr__(self, "alloc_sites", values[-self.obj_depth :] if self.obj_depth else ())
        object.__setattr__(
            self, "call_sites", self.call_sites[-self.call_k :] if self.call_k else ()
        )
        super().__post_init__()

    @override
    def to_string(self) -> str:
        call_part = (
            "[" + ",".join(str(cs) for cs in self.call_sites) + "]" if self.call_sites else "[]"
        )
        shortened = [str(s).split(":")[-1] for s in self.alloc_sites]
        obj_part = "<" + ",".join(shortened) + ">" if self.alloc_sites else "<>"
        return call_part + obj_part

    @override
    def is_empty(self) -> bool:
        return len(self.call_sites) == 0 and len(self.alloc_sites) == 0

    def append_call(self, call_site: CallContextSite) -> HybridContext:
        """Create new context by appending call site."""
        if self.call_k == 0:
            return self
        new_calls = (self.call_sites + (call_site,))[-self.call_k :]
        return HybridContext(new_calls, self.alloc_sites, self.call_k, self.obj_depth)

    def append_object(self, alloc_site: ContextSeed) -> HybridContext:
        """Create new context by appending allocation site."""
        if self.obj_depth == 0:
            return self
        new_allocs = (self.alloc_sites + (finite_context_atom(alloc_site),))[-self.obj_depth :]
        return HybridContext(self.call_sites, new_allocs, self.call_k, self.obj_depth)

    @overload
    def append(self, call_site: HybridContextInput) -> HybridContext: ...

    @overload
    def append(
        self, call_site: CallContextSite, alloc_site: AbstractObject | None
    ) -> HybridContext: ...

    @override
    def append(
        self,
        call_site: CallContextSite | HybridContextInput,
        alloc_site: AbstractObject | None | _MissingHybridReceiver = _MissingHybridReceiver.TOKEN,
    ) -> HybridContext:
        if isinstance(alloc_site, _MissingHybridReceiver):
            if not isinstance(call_site, tuple) or len(call_site) != 2:
                raise TypeError(
                    "HybridContext.append() missing 1 required positional argument: 'alloc_site'"
                )
            call_site, alloc_site = call_site
        elif isinstance(call_site, tuple):
            raise TypeError("HybridContext.append() requires a CallSite for the two-argument form")
        new_calls = (self.call_sites + (call_site,))[-self.call_k :] if self.call_k else ()
        new_allocs = (
            (self.alloc_sites + (finite_context_atom(alloc_site),))[-self.obj_depth :]
            if self.obj_depth
            else ()
        )
        return HybridContext(new_calls, new_allocs, self.call_k, self.obj_depth)

    @override
    def __hash__(self) -> int:
        return hash((self.call_sites, self.alloc_sites, self.call_k, self.obj_depth))

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, HybridContext):
            return False
        return (
            self.call_sites == other.call_sites
            and self.alloc_sites == other.alloc_sites
            and self.call_k == other.call_k
            and self.obj_depth == other.obj_depth
        )


@dataclass(frozen=True)
class SummaryContext(AbstractContext[T]):
    """Explicit marker for summary objects produced by widening."""

    inner: AbstractContext[T] | None

    @override
    def to_string(self) -> str:
        if self.inner is None:
            raise AttributeError("'NoneType' object has no attribute 'to_string'")
        return f"S[{self.inner.to_string()}]"

    @override
    def is_empty(self) -> bool:
        if self.inner is None:
            raise AttributeError("'NoneType' object has no attribute 'is_empty'")
        return self.inner.is_empty()

    @override
    def append(self, call_site: T) -> SummaryContext[T]:
        if self.inner is None:
            raise AttributeError("'NoneType' object has no attribute 'append'")
        return SummaryContext(self.inner.append(call_site))

    @override
    def __hash__(self) -> int:
        return hash(("summary", self.inner))

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SummaryContext):
            return False
        return self.inner == other.inner


def is_summary_context[ItemT](context: AbstractContext[ItemT]) -> TypeGuard[SummaryContext[ItemT]]:
    return isinstance(context, SummaryContext)


@dataclass(frozen=True)
class Ctx[CtxContentT]:
    """Content with context.

    Attributes:
        context: NativeContext | None
        content: CtxContentT
    """

    context: NativeContext | None
    scope: Scope | None
    content: CtxContentT

    @override
    def __hash__(self) -> int:
        return hash((self.content, self.scope, self.context))

    @override
    def __eq__(self, other: object) -> bool:
        if not _context_record(other):
            return False
        return (
            self.content == other.content
            and self.context == other.context
            and self.scope == other.scope
        )

    @override
    def __str__(self) -> str:
        return f"{self.context}:{self.content}"


# Runtime class validation erases only the generic parameter at this boundary;
# the TypeGuard above exposes a frozen Ctx[object] for validated content reads.
_CTX_RUNTIME: type[object] = Ctx


@dataclass(frozen=True)
class Scope:
    """Function or module scope for variables.

    Attributes:
        name: Qualified scope name (e.g., "module.Class.method")
        kind: Type of scope
        parent: Last level scope
        module: Top level scope
    """

    stmt: IRScope
    obj: AbstractObject
    context: NativeContext
    _parent: Scope | None
    _module: Scope | None

    def __post_init__(self) -> None:
        if not isinstance(self.stmt, IRScope):
            raise ValueError(f"Scope must be an IRScope, {self.stmt} got")
        if not isinstance(self.stmt, IRModule) and self._parent is None:
            raise ValueError("Parent is required for non-module scopes")
        if self._module is not None and not isinstance(self._module.stmt, IRModule):
            raise ValueError(f"Module shoud be IRModule, but got {type(self._module.stmt)}!")

    @override
    def __hash__(self) -> int:
        return hash((self.stmt, self.context, self.obj))

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Scope):
            return False
        return self.stmt == other.stmt and self.context == other.context and self.obj == other.obj

    @override
    def __str__(self) -> str:
        return f"Scope[{self.stmt}:<{self.obj}>@{self.context}]"

    @classmethod
    def new(
        cls,
        obj: AbstractObject,
        module: Scope | None,
        context: NativeContext,
        stmt: IRScope,
        parent: Scope | None = None,
    ) -> Scope:
        if isinstance(stmt, IRModule) and parent is not None:
            parent = None
        return cls(stmt, obj, context, parent, module)

    @property
    def name(self) -> str:
        return self.stmt.get_qualname()

    @property
    def module(self) -> Scope:
        if self._module:
            return self._module
        return self

    @property
    def parent(self) -> Scope:
        if self._parent is None:
            if self._module is None:
                return self
            return self._module
        return self._parent

    @property
    def kind(
        self,
    ) -> (
        Literal["function", "instance_method", "class_method", "static_method", "module", "class"]
        | None
    ):
        from pythonstan.ir.ir_statements import IRClass, IRFunc, IRModule

        if isinstance(self.stmt, IRFunc):
            if self.stmt.is_instance_method:
                return "instance_method"
            if self.stmt.is_class_method:
                return "class_method"
            if self.stmt.is_static_method:
                return "static_method"
            return "function"
        if isinstance(self.stmt, IRClass):
            return "class"
        if isinstance(self.stmt, IRModule):
            return "module"
        return None


# Actual policy families admitted at the Native boundary. No custom root context
# is silently treated as one of these implementations.
type NativeBaseContext = (
    CallStringContext | ObjectContext | TypeContext | ReceiverContext | ParamContext | HybridContext
)
type NativeSummaryContext = (
    SummaryContext[Never]
    | SummaryContext[CallContextSite]
    | SummaryContext[ContextSeed]
    | SummaryContext[ContextSeed]
    | SummaryContext[ContextSeed]
    | SummaryContext[ParameterContextInput]
    | SummaryContext[HybridContextInput]
)
type NativeProtocolContext = (
    ProtocolKeyContext[CallContextSite]
    | ProtocolKeyContext[ContextSeed]
    | ProtocolKeyContext[ContextSeed]
    | ProtocolKeyContext[ContextSeed]
    | ProtocolKeyContext[ParameterContextInput]
    | ProtocolKeyContext[HybridContextInput]
)
type NativeContext = (
    CallStringContext
    | ObjectContext
    | TypeContext
    | ReceiverContext
    | ParamContext
    | HybridContext
    | NativeSummaryContext
    | NativeProtocolContext
)


def is_native_context(value: object) -> TypeGuard[NativeContext]:
    # The optional family imports the base records, so resolve it only after
    # ordinary module initialization rather than creating a runtime cycle.
    from .protocol_context import ProtocolKeyContext

    # Generic wrappers remain general-purpose APIs. The Native profile admits
    # only wrappers whose eventual base is one of the six registered families.
    # Runtime class tokens and a readonly capability avoid inventing an erased
    # wrapper type argument while validating the actual object graph.
    wrappers: tuple[type[object], type[object]] = (SummaryContext, ProtocolKeyContext)
    current: object = value
    seen: set[int] = set()
    while isinstance(current, wrappers):
        identity = id(current)
        if identity in seen or not isinstance(current, _WrappedContext):
            return False
        seen.add(identity)
        inner = current.inner
        if inner is None:
            return isinstance(current, SummaryContext)
        current = inner
    return isinstance(
        current,
        (
            CallStringContext,
            ObjectContext,
            TypeContext,
            ReceiverContext,
            ParamContext,
            HybridContext,
        ),
    )


def make_native_summary(context: NativeContext | None) -> NativeSummaryContext:
    """Construct the real wrapper, preserving its exact inner identity.

    The general wrapper remains generic. This factory validates the emitted
    registered profile, including the historically admitted absent inner.
    """
    result = SummaryContext(context)
    if is_native_context(result) and isinstance(result, SummaryContext):
        return result
    raise ValueError("Summary context is outside the registered Native profile")
