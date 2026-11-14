"""Finite Python 3.12 builtin type identities used by class-relation queries.

This declaration models nominal parents, not constructor or operator support.
References: Python 3.12 library/functions.html and library/exceptions.html.
"""

from __future__ import annotations

from .exceptions import (
    EXCEPTION_ALIASES,
    EXCEPTION_NAMES,
    EXCEPTION_PARENTS,
    require_builtin_exception_name,
)

TYPECLASS_MODEL_VERSION = "python-3.12-classinfo-v1"

PLAIN_BUILTIN_TYPES: frozenset[str] = frozenset(
    {
        "object",
        "type",
        "bool",
        "int",
        "float",
        "complex",
        "str",
        "bytes",
        "bytearray",
        "memoryview",
        "list",
        "tuple",
        "dict",
        "set",
        "frozenset",
        "range",
        "slice",
        "property",
        "classmethod",
        "staticmethod",
        "super",
        "enumerate",
        "map",
        "filter",
        "zip",
        "reversed",
    }
)
BUILTIN_TYPE_NAMES: frozenset[str] = PLAIN_BUILTIN_TYPES | EXCEPTION_NAMES


def canonical_builtin_type(name: str) -> str:
    return EXCEPTION_ALIASES.get(name, name)


def builtin_type_ancestors(name: str) -> frozenset[str]:
    """Return the finite nominal closure including the named class itself."""
    if name not in BUILTIN_TYPE_NAMES:
        return frozenset()
    pending = [canonical_builtin_type(name)]
    result: set[str] = set()
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        if current in EXCEPTION_NAMES:
            pending.extend(EXCEPTION_PARENTS[require_builtin_exception_name(current)])
            if current == "BaseException":
                pending.append("object")
        elif current == "bool":
            pending.append("int")
        elif current != "object":
            pending.append("object")
    return frozenset(result)
