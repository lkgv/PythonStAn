"""Abstract objects and allocation sites for pointer analysis.

This module defines the representation of heap objects in the k-CFA pointer analysis.
Objects are context-sensitive and identified by their allocation site and context.
"""

import ast
from dataclasses import dataclass, replace
from enum import Enum
from types import EllipsisType
from typing import TYPE_CHECKING, Optional, Union, overload, override

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import IRClass, IRFunc, IRModule, IRScope, IRStatement

    from .context import (
        AbstractContext,
        CallSite,
        NativeContext,
        Scope,
    )
    from .heap_model import Field
    from .variable import Variable

__all__ = [
    "AllocationSource",
    "NativeConstantValue",
    "AllocKind",
    "AllocSite",
    "AbstractObject",
    "FunctionObject",
    "ConstantObject",
    "UnknownValue",
    "ClassObject",
    "ModuleObject",
    "InstanceObject",
    "MethodObject",
    "BuiltinObject",
    "ListObject",
    "TupleObject",
    "MappingItemObject",
    "DictObject",
    "SetObject",
    "BuiltinClassObject",
    "BuiltinInstanceObject",
    "BuiltinMethodObject",
    "BuiltinFunctionObject",
    "SuperObject",
    "GeneratorObject",
    "CoroutineObject",
    "AsyncGeneratorObject",
    "ObjectFactory",
    "truncate_context",
    "summarize_object",
    "is_summary_object",
]


type AllocationSource = (
    IRStatement
    | IRScope
    | None
    | str
    | int
    | float
    | CallSite
    | AbstractObject
    | Scope
    | Field
    | tuple[AllocationSource, ...]
)

type NativeConstantValue = str | bytes | int | float | complex | bool | EllipsisType | None


class AllocKind(Enum):
    """Types of allocations in Python programs."""

    OBJECT = "obj"
    LIST = "list"
    TUPLE = "tuple"
    DICT = "dict"
    SET = "set"
    BOOLEAN = "bool"
    INTEGER = "int"
    FLOAT = "float"
    STRING = "str"
    FUNCTION = "func"
    METHOD = "method"
    CLASS = "class"
    INSTANCE = "instance"
    MODULE = "module"
    BOUND_METHOD = METHOD
    BUILTIN = "builtin"
    CELL = "cell"
    CONSTANT = "constant"
    GENERATOR = "generator"
    COROUTINE = "coroutine"
    ASYNC_GENERATOR = "async_generator"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class AllocSite:
    """Allocation site with source location information.

    An allocation site represents a program location where an object is created.
    This forms the static part of an abstract object's identity.

    Attributes:
        file: Source file name
        line: Line number
        col: Column offset
        kind: Type of allocation
        name: Optional name for named allocations (functions, classes)
    """

    stmt: AllocationSource
    kind: AllocKind

    def __post_init__(self) -> None:
        from pythonstan.ir.ir_statements import IRStatement

        assert isinstance(self.kind, AllocKind), (
            f"kind must be an AllocKind, but got {type(self.kind)}"
        )
        # For user-defined objects, stmt should be IRStatement
        # For builtin objects and instances, stmt can be a string identifier
        # INSTANCE allocations use call site strings for context sensitivity
        if self.kind in (AllocKind.CLASS, AllocKind.FUNCTION, AllocKind.METHOD):
            assert isinstance(self.stmt, IRStatement), (
                f"stmt must be an IRStatement, but got {type(self.stmt)}"
            )

    @override
    def __str__(self) -> str:
        from pythonstan.ir.ir_statements import IRStatement

        """String representation for debugging and display."""
        if (
            self.kind == AllocKind.FUNCTION
            or self.kind == AllocKind.METHOD
            or self.kind == AllocKind.CLASS
        ):
            assert isinstance(self.stmt, IRStatement), (
                f"stmt must be an IRStatement, but got {type(self.stmt)}"
            )
        return f"{self.stmt}@{self.kind}"

    @property
    def line(self) -> int:
        from pythonstan.frontend.source import source_position
        from pythonstan.ir.ir_statements import IRStatement

        return source_position(self.stmt.get_ast())[0] if isinstance(self.stmt, IRStatement) else 0

    @property
    def col(self) -> int:
        from pythonstan.frontend.source import source_position
        from pythonstan.ir.ir_statements import IRStatement

        return source_position(self.stmt.get_ast())[1] if isinstance(self.stmt, IRStatement) else 0

    def require_function_source(self) -> "IRFunc":
        """Read the actual allocation statement under the function-site invariant."""
        from pythonstan.ir.ir_statements import IRFunc

        if not isinstance(self.stmt, IRFunc):
            raise TypeError("Function allocation source must be IRFunc")
        return self.stmt

    def require_class_source(self) -> "IRClass":
        """Read the actual allocation statement under the class-site invariant."""
        from pythonstan.ir.ir_statements import IRClass

        if not isinstance(self.stmt, IRClass):
            raise TypeError("Class allocation source must be IRClass")
        return self.stmt

    @staticmethod
    def from_ir_node(stmt: "IRStatement", kind: AllocKind) -> "AllocSite":
        """Create allocation site from IR node.
        Extract source location from IR node.
        IR nodes typically have file, line, col attributes.

        Args:
            stmt: IR node
            kind: Allocation kind

        Returns:
            AllocSite extracted from node
        """

        from pythonstan.ir.ir_statements import IRStatement

        assert isinstance(stmt, (IRStatement,)), (
            f"stmt must be an IRStatement, but got {type(stmt)}"
        )
        assert isinstance(kind, AllocKind), f"kind must be an AllocKind, but got {type(kind)}"
        if kind == AllocKind.FUNCTION or kind == AllocKind.METHOD or kind == AllocKind.CLASS:
            assert isinstance(stmt, IRStatement), (
                f"stmt must be an IRStatement, but got {type(stmt)}"
            )

        return AllocSite(stmt, kind)


