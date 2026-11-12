"""Trusted builtin operations on already evaluated finite scalar operands."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import cmp_to_key

from pythonstan.analysis.contracts.json_data import JsonScalar


def scalar_conversion(name: str, arguments: Sequence[JsonScalar], keywords: Mapping[str, JsonScalar]) -> JsonScalar:
    if name == "str":
        if len(arguments) > 3 or any(key not in {"object", "encoding", "errors"} for key in keywords):
            raise TypeError("Invalid str arguments")
        if arguments and "object" in keywords:
            raise TypeError("Multiple values for str object")
        value = arguments[0] if arguments else keywords.get("object", "")
        encoding = arguments[1] if len(arguments) > 1 else keywords.get("encoding")
        errors = arguments[2] if len(arguments) > 2 else keywords.get("errors")
        if len(arguments) > 1 and "encoding" in keywords or len(arguments) > 2 and "errors" in keywords:
            raise TypeError("Multiple string decoding options")
        if (len(arguments) > 1 or "encoding" in keywords) and not isinstance(encoding, str) or (len(arguments) > 2 or "errors" in keywords) and not isinstance(errors, str):
            raise TypeError("String decoding options must be strings")
        if (len(arguments) > 1 or "encoding" in keywords or len(arguments) > 2 or "errors" in keywords) and (arguments or "object" in keywords):
            raise TypeError("Decoding a scalar requires a bytes buffer")
        return str(value)
    if name == "int":
        if len(arguments) > 2 or any(key != "base" for key in keywords):
            raise TypeError("Invalid int arguments")
        if len(arguments) > 1 and "base" in keywords:
            raise TypeError("Multiple values for int base")
        has_base = len(arguments) > 1 or "base" in keywords
        if not arguments:
            if has_base:
                raise TypeError("int requires an input with an explicit base")
            return 0
        value = arguments[0]
        if has_base:
            base = arguments[1] if len(arguments) > 1 else keywords["base"]
            if not isinstance(value, str) or not isinstance(base, int):
                raise TypeError("An explicit integer base requires text and an integer base")
            return int(value, base)
        if isinstance(value, (int, float, str)):
            return int(value)
        raise TypeError("Scalar cannot be converted to int")
    if name == "float":
        if keywords or len(arguments) > 1:
            raise TypeError("Invalid float arguments")
        if not arguments:
            return 0.0
        value = arguments[0]
        if isinstance(value, (int, float, str)):
            return float(value)
        raise TypeError("Scalar cannot be converted to float")
    if name == "repr":
        if keywords or len(arguments) != 1:
            raise TypeError("Invalid repr arguments")
        return repr(arguments[0])
    if name == "abs":
        if keywords or len(arguments) != 1:
            raise TypeError("Invalid abs arguments")
        value = arguments[0]
        if isinstance(value, (int, float)):
            return abs(value)
        raise TypeError("Scalar has no absolute value")
    raise ValueError("Unknown scalar conversion: " + name)


def scalar_range(arguments: Sequence[JsonScalar]) -> range:
    integers: list[int] = []
    for value in arguments:
        if not isinstance(value, int):
            raise TypeError("range arguments must be integers")
        integers.append(value)
    if len(integers) == 1:
        return range(integers[0])
    if len(integers) == 2:
        return range(integers[0], integers[1])
    if len(integers) == 3:
        return range(integers[0], integers[1], integers[2])
    raise TypeError("range requires one to three arguments")


def _less(left: JsonScalar, right: JsonScalar) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left < right
    if isinstance(left, str) and isinstance(right, str):
        return left < right
    raise TypeError("Scalars are not ordered")


def _compare(left: JsonScalar, right: JsonScalar) -> int:
    if _less(left, right):
        return -1
    return 1 if _less(right, left) else 0


def scalar_sort(values: Sequence[JsonScalar]) -> list[JsonScalar]:
    return sorted(values, key=cmp_to_key(_compare))


def scalar_aggregate(name: str, values: Sequence[JsonScalar], initial: JsonScalar = 0) -> JsonScalar:
    if name == "all":
        return all(values)
    if name == "any":
        return any(values)
    if name == "sum":
        if not isinstance(initial, (int, float)):
            raise TypeError("sum requires a numeric initial value")
        numbers: list[int | float] = []
        for value in values:
            if not isinstance(value, (int, float)):
                raise TypeError("sum requires numeric elements")
            numbers.append(value)
        return sum(numbers, initial)
    if name not in {"min", "max"}:
        raise ValueError("Unknown scalar aggregate: " + name)
    if not values:
        raise ValueError("An extremum requires a nonempty sequence")
    result = values[0]
    for value in values[1:]:
        replace = _less(value, result) if name == "min" else _less(result, value)
        if replace:
            result = value
    return result
