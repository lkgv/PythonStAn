"""Validated configuration for the independent AI pass.

Budget options have a single projection into AnalysisBudget. Solver-only options
control partitioning and widening; overlapping names are rejected to avoid two
conflicting authorities for the same bound.
"""
from dataclasses import asdict, dataclass, field, fields
from copy import deepcopy
import json
from typing import Any, Dict, List, Optional

from pythonstan.analysis.contracts.budgets import AnalysisBudget, validate_budget_options

__all__ = ["Config", "validate_budget_options"]




@dataclass(frozen=True)
class Config:
    pta_analysis_name: str = "kcfa"
    plan_scopes: Optional[List[str]] = None
    entry_scope: Optional[str] = None
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
    solver_options: Dict[str, Any] = field(default_factory=dict)
    type: str = "ai analysis"

    @classmethod
    def from_dict(cls, config_dict: Dict) -> "Config":
        if type(config_dict) is not dict:
            raise ValueError("AI configuration must be a dictionary")
        unknown = set(config_dict) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown AI options: {sorted(unknown)}")
        return cls(**deepcopy(config_dict))

    def to_dict(self) -> Dict:
        result = asdict(self)
        if "merge_points" in result["solver_options"]:
            result["solver_options"]["merge_points"] = sorted(result["solver_options"]["merge_points"])
        return result

    def to_budget(self) -> AnalysisBudget:
        return AnalysisBudget(**validate_budget_options({f.name: getattr(self, f.name) for f in fields(AnalysisBudget)}))

    def to_solver_config(self):
        from pythonstan.analysis.pointer.ai.solver import SolverConfig
        # Shared controls always come from AnalysisBudget, never solver_options.
        result = SolverConfig(
            max_iterations=self.max_iterations, max_block_visits=self.max_stmt_visits,
            enable_narrowing=self.enable_narrowing, narrowing_iterations=self.narrowing_iterations,
            max_addr_set_size=self.max_addr_set_size, enable_value_partition=self.enable_value_partition,
            max_value_partitions=self.max_value_partitions, value_partition_str_cap=self.value_partition_str_cap,
        )
        allowed = {"widen_delay", "loop_unroll", "loop_iters", "enable_trace_partitioning", "infer_merge_points",
                   "merge_points", "max_traces_per_block", "max_total_traces"}
        if type(self.solver_options) is not dict or set(self.solver_options) - allowed:
            raise ValueError("solver_options contains unknown or overlapping budget controls")
        for name, value in self.solver_options.items():
            if name in {"enable_trace_partitioning", "infer_merge_points"}:
                if type(value) is not bool:
                    raise ValueError(f"{name} must be a Boolean")
            elif name == "merge_points":
                if type(value) not in {list, tuple, set} or any(type(x) is not int or x < 0 for x in value):
                    raise ValueError("merge_points must contain nonnegative block ids")
                value = set(value)
            elif name == "loop_iters":
                if type(value) is not dict:
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
                raise ValueError("widen_delay and loop_unroll are aliases; use one or matching values")
            result.loop_unroll = delay
        return result

    def __post_init__(self):
        if self.type != "ai analysis":
            raise ValueError("type must be ai analysis")
        if type(self.pta_analysis_name) is not str or not self.pta_analysis_name:
            raise ValueError("pta_analysis_name must be nonempty")
        if self.plan_scopes is not None and (type(self.plan_scopes) is not list or any(type(x) is not str or not x for x in self.plan_scopes)):
            raise ValueError("plan_scopes must be a list of nonempty names")
        if self.entry_scope is not None and (type(self.entry_scope) is not str or not self.entry_scope):
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

    def __str__(self):
        return "AI Analysis Config: " + json.dumps(self.to_dict(), indent=4)