@dataclass(frozen=True)
class AbstractObject:
    """Abstract heap object with context sensitivity.

    An abstract object represents a set of concrete runtime objects that share
    the same allocation site and context. The context enables context-sensitive
    analysis by distinguishing objects allocated at the same site in different
    calling contexts.

    Attributes:
        scope: container scope
        context: Analysis context (dynamic identity)
        alloc_site: Allocation site (static identity)
    """

    context: "NativeContext | None"
    alloc_site: AllocSite

    @override
    def __str__(self) -> str:
        """String representation showing site and context."""
        return f"{self.alloc_site}@{self.context}"

    @property
    def kind(self) -> AllocKind:
        """Get allocation kind from site."""
        return self.alloc_site.kind

    @property
    def is_callable(self) -> bool:
        """Check if object is callable (function, class, bound method)."""
        return self.kind in (
            AllocKind.FUNCTION,
            AllocKind.CLASS,
            AllocKind.BOUND_METHOD,
            AllocKind.BUILTIN,
        )

    def get_type(self) -> AllocSite:
        """Get the type of the object."""
        return self.alloc_site


@dataclass(frozen=True)
class UnknownValue(AbstractObject):
    """Explicit value top at an arbitrary external semantic boundary.

    Its concretization contains scalar values and references, not merely one
    named opaque object. Native field/call consumers preserve it; AbsVal bridge
    recognizes ``is_top_value`` and constructs the full value-domain top.
    """

    @property
    def is_top_value(self) -> bool:
        return True


@dataclass(frozen=True)
class FunctionObject(AbstractObject):
    """Function object with context sensitivity."""

    container_scope: "Scope"
    ir: "IRFunc"


@dataclass(frozen=True)
class MethodObject(FunctionObject):
    class_obj: "ClassObject"
    instance_obj: "InstanceObject | ClassObject | None"

    def deliver_into(self, inst: "InstanceObject | ClassObject") -> "MethodObject":
        return MethodObject(
            self.context, self.alloc_site, self.container_scope, self.ir, self.class_obj, inst
        )

    def inherit_into(self, cls_obj: "ClassObject") -> "MethodObject":
        return MethodObject(
            self.context, self.alloc_site, self.container_scope, self.ir, cls_obj, None
        )


