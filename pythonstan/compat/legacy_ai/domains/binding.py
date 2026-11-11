"""Callable/descriptor binding semantics for abstract interpretation.

This module contains cross-cutting glue for:
- Extracting callable targets from abstract values
- Binding descriptors/methods to receivers

These were moved out of value_domain.py to keep that module focused on
the AbsVal reduced product and basic domain operations.
"""

from typing import TYPE_CHECKING, FrozenSet, Optional, Set

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject
    from pythonstan.compat.legacy_ai.value_domain import AbsVal, CallableTarget

__all__ = [
    "to_callable_targets",
    "bind_descriptor",
]

# Domain name constants (avoid circular import)
CALLABLE_DOMAIN = "callable"


def to_callable_targets(val: "AbsVal") -> FrozenSet["CallableTarget"]:
    """Extract callable targets from a value.

    Consults:
    1. Address set (base)
    2. Callable domain

    Args:
        val: Abstract value to extract callables from

    Returns:
        Frozenset of callable targets for dispatch
    """
    from pythonstan.compat.legacy_ai.value_domain import CallableTarget

    targets: Set[CallableTarget] = {CallableTarget(obj) for obj in val.addrs}

    # Consult callable domain
    callable_elem = val.components.get(CALLABLE_DOMAIN)
    if callable_elem is not None and hasattr(callable_elem, "to_callable_targets"):
        targets.update(callable_elem.to_callable_targets())

    return frozenset(targets)


def bind_descriptor(
    owner: "AbstractObject",
    attr_value: "AbsVal",
    receiver: Optional["AbsVal"],
) -> "AbsVal":
    """Bind descriptor or method values to a receiver when possible.

    Delegates binding to the callable domain.

    Args:
        owner: The class or object that owns the attribute
        attr_value: The attribute value (may contain callables)
        receiver: Optional receiver value (self/cls binding)

    Returns:
        AbsVal with callable domain elements bound to the receiver
    """
    if receiver is None:
        return attr_value

    result = attr_value

    # Bind in callable domain
    callable_elem = result.components.get(CALLABLE_DOMAIN)
    if callable_elem is not None and hasattr(callable_elem, "bind_receiver"):
        bound_callable = callable_elem.bind_receiver(owner, receiver)
        result = result.with_component(CALLABLE_DOMAIN, bound_callable)

    return result
