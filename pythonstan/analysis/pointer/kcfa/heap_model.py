"""Heap model and field abstraction for pointer analysis.

This module defines the abstraction of heap storage including field keys
for attribute access, container elements, and dictionary values.
"""

from collections.abc import ValuesView
from dataclasses import dataclass
from enum import Enum
from types import EllipsisType
from typing import TYPE_CHECKING, Optional, override

from pythonstan.analysis.state.keys import FieldKey as SourceFieldKey

from .variable import VariableKind

if TYPE_CHECKING:
    from pythonstan.ir.ir_statements import IRScope

    from .context import Ctx, NativeContext, Scope
    from .object import (
        AbstractObject,
        AllocationSource,
        AllocKind,
        AllocSite,
        ClassObject,
        FunctionObject,
    )
    from .variable import FieldAccess, Variable

__all__ = [
    "NativeLiteralKey",
    "NativeFieldKey",
    "FieldKind",
    "Field",
    "attr",
    "elem",
    "key",
    "value",
    "unknown",
    "wildcard_key",
    "HeapModel",
]

_FUNCTION_TEMPORARY_NAMES = frozenset({"$return"})


class FieldKind(Enum):
    """Kinds of heap fields."""

    ATTRIBUTE = "attr"
    ELEMENT = "elem"
    VALUE = "value"
    UNKNOWN = "unknown"
    POSITION = "position"
    KEY = "key"
    WILDCARD_KEY = "wildcard_key"
    CLASS_METADATA = "class_metadata"
    OWN_ATTRIBUTE = "own_attribute"
    OWN_UNKNOWN = "own_unknown"


def _field_kind_value(kind: FieldKind) -> str:
    value: object = kind.value
    if not isinstance(value, str):
        raise TypeError("Native field kind must have a string value")
    return value


@dataclass(frozen=True)
class Field:
    """Heap field key for object field access.

    Fields abstract over different kinds of object field access:
    - ATTRIBUTE: Named attribute access (obj.name)
    - ELEMENT: Container element access (list/set/tuple elements)
    - VALUE: Dictionary value access (dict values, key-insensitive)
    - UNKNOWN: Dynamic/unknown attribute access

    Attributes:
        kind: Type of field access
        name: Field name for ATTRIBUTE kind, None otherwise
    """

    kind: FieldKind
    name: str | None = None
    index: int | None = None
    key_type: str | None = None

    def __post_init__(self) -> None:
        """Validate field constraints."""
        if self.kind in {FieldKind.ATTRIBUTE, FieldKind.OWN_ATTRIBUTE} and self.name is None:
            raise ValueError("ATTRIBUTE field must have name")
        if self.kind == FieldKind.KEY and self.name is None:
            raise ValueError("KEY field must have name")
        if self.kind == FieldKind.POSITION and self.index is None:
            raise ValueError("POSITION field must have index")
        if self.kind in [
            FieldKind.ELEMENT,
            FieldKind.VALUE,
            FieldKind.UNKNOWN,
            FieldKind.WILDCARD_KEY,
            FieldKind.OWN_UNKNOWN,
        ] and (self.name is not None or self.index is not None):
            raise ValueError(f"{_field_kind_value(self.kind)} field should not have name or index")

    @override
    def __str__(self) -> str:
        """String representation for debugging."""
        if self.kind == FieldKind.ATTRIBUTE:
            return f".{self.name}"
        if self.kind == FieldKind.OWN_ATTRIBUTE:
            return f".own({self.name})"
        if self.kind == FieldKind.POSITION:
            return f"[{self.index}]"
        if self.kind == FieldKind.KEY:
            return f"['{self.name}']"
        value: object = self.kind.value
        return f".{value}"


type NativeLiteralKey = (
    str
    | bytes
    | int
    | float
    | bool
    | complex
    | EllipsisType
    | None
    | tuple[NativeLiteralKey, ...]
    | frozenset[NativeLiteralKey]
)

type NativeFieldKey = NativeLiteralKey | SourceFieldKey


def key(key_name: NativeFieldKey) -> Field:
    """Create key field for specific key/index access. Used for dictionary and list/tuple elements.

    Used for dict["key"] where key is statically known constant.

    Args:
        key_name: Dictionary key as string

    Returns:
        Field for specific key access
    """
    # Python's numeric keys share a hash/equality class: True, 1, and 1.0
    # select the same dictionary entry. Keep that class intact before encoding.
    key_type = None
    if isinstance(key_name, (bool, int)) or (isinstance(key_name, float) and key_name.is_integer()):
        key_type = "number"
        key_name = str(int(key_name))
    elif isinstance(key_name, complex) and key_name.imag == 0:
        return key(key_name.real)
    elif not isinstance(key_name, str):
        key_type = "number" if isinstance(key_name, (float, complex)) else type(key_name).__name__
        key_name = str(key_name)
    return Field(FieldKind.KEY, key_name, None, key_type=key_type)


