"""Validated configuration for the independent AI pass.

Budget options have a single projection into AnalysisBudget. Solver-only options
control partitioning and widening; overlapping names are rejected to avoid two
conflicting authorities for the same bound.
"""

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, override

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.ai.solver import SolverConfig

from pythonstan.analysis.contracts.budgets import AnalysisBudget, validate_budget_options

__all__ = ["Config", "validate_budget_options"]


@dataclass(frozen=True)
class Config:
    pta_analysis_name: str = "kcfa"
    plan_scopes: list[str] | None = None
    entry_scope: str | None = None
    # This pass consumes existing PTA addresses and contexts. It cannot change
    # the abstraction of those facts by choosing another policy after the fact.
    context_policy: str = "inherit"
    log_level: str = "INFO"
    max_iterations: int = 100
    max_stmt_visits: int = 50
    max_recursion_depth: int = 3
    strict_exceptions: bool = False
    max_addr_set_size: int = 50
    max_field_keys: int = 20
    max_const_strings: int = 10
    max_ho_tokens: int = 20
    enable_narrowing: bool = True
    narrowing_iterations: int = 2
    enable_value_partition: bool = True
    max_value_partitions: int = 4
    value_partition_str_cap: int = 4
    max_const_ints: int = 4
    max_tuple_len_exact: int = 8
    max_tuple_alts: int = 4
    max_star_expand_len: int = 8
    max_star_expand_alts: int = 4
    max_dict_parts: int = 20
    max_dict_key_const_strs: int = 10
    max_dict_key_const_ints: int = 10
    max_dict_key_tuple_len: int = 4
    max_dict_key_tuple_alts: int = 2
    enable_dict_identity_keys: bool = True
    max_dict_identity_keys: int = 10
    solver_options: dict[str, object] = field(default_factory=dict)
    type: str = "ai analysis"

    @classmethod
    def from_dict(cls, config_dict: Mapping[str, object] | Mapping[object, object]) -> "Config":
        if type(config_dict) is not dict:
            raise ValueError("AI configuration must be a dictionary")
        unknown = set(config_dict) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown AI options: {sorted(unknown)}")
        validated_config: dict[str, object] = {}
        for name, value in config_dict.items():
            if not isinstance(name, str):
                raise ValueError("AI option names must be strings")
            validated_config[name] = value
        config_dict = validated_config
        budget_names = AnalysisBudget().to_dict().keys()
        budget = AnalysisBudget.from_options(
            {name: value for name, value in config_dict.items() if name in budget_names}
        )

        def text(name: str, default: str, message: str) -> str:
            value = config_dict.get(name, default)
            if type(value) is not str:
                raise ValueError(message)
            return value

        entry = config_dict.get("entry_scope")
        if entry is not None and (type(entry) is not str or not entry):
            raise ValueError("entry_scope must be nonempty")
        raw_scopes = config_dict.get("plan_scopes")
        scopes: list[str] | None = None
        if raw_scopes is not None:
            if type(raw_scopes) is not list:
                raise ValueError("plan_scopes must be a list of nonempty names")
            scopes = []
            for scope in raw_scopes:
                if type(scope) is not str or not scope:
                    raise ValueError("plan_scopes must be a list of nonempty names")
                scopes.append(scope)
        raw_options = config_dict.get("solver_options", {})
        if type(raw_options) is not dict or any(type(name) is not str for name in raw_options):
            raise ValueError("solver_options contains unknown or overlapping budget controls")
        options: dict[str, object] = dict(raw_options)
        return cls(
            pta_analysis_name=text(
                "pta_analysis_name", "kcfa", "pta_analysis_name must be nonempty"
            ),
            plan_scopes=scopes,
            entry_scope=entry,
            context_policy=text("context_policy", "inherit", "context_policy must be a string"),
            log_level=text("log_level", "INFO", "Invalid log_level"),
            solver_options=deepcopy(options),
            type=text("type", "ai analysis", "type must be ai analysis"),
            max_iterations=budget.max_iterations,
            max_stmt_visits=budget.max_stmt_visits,
            max_recursion_depth=budget.max_recursion_depth,
            strict_exceptions=budget.strict_exceptions,
            max_addr_set_size=budget.max_addr_set_size,
            max_field_keys=budget.max_field_keys,
            max_const_strings=budget.max_const_strings,
            max_ho_tokens=budget.max_ho_tokens,
            enable_narrowing=budget.enable_narrowing,
            narrowing_iterations=budget.narrowing_iterations,
            enable_value_partition=budget.enable_value_partition,
            max_value_partitions=budget.max_value_partitions,
            value_partition_str_cap=budget.value_partition_str_cap,
            max_const_ints=budget.max_const_ints,
            max_tuple_len_exact=budget.max_tuple_len_exact,
            max_tuple_alts=budget.max_tuple_alts,
            max_star_expand_len=budget.max_star_expand_len,
            max_star_expand_alts=budget.max_star_expand_alts,
            max_dict_parts=budget.max_dict_parts,
            max_dict_key_const_strs=budget.max_dict_key_const_strs,
            max_dict_key_const_ints=budget.max_dict_key_const_ints,
            max_dict_key_tuple_len=budget.max_dict_key_tuple_len,
            max_dict_key_tuple_alts=budget.max_dict_key_tuple_alts,
            enable_dict_identity_keys=budget.enable_dict_identity_keys,
            max_dict_identity_keys=budget.max_dict_identity_keys,
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = dict(self.to_budget().to_dict())
        solver_options = deepcopy(self.solver_options)
        if "merge_points" in solver_options:
            merged = solver_options["merge_points"]
            if not isinstance(merged, (list, tuple, set)):
                raise ValueError("merge_points must contain nonnegative block ids")
            solver_options["merge_points"] = sorted(merged)
        result.update(
            pta_analysis_name=self.pta_analysis_name,
            plan_scopes=deepcopy(self.plan_scopes),
            entry_scope=self.entry_scope,
            context_policy=self.context_policy,
            log_level=self.log_level,
            solver_options=solver_options,
            type=self.type,
        )
        return result

    def to_budget(self) -> AnalysisBudget:
        return AnalysisBudget.from_options(
            {
                "max_iterations": self.max_iterations,
                "max_stmt_visits": self.max_stmt_visits,
                "max_recursion_depth": self.max_recursion_depth,
                "strict_exceptions": self.strict_exceptions,
                "max_addr_set_size": self.max_addr_set_size,
                "max_field_keys": self.max_field_keys,
                "max_const_strings": self.max_const_strings,
                "max_ho_tokens": self.max_ho_tokens,
                "enable_narrowing": self.enable_narrowing,
                "narrowing_iterations": self.narrowing_iterations,
                "enable_value_partition": self.enable_value_partition,
                "max_value_partitions": self.max_value_partitions,
                "value_partition_str_cap": self.value_partition_str_cap,
                "max_const_ints": self.max_const_ints,
                "max_tuple_len_exact": self.max_tuple_len_exact,
                "max_tuple_alts": self.max_tuple_alts,
                "max_star_expand_len": self.max_star_expand_len,
                "max_star_expand_alts": self.max_star_expand_alts,
                "max_dict_parts": self.max_dict_parts,
                "max_dict_key_const_strs": self.max_dict_key_const_strs,
                "max_dict_key_const_ints": self.max_dict_key_const_ints,
                "max_dict_key_tuple_len": self.max_dict_key_tuple_len,
                "max_dict_key_tuple_alts": self.max_dict_key_tuple_alts,
                "enable_dict_identity_keys": self.enable_dict_identity_keys,
                "max_dict_identity_keys": self.max_dict_identity_keys,
            }
        )

    def to_solver_config(self) -> "SolverConfig":
        from pythonstan.analysis.pointer.ai.solver import SolverConfig

        # Shared controls always come from AnalysisBudget, never solver_options.
        result = SolverConfig(
            max_iterations=self.max_iterations,
            max_block_visits=self.max_stmt_visits,
            enable_narrowing=self.enable_narrowing,
            narrowing_iterations=self.narrowing_iterations,
            max_addr_set_size=self.max_addr_set_size,
            enable_value_partition=self.enable_value_partition,
            max_value_partitions=self.max_value_partitions,
            value_partition_str_cap=self.value_partition_str_cap,
        )
        allowed = {
            "widen_delay",
            "loop_unroll",
            "loop_iters",
            "enable_trace_partitioning",
            "infer_merge_points",
            "merge_points",
            "max_traces_per_block",
            "max_total_traces",
        }
        if type(self.solver_options) is not dict or set(self.solver_options) - allowed:
            raise ValueError("solver_options contains unknown or overlapping budget controls")
        for name, value in self.solver_options.items():
            if name in {"enable_trace_partitioning", "infer_merge_points"}:
                if type(value) is not bool:
                    raise ValueError(f"{name} must be a Boolean")
            elif name == "merge_points":
                if (
                    not isinstance(value, (list, tuple, set))
                    or type(value) not in {list, tuple, set}
                    or any(type(x) is not int or x < 0 for x in value)
                ):
                    raise ValueError("merge_points must contain nonnegative block ids")
                value = set(value)
            elif name == "loop_iters":
                if not isinstance(value, dict) or type(value) is not dict:
                    raise ValueError("loop_iters must map block ids to nonnegative delays")
                converted = {}
                for key, delay in value.items():
                    if type(key) is str and key.isdecimal():
                        key = int(key)
                    if type(key) is not int or key < 0 or type(delay) is not int or delay < 0:
                        raise ValueError("loop_iters must map block ids to nonnegative delays")
                    converted[key] = delay
                value = converted
            elif type(value) is not int or value < (1 if name.startswith("max_") else 0):
                raise ValueError(f"{name} is outside its supported range")
            setattr(result, name, deepcopy(value))
        if "widen_delay" in self.solver_options:
            delay = self.solver_options["widen_delay"]
            if "loop_unroll" in self.solver_options and self.solver_options["loop_unroll"] != delay:
                raise ValueError(
                    "widen_delay and loop_unroll are aliases; use one or matching values"
                )
            if type(delay) is not int:
                raise ValueError("widen_delay is outside its supported range")
            result.loop_unroll = delay
        return result

    def __post_init__(self) -> None:
        if self.type != "ai analysis":
            raise ValueError("type must be ai analysis")
        if type(self.pta_analysis_name) is not str or not self.pta_analysis_name:
            raise ValueError("pta_analysis_name must be nonempty")
        if self.plan_scopes is not None and (
            type(self.plan_scopes) is not list
            or any(type(x) is not str or not x for x in self.plan_scopes)
        ):
            raise ValueError("plan_scopes must be a list of nonempty names")
        if self.entry_scope is not None and (
            type(self.entry_scope) is not str or not self.entry_scope
        ):
            raise ValueError("entry_scope must be nonempty")
        if type(self.context_policy) is not str:
            raise ValueError("context_policy must be a string")
        if self.context_policy != "inherit":
            from pythonstan.analysis.pointer.kcfa.context_selector import parse_policy

            parse_policy(self.context_policy)
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError("Invalid log_level")
        self.to_budget()
        self.to_solver_config()
        # Isolate caller-owned nested values even for direct construction.
        object.__setattr__(self, "solver_options", deepcopy(self.solver_options))
        object.__setattr__(self, "plan_scopes", deepcopy(self.plan_scopes))

    @override
    def __str__(self) -> str:
        return "AI Analysis Config: " + json.dumps(self.to_dict(), indent=4)