# TODO Add some types of objects


@dataclass(frozen=True)
class ClassObject(AbstractObject):
    container_scope: "Scope"
    ir: "IRClass"


@dataclass(frozen=True)
class ModuleObject(AbstractObject):
    ir: "IRModule"


@dataclass(frozen=True)
class InstanceObject(AbstractObject):
    class_obj: "ClassObject"


@dataclass(frozen=True)
class ConstantObject(AbstractObject):
    value: NativeConstantValue


@dataclass(frozen=True)
class BuiltinObject(AbstractObject):
    """Builtin object (e.g., built-in functions)."""


@dataclass(frozen=True)
class ListObject(AbstractObject):
    """List object with mutable elements tracked via elem() field."""


@dataclass(frozen=True)
class TupleObject(AbstractObject):
    """Tuple object with immutable elements tracked via position(i) fields."""


@dataclass(frozen=True)
class MappingItemObject(TupleObject):
    """An exact two-slot mapping item; its aggregate is not either slot."""


@dataclass(frozen=True)
class DictObject(AbstractObject):
    """Dictionary object with values tracked via key(k) and value() fields."""


@dataclass(frozen=True)
class SetObject(AbstractObject):
    """Set object with elements tracked via elem() field."""


@dataclass(frozen=True)
class BuiltinClassObject(AbstractObject):
    """Builtin class object (e.g., list, dict, str types).

    Represents the builtin type objects themselves, not instances.
    Used for calls like `list()`, `dict()`, etc.
    """

    builtin_name: str  # Name of the builtin (e.g., "list", "dict", "str")

    @override
    def __str__(self) -> str:
        return f"<builtin_class '{self.builtin_name}' at {self.alloc_site}>"


@dataclass(frozen=True)
class BuiltinInstanceObject(AbstractObject):
    """Builtin instance object (e.g., list instance, dict instance).

    Represents instances of builtin types with specific field semantics.
    """

    builtin_type: str  # Type name (e.g., "list", "dict", "str")

    @override
    def __str__(self) -> str:
        return f"<builtin_instance of '{self.builtin_type}' at {self.alloc_site}>"


@dataclass(frozen=True)
class BuiltinMethodObject(AbstractObject):
    """Builtin method bound to an instance.

    Represents methods like list.append, dict.get, etc.
    """

    method_name: str  # Method name (e.g., "append", "get")
    receiver: "AbstractObject"  # The object this method is bound to
    receiver_var: Optional["Variable"] = (
        None  # The variable holding the receiver (for constraint generation)
    )

    @override
    def __str__(self) -> str:
        return f"<builtin_method '{self.method_name}' of {self.receiver}>"


@dataclass(frozen=True)
class BuiltinFunctionObject(AbstractObject):
    """Builtin function object (e.g., len, isinstance, iter).

    Represents standalone builtin functions, not methods.
    """

    function_name: str  # Function name (e.g., "len", "iter", "sorted")

    @override
    def __str__(self) -> str:
        return f"<builtin_function '{self.function_name}'>"


@dataclass(frozen=True)
class SuperObject(AbstractObject):
    """Super proxy object for MRO-based method resolution.

    Represents the result of super() calls. Field access on SuperObject
    resolves to parent class fields using MRO and InheritanceConstraint.

    Key behaviors:
    - Field access triggers InheritanceConstraint for parent classes
    - Methods accessed are bound to instance_obj if present
    - Uses SelectorNode + PFG edges for lazy parent field resolution

    Attributes:
        current_class: Class context where super() was called (determines MRO position)
        instance_obj: Instance for method binding (None for unbound super)
    """

    current_class: Optional["ClassObject"]  # Class to skip in MRO lookup
    instance_obj: Optional["AbstractObject"]  # Instance for binding methods

    @override
    def __str__(self) -> str:
        if self.current_class:
            return f"<super of {self.current_class}>"
        return f"<super at {self.alloc_site}>"