def attr(name: str) -> Field:
    """Create attribute field key.

    Args:
        name: Attribute name

    Returns:
        Field for obj.name access

    Example:
        >>> attr("foo")
        Field(kind=FieldKind.ATTRIBUTE, name="foo")
    """
    return Field(FieldKind.ATTRIBUTE, name, None)


def elem() -> Field:
    """Create element field key for containers.

    Used for dict/list/tuple/set elements where we abstract over all indices.

    Returns:
        Field for generic container element access

    Example:
        >>> elem()
        Field(kind=FieldKind.ELEMENT, name=None, index=None)
    """
    return Field(FieldKind.ELEMENT, None, None)


def value() -> Field:
    """Create value field key for dictionaries.

    Used for dict values where we abstract over all keys.

    Returns:
        Field for generic dictionary value access

    Example:
        >>> value()
        Field(kind=FieldKind.VALUE, name=None, index=None)
    """
    return Field(FieldKind.VALUE, None, None)


def unknown() -> Field:
    """Create unknown field key for dynamic access.

    Used when attribute name cannot be determined statically.

    Returns:
        Field for unknown attribute access

    Example:
        >>> unknown()
        Field(kind=FieldKind.UNKNOWN, name=None)
    """
    return Field(FieldKind.UNKNOWN, None, None)


def wildcard_key() -> Field:
    """Dictionary writes whose key is not represented by a finite key field."""
    return Field(FieldKind.WILDCARD_KEY)


type HeapRegisterKey = (
    tuple[NativeContext | None, IRScope | AbstractObject] | tuple[IRScope] | tuple[Scope]
)

type HeapObjectKey = tuple[NativeContext | None, AllocationSource, AllocKind]
type CaptureOwner = FunctionObject | ClassObject


class HeapModel:
    """Heap model for pointer analysis.

    Maintains the heap and field accesses.
    """

    heap: "dict[HeapRegisterKey, dict[str, Ctx[Variable]]]"
    prev_scope: "dict[Scope, Scope]"
    objects: "dict[HeapObjectKey, AbstractObject]"
    cell_vars: "dict[CaptureOwner, dict[str, Ctx[Variable]]]"
    global_vars: "dict[CaptureOwner, dict[str, Ctx[Variable]]]"
    nonlocal_vars: "dict[CaptureOwner, dict[str, Ctx[Variable]]]"

    def __init__(self) -> None:
        self.heap = {}
        self.field_accesses: dict[tuple[AbstractObject, Field], FieldAccess] = {}
        self.objects = {}
        self.cell_vars = {}
        self.global_vars = {}
        self.nonlocal_vars = {}

    def get_variable(
        self, scope: "Scope", context: "NativeContext | None", var: "Variable"
    ) -> Optional["Ctx[Variable]"]:
        ctx_key = self._get_var_key(scope, context, var)

        registers = self.heap.get(ctx_key, {})
        return registers.get(var.name, None)

    def set_variable(
        self,
        scope: "Scope",
        context: "NativeContext | None",
        var: "Variable",
        ctx_var: "Ctx[Variable]",
    ) -> None:
        ctx_key = self._get_var_key(scope, context, var)

        registers = self.heap.get(ctx_key, None)
        if registers is None:
            registers = {}
            self.heap[ctx_key] = registers
        registers[var.name] = ctx_var  # TODO whether use context or scope.context?

    def _get_var_key(
        self, scope: "Scope", context: "NativeContext | None", var: "Variable"
    ) -> HeapRegisterKey:
        # if var.kind == VariableKind.GLOBAL:
        #     return (scope.module.stmt,)
        # return (context, scope.stmt)

        if var.kind == VariableKind.TEMPORARY:
            if var.name in _FUNCTION_TEMPORARY_NAMES:
                # Distinct call contexts must not exchange returned closures.
                return (context, scope.obj)
            return (context, scope.stmt)

        if var.kind == VariableKind.GLOBAL:
            return (scope.module.stmt,)
        return (context, scope.stmt)

    def get_all_variables(
        self, scope: "Scope", context: "NativeContext | None"
    ) -> "ValuesView[Ctx[Variable]]":
        ctx_key = (scope,)
        return self.heap.get(ctx_key, {}).values()

    def set_obj(
        self,
        scope: "Scope | None",
        context: "NativeContext | None",
        c: "AllocSite",
        o: "AbstractObject",
    ) -> None:
        # print(f"New object: {o}")
        self.objects[(context, c.stmt, c.kind)] = o

    def get_obj(
        self, scope: "Scope | None", context: "NativeContext | None", c: "AllocSite"
    ) -> Optional["AbstractObject"]:
        return self.objects.get((context, c.stmt, c.kind), None)

    def get_cell_vars(self, obj: "CaptureOwner") -> "dict[str, Ctx[Variable]]":
        return self.cell_vars.get(obj, {})

    def get_global_vars(self, obj: "CaptureOwner") -> "dict[str, Ctx[Variable]]":
        return self.global_vars.get(obj, {})

    def get_nonlocal_vars(self, obj: "CaptureOwner") -> "dict[str, Ctx[Variable]]":
        return self.nonlocal_vars.get(obj, {})
