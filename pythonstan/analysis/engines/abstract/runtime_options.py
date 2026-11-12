"""Decode the real source driver's owned runtime configuration once.

The public resolver owns immutable user configuration. These private copies
belong to one driver and preserve the existing solver-option precedence. They
are never published as configuration or shared with another execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    JsonValue,
    require_int,
    require_object,
    validate_json_object,
)
from pythonstan.analysis.domains.values import ValuePolicy


@dataclass(slots=True)
class RuntimeOptions:
    options: JsonObject
    abstract: JsonObject
    resources: JsonObject
    value_policy: ValuePolicy
    max_work: int
    max_seconds: float
    max_memory_mb: float
    context_depth: int
    max_paths: int


def _integer(value: JsonValue, name: str) -> int:
    """Keep the historical numeric conversion at the lower runtime boundary."""
    if isinstance(value, (int, float, str)):
        return int(value)
    raise TypeError(f"{name} must be convertible to an integer")


def _number(value: JsonValue, name: str) -> float:
    # The previous constructor used float(value or 0) for optional limits.
    if value is None or value is False or value == "":
        return 0.0
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"{name} must be convertible to a number")


def decode_runtime_options(options: Mapping[str, object] | None) -> RuntimeOptions:
    """Validate JSON and expose the exact fields consumed by driver construction.

    Policy capacities use the already documented integer contract. Invalid
    direct-driver capacities fail here rather than entering a partially typed
    ValuePolicy. Public configuration already checked those capacities.
    """
    document = validate_json_object({} if options is None else options)
    abstract = dict(require_object(document.get("abstract", {})))
    abstract.update(require_object(abstract.get("solver_options", {})))
    resources = dict(require_object(document.get("resources", {})))
    policy = ValuePolicy(
        max_const_strings=require_int(abstract.get("max_const_strings", 10)),
        max_const_ints=require_int(abstract.get("int_domain_max_consts", 10)),
        max_addr_set_size=require_int(abstract.get("max_addr_set_size", 50)),
        max_scalar_pairs=require_int(abstract.get("max_scalar_pairs", 256)),
    )
    return RuntimeOptions(
        options=document,
        abstract=abstract,
        resources=resources,
        value_policy=policy,
        max_work=_integer(
            resources.get("max_work", abstract.get("max_iterations", 100000)), "max_work"
        ),
        max_seconds=_number(resources.get("max_seconds", 0), "max_seconds"),
        max_memory_mb=_number(resources.get("max_memory_mb", 0), "max_memory_mb"),
        context_depth=_integer(abstract.get("context_depth", 2), "context_depth"),
        max_paths=max(1, _integer(abstract.get("max_traces_per_block", 8), "max_paths")),
    )


__all__ = ["RuntimeOptions", "decode_runtime_options"]


def integer_option(options: Mapping[str, JsonValue], name: str, default: int) -> int:
    """Read an actual driver option with its historical numeric conversion."""
    return _integer(options.get(name, default), name)