@dataclass(frozen=True)
class GeneratorObject(AbstractObject):
    """Generator object returned from calling a generator function.

    Represents the generator iterator produced when calling a function
    containing yield statements. Yielded values flow to the elem() field,
    enabling iteration via iter()/next() builtin handling.

    Key behaviors:
    - Calling a generator function returns GeneratorObject (not function result)
    - IRYield stores values to this object's elem() field
    - next()/iter() loads from elem() field (existing builtin handling)
    - send() values flow to yield expression targets (future enhancement)

    Attributes:
        func_obj: The FunctionObject that defines this generator
        container_scope: Scope where the generator function is defined
    """

    func_obj: "FunctionObject"
    container_scope: "Scope"

    @override
    def __str__(self) -> str:
        try:
            func_name = self.func_obj.ir.name
        except AttributeError:
            func_name = "<unknown>"
        return f"<generator object {func_name} at {self.alloc_site}>"


@dataclass(frozen=True)
class AsyncGeneratorObject(AbstractObject):
    """Dormant asynchronous iterator, resumed through an awaited advance.

    This is deliberately neither a synchronous GeneratorObject nor a
    CoroutineObject: next(generator) and await(generator) are invalid drives.
    Yielded values use elem(), while each __anext__/asend result has its own
    lazy awaitable protocol relation to this object.
    """

    func_obj: "FunctionObject"
    container_scope: "Scope"


@dataclass(frozen=True)
class CoroutineObject(AbstractObject):
    """Coroutine object returned from calling an async function.

    Represents the coroutine produced when calling an async def function.
    The coroutine's result flows through the $await_result field, enabling
    await expressions to retrieve the final value.

    Key behaviors:
    - Calling an async function returns CoroutineObject (not function result)
    - IRReturn in async function stores to $await_result field
    - IRAwait loads from the coroutine's $await_result field

    Attributes:
        func_obj: The FunctionObject that defines this coroutine
        container_scope: Scope where the async function is defined
    """

    func_obj: "FunctionObject"
    container_scope: "Scope"

    @override
    def __str__(self) -> str:
        try:
            func_name = self.func_obj.ir.name
        except AttributeError:
            func_name = "<unknown>"
        return f"<coroutine object {func_name} at {self.alloc_site}>"


