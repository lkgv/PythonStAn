"""Context abstractions for context-sensitive pointer analysis.

This module defines context representations for different context sensitivity
policies including call-string, object, type, receiver, and hybrid contexts.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Tuple, Optional, Any, TypeVar, Generic, Union, Literal, TYPE_CHECKING, Dict

from pythonstan.analysis.pointer.kcfa.object import FunctionObject, ClassObject, ModuleObject
from pythonstan.ir.ir_statements import IRScope, IRModule, IRStatement

if TYPE_CHECKING:
    from .object import AbstractObject, AllocSite

__all__ = [
    "CallSite",
    "Ctx",
    "Scope",
    "AbstractContext",
    "CallStringContext",
    "ObjectContext",
    "TypeContext",
    "ReceiverContext",
    "ParamContext",
    "HybridContext",
    "SummaryContext",
    "FiniteContextAtom",
    "OrderedBindingToken",
    "finite_context_atom",
    "ordered_binding_token",
]


@dataclass(frozen=True)
class CallSite:
    """Call site identifier bound to an IR statement."""
    
    statement: IRStatement
    scope_name: Optional[str] = None
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
    
    def _location(self) -> Tuple[Optional[int], Optional[int]]:
        ast_node = self.statement.get_ast()
        if ast_node is None:
            return None, None
        line = getattr(ast_node, "lineno", None)
        col = getattr(ast_node, "col_offset", None)
        return line, col
    
    def __str__(self) -> str:
        return self.site_id


def _valid_source_identity(value, depth=8):
    """Validate the atom boundary without traversing source IR internals."""
    from enum import Enum
    from .object import AllocSite
    if isinstance(value, (str, int, bool, type(None), Enum, IRStatement)):
        return True
    if isinstance(value, CallSite):
        return isinstance(value.statement, IRStatement)
    if isinstance(value, AllocSite):
        # A synthetic allocation payload embedding owner objects must have
        # passed _source_identity, producing a bounded tuple, before this API.
        return isinstance(value.stmt, (str, IRStatement))
    if depth > 0 and isinstance(value, (tuple, frozenset)):
        return all(_valid_source_identity(item, depth - 1) for item in value)
    return False


@dataclass(frozen=True)
class FiniteContextAtom:
    """A source identity with a bounded *flat* allocation history.

    ``source`` and ``origins`` contain only source sites/variable descriptors,
    never objects, scopes, or contexts. In particular an origin atom contributes
    only its source, not its own origins. The token universe is therefore finite
    for a frozen source/model inventory even under recursive allocation.
    """

    kind: str
    source: Any
    origins: Tuple[Any, ...] = ()

    def __post_init__(self):
        if (self.kind not in {"object", "variable", "field", "value"}
                or not _valid_source_identity(self.source)
                or type(self.origins) is not tuple or len(self.origins) > 2
                or not all(_valid_source_identity(origin) for origin in self.origins)):
            raise ValueError("FiniteContextAtom requires bounded flat source identities")


@dataclass(frozen=True)
class OrderedBindingToken:
    """Positional and keyword parameter origins, with multiplicity intact."""

    bindings: Tuple[Tuple[str, Any], ...] = ()

    def __post_init__(self):
        if type(self.bindings) is not tuple or any(
                type(entry) is not tuple or len(entry) != 2
                or not isinstance(entry[0], str)
                or not entry[0].startswith(("pos:", "kw:"))
                or not isinstance(entry[1], (FiniteContextAtom, CallSite, str, int, type(None)))
                for entry in self.bindings):
            raise ValueError("OrderedBindingToken requires named finite atoms")



def _source_identity(item, depth=3):
    """Project synthetic sites too: their payloads may contain owner objects.

    Synthetic namespace/collector sites sometimes embed an AbstractObject in a
    tuple. Keeping that tuple unchanged would reintroduce the original recursive
    identity through an AllocSite. Bound this source-shape projection as well.
    """
    from .object import AllocSite
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
    if hasattr(item, "alloc_site"):
        return _source_identity(item.alloc_site, depth - 1)
    if isinstance(item, Ctx):
        content = item.content
        return (getattr(item.scope, "stmt", None),
                getattr(content, "kind", None),
                getattr(content, "name", "<value>").split("@", 1)[0])
    if isinstance(item, (tuple, frozenset)):
        values = tuple(_source_identity(value, depth - 1) for value in item)
        return frozenset(values) if isinstance(item, frozenset) else values
    # Enums and immutable field descriptors are finite model symbols.
    from enum import Enum
    if isinstance(item, Enum):
        return item
    if hasattr(item, "kind") and hasattr(item, "name"):
        return (item.kind, item.name, getattr(item, "index", None),
                getattr(item, "key_type", None))
    return ("source_shape_summary", type(item).__name__)


def _origin_sources(context, limit=2):
    # Context wrappers are stripped, never retained inside a token.
    while hasattr(context, "inner"):
        context = context.inner
    items = ()
    for name in ("call_sites", "alloc_sites", "types", "receivers", "params"):
        items += tuple(getattr(context, name, ()))
    sources = []
    for item in items[-limit:]:
        if isinstance(item, FiniteContextAtom):
            sources.append(item.source)
        elif isinstance(item, OrderedBindingToken):
            sources.append(tuple((key, atom.source if isinstance(atom, FiniteContextAtom)
                                  else atom) for key, atom in item.bindings))
        elif isinstance(item, CallSite):
            sources.append(item)
        elif isinstance(item, (str, int, type(None))):
            sources.append(item)
        elif hasattr(item, "alloc_site"):
            sources.append(_source_identity(item.alloc_site))
        else:
            # AllocSite and IR identities contain source, never analysis state.
            sources.append(_source_identity(item))
    return tuple(sources)


def finite_context_atom(item):
    """Forget recursive analysis identities while retaining finite origins.

    Parameter sensitivity is explicitly sensitivity to ordered argument source
    locations. It is not a promise of distinguishing all abstract argument
    values. ``$`` helper variable identities omit dynamic object-id suffixes.
    """
    if isinstance(item, (FiniteContextAtom, CallSite, str, int, type(None))):
        return item
    if isinstance(item, OrderedBindingToken):
        return OrderedBindingToken(tuple((key, finite_context_atom(value))
                                         for key, value in item.bindings))
    if isinstance(item, Ctx):
        content = item.content
        if hasattr(content, "name") and hasattr(content, "kind"):
            name = content.name
            if name.startswith("$"):
                name = name.split("@", 1)[0]
            source = (getattr(item.scope, "stmt", None), content.kind, name)
            return FiniteContextAtom("variable", source)
        return FiniteContextAtom("value", _source_identity(content))
    if hasattr(item, "alloc_site") and hasattr(item, "context"):
        return FiniteContextAtom("object", _source_identity(item.alloc_site),
                                 _origin_sources(item.context))
    if isinstance(item, tuple):
        return tuple(finite_context_atom(value) for value in item)
    if isinstance(item, frozenset):
        # Compatibility for direct old API callers only. Engine callers pass
        # OrderedBindingToken, so call argument order is not discarded here.
        return frozenset(finite_context_atom(value) for value in item)
    if hasattr(item, "obj") and hasattr(item, "field"):
        obj = finite_context_atom(item.obj)
        return FiniteContextAtom("field", (getattr(obj, "source", obj), item.field))
    if isinstance(item, AbstractContext):
        raise ValueError("A context is not a finite context atom")
    from .object import AllocSite
    if isinstance(item, (AllocSite, IRStatement)):
        return _source_identity(item)
    raise TypeError(f"Unsupported context atom: {type(item).__name__}")


def ordered_binding_token(args=(), kwargs=()):
    """Canonical token preserving source evaluation position and keyword name."""
    pairs = [(f"pos:{index}", finite_context_atom(value))
             for index, value in enumerate(args)]
    entries = kwargs.items() if hasattr(kwargs, "items") else kwargs
    pairs.extend((f"kw:{name}", finite_context_atom(value))
                 for name, value in entries)
    return OrderedBindingToken(tuple(pairs))


max_idx: int = 0
context_pool: Dict['AbstractContext', int] = {}


T = TypeVar('T', 'CallSite', 'AbstractObject', 'AllocSite')
class AbstractContext(ABC, Generic[T]):
    """Base class for all context implementations."""
    
    def __post_init__(self):
        global max_idx
        if self not in context_pool:
            context_pool[self] = max_idx
            max_idx += 1
    
    @abstractmethod
    def to_string(self) -> str:
        """String representation for hashing/comparison."""
        pass
    
    @abstractmethod
    def is_empty(self) -> bool:
        """Check if context is empty."""
        pass
    
    @abstractmethod
    def append(self, call_site: T) -> 'AbstractContext':
        """Append a call site to the context."""
        pass

    @abstractmethod
    def __hash__(self) -> int:
        """Hash for use in dictionaries/sets."""
        pass
    
    # @abstractmethod
    def __eq__(self, other: Any) -> bool:
        """Equality comparison."""
        context_pool[self] == context_pool[other]
    
    def __str__(self) -> str:
        return f"Ctx[{context_pool[self]}]"
    
    def __repr__(self) -> str:
        return str(self)


@dataclass(frozen=True)
class CallStringContext(AbstractContext['CallSite']):
    """Call-string sensitivity (k-CFA).
    
    Context is a sequence of call sites representing the call string.
    
    Attributes:
        call_sites: Call sites in calling order (most recent last)
        k: Maximum context length
    """
    
    call_sites: Tuple[CallSite, ...] = ()
    k: int = 2
    
    def to_string(self) -> str:
        if not self.call_sites:
            return "[]"
        return "[" + " → ".join(str(cs) for cs in self.call_sites) + "]"
    
    def is_empty(self) -> bool:
        return len(self.call_sites) == 0
    
    def append(self, call_site: CallSite) -> 'CallStringContext':
        """Create new context by appending call site."""
        if self.k == 0:
            return self
        new_sites = (self.call_sites + (call_site,))[-self.k:]
        return CallStringContext(new_sites, self.k)
    
    def __len__(self) -> int:
        return len(self.call_sites)
    
    def __hash__(self) -> int:
        return hash((self.call_sites, self.k))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, CallStringContext):
            return False
        return self.call_sites == other.call_sites and self.k == other.k


@dataclass(frozen=True)
class ObjectContext(AbstractContext[Union['CallSite', 'AbstractObject']]):
    """Object sensitivity: allocation site chain.
    
    Attributes:
        alloc_sites: Objects and call sites
        depth: Maximum depth for object context
    """
    
    alloc_sites: Tuple[Union['CallSite', 'AbstractObject'], ...] = ()
    depth: int = 2
    
    def __post_init__(self):
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        values = tuple(finite_context_atom(value) for value in self.alloc_sites)
        object.__setattr__(self, "alloc_sites", values[-self.depth:] if self.depth else ())
        super().__post_init__()

    def to_string(self) -> str:
        if not self.alloc_sites:
            return "<>"
        shortened = [str(s) for s in self.alloc_sites]
        return "<" + ",".join(shortened) + ">"
    
    def is_empty(self) -> bool:
        return len(self.alloc_sites) == 0
    
    def append(self, item: Union['CallSite', 'AbstractObject']) -> 'ObjectContext':
        """Create new context by appending allocation site."""
        item = finite_context_atom(item)
        if self.depth == 0:
            return self
        new_sites = (self.alloc_sites + (item,))[-self.depth:]
        return ObjectContext(new_sites, self.depth)
    
    def __hash__(self) -> int:
        return hash((self.alloc_sites, self.depth))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ObjectContext):
            return False
        return self.alloc_sites == other.alloc_sites and self.depth == other.depth


@dataclass(frozen=True)
class TypeContext(AbstractContext[Union['CallSite', 'AbstractObject']]):
    """Type sensitivity: receiver type chain.
    
    Attributes:
        types: Type objects and call sites
        depth: Maximum depth for type context
    """
    
    types: Tuple[Union['CallSite', 'AbstractObject'], ...] = ()
    depth: int = 2
    
    def __post_init__(self):
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        values = tuple(finite_context_atom(value) for value in self.types)
        object.__setattr__(self, "types", values[-self.depth:] if self.depth else ())
        super().__post_init__()

    def to_string(self) -> str:
        if not self.types:
            return "<:>"
        return "<" + ":".join(str(t) for t in self.types) + ">"
    
    def is_empty(self) -> bool:
        return len(self.types) == 0
    
    def append(self, item: Union['CallSite', 'AbstractObject']) -> 'TypeContext':
        """Create new context by appending type."""
        if self.depth == 0:
            return self
        new_types = (self.types + (finite_context_atom(item),))[-self.depth:]
        return TypeContext(new_types, self.depth)
    
    def __hash__(self) -> int:
        return hash((self.types, self.depth))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, TypeContext):
            return False
        return self.types == other.types and self.depth == other.depth


@dataclass(frozen=True)
class ReceiverContext(AbstractContext[Union['CallSite', 'AllocSite']]):
    """Receiver-object sensitivity: self/receiver allocation sites.
    
    Attributes:
        receivers: Receiver allocation sites and call sites
        depth: Maximum depth for receiver context
    """
    
    receivers: Tuple[Union['CallSite', 'AllocSite'], ...] = ()
    depth: int = 2
    
    def __post_init__(self):
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        values = tuple(finite_context_atom(value) for value in self.receivers)
        object.__setattr__(self, "receivers", values[-self.depth:] if self.depth else ())
        super().__post_init__()

    def to_string(self) -> str:
        if not self.receivers:
            return "<rcv:>"
        shortened = [str(r).split(':')[-1] for r in self.receivers]
        return "<rcv:" + ",".join(shortened) + ">"
    
    def is_empty(self) -> bool:
        return len(self.receivers) == 0
    
    def append(self, item: Union['CallSite', 'AllocSite']) -> 'ReceiverContext':
        """Create new context by appending receiver allocation site."""
        if self.depth == 0:
            return self
        new_receivers = (self.receivers + (finite_context_atom(item),))[-self.depth:]
        return ReceiverContext(new_receivers, self.depth)
    
    def __hash__(self) -> int:
        return hash((self.receivers, self.depth))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ReceiverContext):
            return False
        return self.receivers == other.receivers and self.depth == other.depth


@dataclass(frozen=True)
class ParamContext(AbstractContext[Tuple['AbstractObject', ...]]):
    """Receiver-object sensitivity: self/receiver allocation sites.
    
    Attributes:
        params: Parameters
        depth: Maximum depth for receiver context
    """

    params: Tuple[Union[CallSite, Tuple['AbstractObject', ...]], ...] = ()
    depth: int = 2
    
    def __post_init__(self):
        if type(self.depth) is not int or self.depth < 0:
            raise ValueError("depth must be a nonnegative integer")
        values = []
        for value in self.params:
            if isinstance(value, (CallSite, OrderedBindingToken, frozenset)):
                value = finite_context_atom(value)
            else:
                value = ordered_binding_token(value)
            values.append(value)
        object.__setattr__(self, "params", tuple(values[-self.depth:]) if self.depth else ())
        super().__post_init__()

    def to_string(self) -> str:
        if not self.params:
            return "<param:>"
        return "<param:" + ",".join(str(p) for p in self.params) + ">"
    
    def is_empty(self) -> bool:
        return len(self.params) == 0
    
    def append(self, params: Union[CallSite, Tuple['AbstractObject', ...]]) -> 'ParamContext':
        """Create new context by appending parameters."""
        if self.depth == 0:
            return self
        if not isinstance(params, (CallSite, OrderedBindingToken)):
            if isinstance(params, frozenset):
                params = finite_context_atom(params)
            else:
                params = ordered_binding_token(params)
        new_params = (self.params + (params,))[-self.depth:]
        return ParamContext(new_params, self.depth)
    
    def __hash__(self) -> int:
        return hash((self.params, self.depth))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ParamContext):
            return False
        return self.params == other.params and self.depth == other.depth


@dataclass(frozen=True)
class HybridContext(AbstractContext[Tuple['CallSite', Optional['AbstractObject']]]):
    """Hybrid: Combine call-string + object sensitivity.
    
    Attributes:
        call_sites: Call sites
        alloc_sites: Allocation site IDs
        call_k: Maximum call-string length
        obj_depth: Maximum object context depth
    """
    
    call_sites: Tuple['CallSite', ...] = ()
    alloc_sites: Tuple['AbstractObject', ...] = ()
    call_k: int = 1
    obj_depth: int = 1
    
    def __post_init__(self):
        if any(type(value) is not int or value < 0 for value in (self.call_k, self.obj_depth)):
            raise ValueError("context depths must be nonnegative integers")
        values = tuple(finite_context_atom(value) for value in self.alloc_sites)
        object.__setattr__(self, "alloc_sites", values[-self.obj_depth:] if self.obj_depth else ())
        object.__setattr__(self, "call_sites", self.call_sites[-self.call_k:] if self.call_k else ())
        super().__post_init__()

    def to_string(self) -> str:
        call_part = "[" + ",".join(str(cs) for cs in self.call_sites) + "]" if self.call_sites else "[]"
        shortened = [str(s).split(':')[-1] for s in self.alloc_sites]
        obj_part = "<" + ",".join(shortened) + ">" if self.alloc_sites else "<>"
        return call_part + obj_part
    
    def is_empty(self) -> bool:
        return len(self.call_sites) == 0 and len(self.alloc_sites) == 0
    
    def append_call(self, call_site: CallSite) -> 'HybridContext':
        """Create new context by appending call site."""
        if self.call_k == 0:
            return self
        new_calls = (self.call_sites + (call_site,))[-self.call_k:]
        return HybridContext(new_calls, self.alloc_sites, self.call_k, self.obj_depth)
    
    def append_object(self, alloc_site: 'AbstractObject') -> 'HybridContext':
        """Create new context by appending allocation site."""
        if self.obj_depth == 0:
            return self
        new_allocs = (self.alloc_sites + (finite_context_atom(alloc_site),))[-self.obj_depth:]
        return HybridContext(self.call_sites, new_allocs, self.call_k, self.obj_depth)

    def append(self, call_site: 'CallSite', alloc_site: Optional['AbstractObject']) -> 'HybridContext':
        new_calls = ((self.call_sites + (call_site,))[-self.call_k:]
                     if self.call_k else ())
        new_allocs = ((self.alloc_sites + (finite_context_atom(alloc_site),))[-self.obj_depth:]
                      if self.obj_depth else ())
        return HybridContext(new_calls, new_allocs, self.call_k, self.obj_depth)
    
    def __hash__(self) -> int:
        return hash((self.call_sites, self.alloc_sites, self.call_k, self.obj_depth))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, HybridContext):
            return False
        return (self.call_sites == other.call_sites and 
                self.alloc_sites == other.alloc_sites and
                self.call_k == other.call_k and 
                self.obj_depth == other.obj_depth)


@dataclass(frozen=True)
class SummaryContext(AbstractContext[Any]):
    """Explicit marker for summary objects produced by widening."""

    inner: AbstractContext[Any]

    def to_string(self) -> str:
        return f"S[{self.inner.to_string()}]"

    def is_empty(self) -> bool:
        return self.inner.is_empty()

    def append(self, call_site: Any) -> 'SummaryContext':
        return SummaryContext(self.inner.append(call_site))

    def __hash__(self) -> int:
        return hash(("summary", self.inner))

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, SummaryContext):
            return False
        return self.inner == other.inner


T = TypeVar('T')
@dataclass(frozen=True)
class Ctx(Generic[T]):
    """Content with context.
    
    Attributes:
        context: AbstractContext[Any]
        content: T
    """
    context: 'AbstractContext[Any]'
    scope: 'Scope'
    content: T    
    
    def __hash__(self) -> int:
        return hash((self.content, self.scope, self.context))
    
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Ctx):
            return False
        return (self.content == other.content and
                self.context == other.context and self.scope == other.scope)
    
    def __str__(self) -> str:
        return f"{self.context}:{self.content}"


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
    obj: Union['FunctionObject', 'ClassObject', 'ModuleObject']
    context: 'AbstractContext'
    _parent: Optional['Scope']
    _module: Optional['Scope']
    
    def __post_init__(self):
        if not isinstance(self.stmt, IRScope):
            raise ValueError(f"Scope must be an IRScope, {self.stmt} got")
        if not isinstance(self.stmt, IRModule) and self._parent is None:
            raise ValueError("Parent is required for non-module scopes")
        if self._module is not None and not isinstance(self._module.stmt, IRModule):
            raise ValueError(f"Module shoud be IRModule, but got {type(self._module.stmt)}!")
    
    def __hash__(self) -> int:
        return hash((self.stmt, self.context, self.obj))
    
    def __eq__(self, other: 'Scope') -> bool:
        if not isinstance(other, Scope):
            return False
        return (self.stmt == other.stmt and self.context == other.context
                and self.obj == other.obj)
    
    def __str__(self) -> str:
        return f"Scope[{self.stmt}:<{self.obj}>@{self.context}]"

    @classmethod
    def new(cls, obj: 'AbstractObject', module: 'Scope', context: 'AbstractContext', stmt: IRScope, parent: Optional['Scope'] = None) -> 'Scope':
        if isinstance(stmt, IRModule) and parent is not None:
            parent = None
        return cls(stmt, obj, context, parent, module)

    @property
    def name(self) -> str:
        return self.stmt.get_qualname()
    
    @property
    def module(self) -> 'Scope':
        if self._module:
            return self._module
        else:
            return self
    
    @property
    def parent(self) -> 'Scope':
        if self._parent is None:
            if self._module is None:
                return self
            else:
                return self._module
        else:
            return self._parent

    @property
    def kind(self) -> Literal["function", "instance_method", "class_method", "static_method", "module", "class"]:
        from pythonstan.ir.ir_statements import IRFunc, IRClass, IRModule
        
        if isinstance(self.stmt, IRFunc):
            if self.stmt.is_instance_method:
                return "instance_method"
            elif self.stmt.is_class_method:
                return "class_method"
            elif self.stmt.is_static_method:
                return "static_method"
            else:
                return "function"
        elif isinstance(self.stmt, IRClass):
            return "class"
        elif isinstance(self.stmt, IRModule):
            return "module"
