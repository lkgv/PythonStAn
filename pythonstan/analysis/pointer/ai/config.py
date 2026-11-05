"""Configuration for AI analysis driver.

This module defines the configuration options for running the AI driver
independently from KCFA, while consuming existing KCFA results.
"""

from dataclasses import dataclass
import json
from typing import Dict, List, Optional

__all__ = ["Config"]


@dataclass(frozen=True)
class Config:
    """AI analysis configuration.

    Attributes:
        pta_analysis_name: Analysis name that produced KCFA results
        plan_scopes: Explicit list of scope qualnames to analyze
        entry_scope: Optional single entry scope qualname
        context_policy: Context sensitivity policy string
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)

        AI budget options:
            max_iterations: Maximum fixpoint iterations
            max_stmt_visits: Maximum statement visits per block
            max_recursion_depth: Maximum nested AI calls
            max_addr_set_size: Cap for address set widening
            max_field_keys: Cap for field store key tracking
            max_const_strings: Cap for string domain const set
            max_ho_tokens: Cap for higher-order token set
            enable_narrowing: Whether to run narrowing pass
            narrowing_iterations: Number of narrowing iterations
            enable_value_partition: Whether to refine states on branches
            max_value_partitions: Max partitions per variable before merging
            value_partition_str_cap: Max string constants to enumerate
    """

    pta_analysis_name: str = "kcfa"
    plan_scopes: Optional[List[str]] = None
    entry_scope: Optional[str] = None
    context_policy: str = "2-param"
    log_level: str = "INFO"

    max_iterations: int = 100
    max_stmt_visits: int = 50
    max_recursion_depth: int = 3
    max_addr_set_size: int = 50
    max_field_keys: int = 20
    max_const_strings: int = 10
    max_ho_tokens: int = 20
    enable_narrowing: bool = True
    narrowing_iterations: int = 2
    enable_value_partition: bool = True
    max_value_partitions: int = 4
    value_partition_str_cap: int = 4
    type: str = "ai analysis"

    @classmethod
    def from_dict(cls, config_dict: Dict) -> "Config":
        return cls(
            pta_analysis_name=config_dict.get("pta_analysis_name", "kcfa"),
            plan_scopes=config_dict.get("plan_scopes", None),
            entry_scope=config_dict.get("entry_scope", None),
            context_policy=config_dict.get("context_policy", "2-cfa"),
            log_level=config_dict.get("log_level", "INFO"),
            max_iterations=config_dict.get("max_iterations", 100),
            max_stmt_visits=config_dict.get("max_stmt_visits", 50),
            max_recursion_depth=config_dict.get("max_recursion_depth", 3),
            max_addr_set_size=config_dict.get("max_addr_set_size", 50),
            max_field_keys=config_dict.get("max_field_keys", 20),
            max_const_strings=config_dict.get("max_const_strings", 10),
            max_ho_tokens=config_dict.get("max_ho_tokens", 20),
            enable_narrowing=config_dict.get("enable_narrowing", True),
            narrowing_iterations=config_dict.get("narrowing_iterations", 2),
            enable_value_partition=config_dict.get("enable_value_partition", True),
            max_value_partitions=config_dict.get("max_value_partitions", 4),
            value_partition_str_cap=config_dict.get("value_partition_str_cap", 4),
            type="ai analysis",
        )

    def to_dict(self) -> Dict:
        return {
            "pta_analysis_name": self.pta_analysis_name,
            "plan_scopes": self.plan_scopes,
            "entry_scope": self.entry_scope,
            "context_policy": self.context_policy,
            "log_level": self.log_level,
            "max_iterations": self.max_iterations,
            "max_stmt_visits": self.max_stmt_visits,
            "max_recursion_depth": self.max_recursion_depth,
            "max_addr_set_size": self.max_addr_set_size,
            "max_field_keys": self.max_field_keys,
            "max_const_strings": self.max_const_strings,
            "max_ho_tokens": self.max_ho_tokens,
            "enable_narrowing": self.enable_narrowing,
            "narrowing_iterations": self.narrowing_iterations,
            "enable_value_partition": self.enable_value_partition,
            "max_value_partitions": self.max_value_partitions,
            "value_partition_str_cap": self.value_partition_str_cap,
            "type": self.type,
        }

    def __post_init__(self) -> None:
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if self.max_stmt_visits <= 0:
            raise ValueError("max_stmt_visits must be positive")
        if self.max_recursion_depth < 0:
            raise ValueError("max_recursion_depth must be >= 0")
        if self.max_addr_set_size <= 0:
            raise ValueError("max_addr_set_size must be positive")
        if self.max_field_keys <= 0:
            raise ValueError("max_field_keys must be positive")
        if self.max_const_strings <= 0:
            raise ValueError("max_const_strings must be positive")
        if self.max_ho_tokens <= 0:
            raise ValueError("max_ho_tokens must be positive")
        if self.narrowing_iterations < 0:
            raise ValueError("narrowing_iterations must be >= 0")
        if self.max_value_partitions <= 0:
            raise ValueError("max_value_partitions must be positive")
        if self.value_partition_str_cap <= 0:
            raise ValueError("value_partition_str_cap must be positive")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError(f"Invalid log level: {self.log_level}")

    def __str__(self) -> str:
        return f"""AI Analysis Config: {json.dumps(self.to_dict(), indent=4)}"""

    def __repr__(self) -> str:
        return self.__str__()