class ObjectFactory:
    """Factory for creating abstract objects with proper context sensitivity.

    Provides convenient methods for creating various kinds of abstract objects
    including builtin objects that require special handling.
    """

    def __init__(self) -> None:
        """Initialize object factory."""

    @staticmethod
    def create_builtin_class(
        builtin_name: str, context: "NativeContext | None"
    ) -> BuiltinClassObject:
        """Create a builtin class object.

        Args:
            builtin_name: Name of builtin type (e.g., "list", "dict")
            context: Analysis context

        Returns:
            BuiltinClassObject for the specified builtin
        """
        from pythonstan.ir.ir_statements import IRAssign

        assign = ast.Assign(
            targets=[ast.Name(id=f"$builtin_class_{builtin_name}", ctx=ast.Store())],
            value=ast.Constant(value=None),
        )
        alloc_site = AllocSite.from_ir_node(IRAssign(assign), AllocKind.CLASS)
        return BuiltinClassObject(context=context, alloc_site=alloc_site, builtin_name=builtin_name)

    @staticmethod
    def create_builtin_instance(
        builtin_type: str, context: "NativeContext | None", stmt: Union[str, "IRStatement"]
    ) -> BuiltinInstanceObject:
        """Create a builtin instance object.

        Args:
            builtin_type: Type of builtin (e.g., "list", "dict")
            context: Analysis context
            stmt: IR statement or synthetic identifier for allocation site

        Returns:
            BuiltinInstanceObject for the specified type
        """
        # Map builtin type name to AllocKind
        kind_map = {
            "list": AllocKind.LIST,
            "dict": AllocKind.DICT,
            "tuple": AllocKind.TUPLE,
            "set": AllocKind.SET,
        }
        kind = kind_map.get(builtin_type, AllocKind.OBJECT)

        alloc_site = AllocSite(stmt=stmt, kind=kind)
        return BuiltinInstanceObject(
            context=context, alloc_site=alloc_site, builtin_type=builtin_type
        )

    @staticmethod
    def create_builtin_method(
        method_name: str, receiver: "AbstractObject", context: "NativeContext | None"
    ) -> BuiltinMethodObject:
        """Create a builtin method object bound to a receiver.

        Args:
            method_name: Name of method (e.g., "append", "get")
            receiver: Object this method is bound to
            context: Analysis context

        Returns:
            BuiltinMethodObject bound to the receiver
        """
        alloc_site = AllocSite(stmt=f"<builtin_method:{method_name}>", kind=AllocKind.BUILTIN)
        return BuiltinMethodObject(
            context=context, alloc_site=alloc_site, method_name=method_name, receiver=receiver
        )

    @staticmethod
    def create_builtin_function(
        function_name: str, context: "NativeContext | None"
    ) -> BuiltinFunctionObject:
        """Create a builtin function object.

        Args:
            function_name: Name of function (e.g., "len", "iter", "sorted")
            context: Analysis context

        Returns:
            BuiltinFunctionObject for the specified function
        """
        alloc_site = AllocSite(stmt=f"<builtin_function:{function_name}>", kind=AllocKind.BUILTIN)
        return BuiltinFunctionObject(
            context=context, alloc_site=alloc_site, function_name=function_name
        )

    @staticmethod
    def create_list(
        context: "NativeContext | None", stmt: Union[str, "IRStatement"]
    ) -> "ListObject":
        """Create a list object.

        Args:
            context: Analysis context
            stmt: IR statement for allocation site

        Returns:
            ListObject
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.LIST)
        return ListObject(context=context, alloc_site=alloc_site)

    @staticmethod
    def create_dict(
        context: "NativeContext | None", stmt: Union[str, "IRStatement"]
    ) -> "DictObject":
        """Create a dict object.

        Args:
            context: Analysis context
            stmt: IR statement for allocation site

        Returns:
            DictObject
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.DICT)
        return DictObject(context=context, alloc_site=alloc_site)

    @staticmethod
    def create_tuple(
        context: "NativeContext | None", stmt: Union[str, "IRStatement"]
    ) -> "TupleObject":
        """Create a tuple object.

        Args:
            context: Analysis context
            stmt: IR statement for allocation site

        Returns:
            TupleObject
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.TUPLE)
        return TupleObject(context=context, alloc_site=alloc_site)

    @staticmethod
    def create_set(context: "NativeContext | None", stmt: Union[str, "IRStatement"]) -> "SetObject":
        """Create a set object.

        Args:
            context: Analysis context
            stmt: IR statement for allocation site

        Returns:
            SetObject
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.SET)
        return SetObject(context=context, alloc_site=alloc_site)

    @staticmethod
    def create_super(
        context: "NativeContext | None",
        stmt: AllocationSource,
        current_class: Optional["ClassObject"] = None,
        instance_obj: Optional["AbstractObject"] = None,
    ) -> "SuperObject":
        """Create a super proxy object.

        Args:
            context: Analysis context
            stmt: IR statement or identifier for allocation site
            current_class: Class context for MRO lookup (None = unresolved)
            instance_obj: Instance for method binding (None = unbound)

        Returns:
            SuperObject for parent class access
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.OBJECT)
        return SuperObject(
            context=context,
            alloc_site=alloc_site,
            current_class=current_class,
            instance_obj=instance_obj,
        )

    @staticmethod
    def create_generator(
        context: "NativeContext | None",
        stmt: Union[str, "IRStatement"],
        func_obj: "FunctionObject",
        container_scope: "Scope",
    ) -> "GeneratorObject":
        """Create a generator object.

        Args:
            context: Analysis context
            stmt: IR statement or call site for allocation site
            func_obj: The generator function object
            container_scope: Scope where the generator function is defined

        Returns:
            GeneratorObject representing the generator iterator
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.GENERATOR)
        return GeneratorObject(
            context=context,
            alloc_site=alloc_site,
            func_obj=func_obj,
            container_scope=container_scope,
        )

    @staticmethod
    def create_coroutine(
        context: "NativeContext | None",
        stmt: Union[str, "IRStatement"],
        func_obj: "FunctionObject",
        container_scope: "Scope",
    ) -> "CoroutineObject":
        """Create a coroutine object.

        Args:
            context: Analysis context
            stmt: IR statement or call site for allocation site
            func_obj: The async function object
            container_scope: Scope where the async function is defined

        Returns:
            CoroutineObject representing the coroutine
        """
        alloc_site = AllocSite(stmt=stmt, kind=AllocKind.COROUTINE)
        return CoroutineObject(
            context=context,
            alloc_site=alloc_site,
            func_obj=func_obj,
            container_scope=container_scope,
        )


