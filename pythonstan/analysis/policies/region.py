"""Immutable resolved regional settings and run-local operation attachments.

The execution attachment shares the driver's identity arena. It owns no work,
time, memory or refinement counters: entering a region cannot renew a quota.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pythonstan.analysis.contracts.identity import stable_digest
from pythonstan.analysis.contracts.json_data import (
    FrozenJsonObject,
    JsonObject,
    freeze_json_object,
    require_int,
    require_object,
    thaw_json_object,
    validate_json_object,
)
from pythonstan.analysis.domains.values import ValueOperations, ValuePolicy


@dataclass(frozen=True, slots=True)
class EffectiveRegionPolicy:
    """An owned, canonical abstract policy compiled before execution."""

    _abstract: FrozenJsonObject
    digest: str
    value_policy: ValuePolicy

    @classmethod
    def from_abstract(cls, abstract: Mapping[str, object]) -> EffectiveRegionPolicy:
        settings = validate_json_object(abstract)
        settings.update(require_object(settings.pop("solver_options", {})))
        # The two public relation names denote one setting and one digest.
        if "enable_value_partition" in settings:
            settings["relations"] = settings["enable_value_partition"]
        elif "relations" in settings:
            settings["enable_value_partition"] = settings["relations"]
        # Regional aliases are canonicalized by config.resolve. The global
        # legacy default loop_unroll remains inert for the new core.
        settings.pop("loop_unroll", None)
        policy = ValuePolicy(
            max_const_strings=require_int(settings.get("max_const_strings", 10)),
            max_const_ints=require_int(settings.get("int_domain_max_consts", 10)),
            max_addr_set_size=require_int(settings.get("max_addr_set_size", 50)),
            max_scalar_pairs=require_int(settings.get("max_scalar_pairs", 256)),
        )
        return cls(freeze_json_object(settings), stable_digest(settings), policy)

    @property
    def abstract(self) -> JsonObject:
        return thaw_json_object(self._abstract)

    def integer(self, name: str, default: int) -> int:
        # Direct-driver construction historically accepts numeric strings;
        # the public resolver has already enforced the exact integer schema.
        value = self._abstract.get(name, default)
        if isinstance(value, (int, float, str)):
            return int(value)
        raise TypeError(f"{name} must be convertible to an integer")

    def boolean(self, name: str, default: bool) -> bool:
        value = self._abstract.get(name, default)
        if type(value) is not bool:
            raise TypeError(f"{name} must be boolean")
        return value

    @property
    def context_depth(self) -> int:
        return self.integer("context_depth", 2)

    @property
    def max_paths(self) -> int:
        return max(1, self.integer("max_traces_per_block", 8))

    def to_dict(self) -> JsonObject:
        return {"digest": self.digest, "abstract": self.abstract}


@dataclass(frozen=True, slots=True)
class RegionExecutionContext:
    """Policy plus operations; global resource authority stays with the driver."""

    policy: EffectiveRegionPolicy
    ops: ValueOperations

    @property
    def abstract(self) -> JsonObject:
        return self.policy.abstract

    def integer(self, name: str, default: int) -> int:
        return self.policy.integer(name, default)

    def boolean(self, name: str, default: bool) -> bool:
        return self.policy.boolean(name, default)

    @property
    def context_depth(self) -> int:
        return self.policy.context_depth

    @property
    def max_paths(self) -> int:
        return self.policy.max_paths


__all__ = ["EffectiveRegionPolicy", "RegionExecutionContext"]
