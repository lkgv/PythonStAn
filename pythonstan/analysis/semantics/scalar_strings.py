"""Finite trusted string operations on already checked primitive call inputs.

Only names exposed by the scalar attribute model are dispatched. Target
objects, descriptors and arbitrary callables cannot enter this service.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from pythonstan.analysis.contracts.json_data import JsonScalar

type StringMethodResult = str | bool | list[str]


class UnsupportedStringMethod(Enum):
    UNSUPPORTED = "unsupported_string_method"


def _arity(arguments: tuple[JsonScalar, ...], minimum: int, maximum: int) -> None:
    if not minimum <= len(arguments) <= maximum:
        raise TypeError("Invalid number of string method arguments")


def _no_keywords(keywords: Mapping[str, JsonScalar]) -> None:
    if keywords:
        raise TypeError("This string method takes no keyword arguments")


def _text(value: JsonScalar) -> str:
    if not isinstance(value, str):
        raise TypeError("String method argument must be a string")
    return value


def _optional_text(value: JsonScalar) -> str | None:
    return None if value is None else _text(value)


def _integer(value: JsonScalar) -> int:
    if not isinstance(value, int):
        raise TypeError("String method argument must be an integer")
    return value


def _slice_bound(value: JsonScalar) -> int | None:
    return None if value is None else _integer(value)


def apply_string_method(
    receiver: str,
    name: str,
    args: tuple[JsonScalar, ...],
    keywords: Mapping[str, JsonScalar],
) -> StringMethodResult | UnsupportedStringMethod:
    """Preserve builtin results and exception classes for the nine exposed names.

    Argument errors remain target TypeErrors. Builtin format/split errors are
    allowed to propagate with their existing classes; the caller owns the
    abstract exception policy. Unsupported names are distinct from any result.
    """
    if name == "format":
        return receiver.format(*args, **keywords)
    if name == "split":
        _arity(args, 0, 2)
        if any(key not in {"sep", "maxsplit"} for key in keywords):
            raise TypeError("Unexpected split keyword argument")
        if (args and "sep" in keywords) or (len(args) == 2 and "maxsplit" in keywords):
            raise TypeError("Multiple values for split argument")
        separator = args[0] if args else keywords.get("sep")
        maximum = args[1] if len(args) == 2 else keywords.get("maxsplit", -1)
        return receiver.split(_optional_text(separator), _integer(maximum))
    if name in {"upper", "lower"}:
        _no_keywords(keywords)
        _arity(args, 0, 0)
        return receiver.upper() if name == "upper" else receiver.lower()
    if name in {"startswith", "endswith"}:
        _no_keywords(keywords)
        _arity(args, 1, 3)
        prefix = _text(args[0])
        start = _slice_bound(args[1]) if len(args) > 1 else 0
        end = _slice_bound(args[2]) if len(args) > 2 else None
        return (
            receiver.startswith(prefix, start, end)
            if name == "startswith"
            else receiver.endswith(prefix, start, end)
        )
    if name == "strip":
        _no_keywords(keywords)
        _arity(args, 0, 1)
        return receiver.strip(_optional_text(args[0]) if args else None)
    if name == "join":
        _no_keywords(keywords)
        _arity(args, 1, 1)
        return receiver.join(_text(args[0]))
    if name == "replace":
        _no_keywords(keywords)
        _arity(args, 2, 3)
        count = _integer(args[2]) if len(args) == 3 else -1
        return receiver.replace(_text(args[0]), _text(args[1]), count)
    return UnsupportedStringMethod.UNSUPPORTED