# =============================================================================
# Summary helpers (context truncation + summary objects)
# =============================================================================


@overload
def truncate_context[InputT](ctx: "AbstractContext[InputT]") -> "AbstractContext[InputT]": ...


@overload
def truncate_context(ctx: None) -> None: ...


def truncate_context[InputT](
    ctx: "AbstractContext[InputT] | None",
) -> "AbstractContext[InputT] | None":
    """Truncate known histories while retaining general-purpose wrapper behavior."""
    from .context import SummaryContext, is_summary_context
    from .protocol_context import ProtocolKeyContext, is_protocol_context

    if ctx is None:
        return None
    if is_summary_context(ctx):
        inner = ctx.inner
        inner_truncated = truncate_context(inner)
        if inner_truncated == inner:
            return ctx
        return SummaryContext(inner_truncated)
    if is_protocol_context(ctx):
        return ProtocolKeyContext(truncate_context(ctx.inner), ctx.partition)
    return _truncate_context_leaf(ctx)


def _truncate_context_leaf[ContextT](ctx: ContextT) -> ContextT:
    """Dataclass replacement preserves each concrete registered leaf class."""
    from .context import (
        CallStringContext,
        HybridContext,
        ObjectContext,
        ParamContext,
        ReceiverContext,
        TypeContext,
    )

    if isinstance(ctx, CallStringContext):
        return replace(ctx, call_sites=(), k=0)
    if isinstance(ctx, ObjectContext):
        return replace(ctx, alloc_sites=(), depth=0)
    if isinstance(ctx, TypeContext):
        return replace(ctx, types=(), depth=0)
    if isinstance(ctx, ReceiverContext):
        return replace(ctx, receivers=(), depth=0)
    if isinstance(ctx, ParamContext):
        return replace(ctx, params=(), depth=0)
    if isinstance(ctx, HybridContext):
        return replace(ctx, call_sites=(), alloc_sites=(), call_k=0, obj_depth=0)
    return ctx


def summarize_object(obj: "AbstractObject") -> "AbstractObject":
    """Rebuild the real summary wrapper, retaining nullable context identity."""
    from .context import SummaryContext

    base_ctx = obj.context
    if isinstance(base_ctx, SummaryContext):
        inner = base_ctx.inner
        inner_truncated = truncate_context(inner)
        if inner_truncated == inner:
            return obj
        return replace(obj, context=SummaryContext(inner_truncated))
    return replace(obj, context=SummaryContext(truncate_context(base_ctx)))


def is_summary_object(obj: "AbstractObject") -> bool:
    """Check if object context is explicitly marked as a summary."""
    try:
        from .context import SummaryContext
    except Exception:
        return False
    return isinstance(obj.context, SummaryContext)
