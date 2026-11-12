"""Configuration for pointer analysis.

This module defines the configuration options for k-CFA pointer analysis.
"""

from dataclasses import dataclass, field, fields
from copy import deepcopy
import json
from typing import Any, Optional, List, Dict

__all__ = ["Config"]


@dataclass(frozen=True)
class Config:
    """Analysis configuration.

    Attributes:
        context_policy: Context sensitivity policy string
        max_iterations: Maximum solver iterations
        max_points_to_size: Widening threshold for points-to sets
        verbose: Enable verbose logging
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        enable_instrumentation: Enable performance instrumentation
        entry_points: Entry point functions
        build_class_hierarchy: Build class hierarchy and compute MRO
        use_mro_resolution: Use MRO for attribute resolution
        project_path: Project root path for module resolution
        library_paths: External library paths for import resolution
        max_import_depth: Maximum depth for transitive import analysis (0 = no imports, -1 = unlimited)
        track_unknowns: Enable tracking of unknown/unresolved calls and allocations
        log_unknown_details: If True, logs each unknown immediately (verbose mode required)
        enable_debug_monitor: Enable comprehensive debug monitoring
        debug_log_interval: Log snapshot every N iterations
        track_object_flow: Track object creation and propagation
        track_pfg_activation: Track PFG edge activation
        export_debug_data: Export debug data to files
        debug_output_dir: Directory for debug output files
        work_scheduling_policy: legacy strict-priority/LIFO or fair cyclic/FIFO work
    """

    context_policy: str = "2-cfa"
    heap_context_policy: Optional[str] = None
    heap_context_depth: Optional[int] = None
    max_iterations: int = 10000
    max_points_to_size: Optional[int] = None
    verbose: bool = False
    log_level: str = "INFO"
    enable_instrumentation: bool = False
    entry_points: Optional[List[str]] = None
    enable_synthetic_method_contexts: bool = True
    build_class_hierarchy: bool = True
    use_mro_resolution: bool = True
    project_path: Optional[str] = None
    library_paths: Optional[List[str]] = None
    max_import_depth: int = 2
    track_unknowns: bool = True
    log_unknown_details: bool = False
    type: str = "pointer analysis"
    index_sensitive: bool = True
    # Keep the historical order available for paired diagnostics. The fair
    # policy gives every nonempty work kind cyclic service and FIFO items.
    work_scheduling_policy: str = "legacy"
    # Experimental ordering within points work; FIFO remains the exact default.
    points_work_policy: str = "fifo"
    builtin_bootstrap_policy: str = "queued"
    # Additional local precision dimension; none preserves bare base policies.
    keyword_context_model: str = "none"
    keyword_context_limit: int = 32

    # Debug monitoring options
    enable_debug_monitor: bool = False
    debug_log_interval: int = 1000
    track_object_flow: bool = False
    track_pfg_activation: bool = False
    export_debug_data: bool = False
    debug_output_dir: str = "debug_output"
    debug_inheritance: bool = False  # Debug class field inheritance

    # AI intrinsic toggle (for KCFA vs KCFA+AI comparison)
    enable_ai_intrinsics: bool = True
    ai_budget: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config_dict: Dict):
        if type(config_dict) is not dict:
            raise ValueError("KCFA configuration must be a dictionary")
        known = {f.name for f in fields(cls)}
        # These are consumed by the driver rather than the solver Config.
        driver_options = {"test_root", "test_dir", "test_files"}
        unknown = set(config_dict) - known - driver_options
        if unknown:
            raise ValueError(f"Unknown KCFA options: {sorted(unknown)}")
        return cls(**deepcopy({k: v for k, v in config_dict.items() if k in known}))

    def to_dict(self) -> Dict:
        return {
            "context_policy": self.context_policy,
            "heap_context_policy": self.heap_context_policy,
            "heap_context_depth": self.heap_context_depth,
            "max_iterations": self.max_iterations,
            "max_points_to_size": self.max_points_to_size,
            "verbose": self.verbose,
            "log_level": self.log_level,
            "enable_instrumentation": self.enable_instrumentation,
            "entry_points": self.entry_points,
            "enable_synthetic_method_contexts": self.enable_synthetic_method_contexts,
            "build_class_hierarchy": self.build_class_hierarchy,
            "use_mro_resolution": self.use_mro_resolution,
            "project_path": self.project_path,
            "library_paths": self.library_paths,
            "max_import_depth": self.max_import_depth,
            "track_unknowns": self.track_unknowns,
            "log_unknown_details": self.log_unknown_details,
            "index_sensitive": self.index_sensitive,
            "work_scheduling_policy": self.work_scheduling_policy,
            "points_work_policy": self.points_work_policy,
            "builtin_bootstrap_policy": self.builtin_bootstrap_policy,
            "keyword_context_model": self.keyword_context_model,
            "keyword_context_limit": self.keyword_context_limit,
            "enable_debug_monitor": self.enable_debug_monitor,
            "debug_log_interval": self.debug_log_interval,
            "track_object_flow": self.track_object_flow,
            "track_pfg_activation": self.track_pfg_activation,
            "export_debug_data": self.export_debug_data,
            "debug_output_dir": self.debug_output_dir,
            "debug_inheritance": self.debug_inheritance,
            "enable_ai_intrinsics": self.enable_ai_intrinsics,
            "ai_budget": dict(self.ai_budget),
            "type": self.type,
        }

    def __post_init__(self):
        """Validate configuration."""
        from pythonstan.analysis.contracts.budgets import validate_budget_options
        from .context_selector import parse_policy

        object.__setattr__(self, "ai_budget", validate_budget_options(self.ai_budget))
        object.__setattr__(self, "entry_points", deepcopy(self.entry_points))
        object.__setattr__(self, "library_paths", deepcopy(self.library_paths))
        if type(self.context_policy) is not str:
            raise ValueError("context_policy must be a string")
        parse_policy(self.context_policy)
        if self.heap_context_policy is not None:
            if type(self.heap_context_policy) is not str:
                raise ValueError("heap_context_policy must be a policy string or None")
            parse_policy(self.heap_context_policy)
        if self.heap_context_depth is not None and (
            type(self.heap_context_depth) is not int or not 0 <= self.heap_context_depth <= 3
        ):
            raise ValueError("heap_context_depth must be an integer in 0..3 or None")
        if self.type != "pointer analysis":
            raise ValueError("type must be pointer analysis")
        if type(self.work_scheduling_policy) is not str or self.work_scheduling_policy not in (
            "legacy",
            "fair",
        ):
            raise ValueError("work_scheduling_policy must be legacy or fair")
        if type(self.points_work_policy) is not str or self.points_work_policy not in (
            "fifo",
            "consumer_fair",
        ):
            raise ValueError("points_work_policy must be fifo or consumer_fair")
        if type(self.builtin_bootstrap_policy) is not str or self.builtin_bootstrap_policy not in (
            "queued",
            "eager_inert",
        ):
            raise ValueError("builtin_bootstrap_policy must be queued or eager_inert")
        if type(self.keyword_context_model) is not str or self.keyword_context_model not in (
            "none",
            "receiver_key_v1",
        ):
            raise ValueError("keyword_context_model must be none or receiver_key_v1")
        if type(self.keyword_context_limit) is not int or self.keyword_context_limit <= 0:
            raise ValueError("keyword_context_limit must be a positive integer")
        if type(self.max_iterations) is not int or self.max_iterations <= 0:
            raise ValueError("max_iterations must be a positive integer")
        if type(self.debug_log_interval) is not int or self.debug_log_interval <= 0:
            raise ValueError("debug_log_interval must be a positive integer")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError(f"Invalid log level: {self.log_level}")
        for option in fields(self):
            if type(option.default) is bool and type(getattr(self, option.name)) is not bool:
                raise ValueError(f"{option.name} must be a Boolean")
        for name in ("entry_points", "library_paths"):
            value = getattr(self, name)
            if value is not None and (
                type(value) is not list or any(type(x) is not str or not x for x in value)
            ):
                raise ValueError(f"{name} must be a list of nonempty strings")
        if self.library_paths:
            raise ValueError(
                "KCFA library_paths is unsupported; set Pipeline library_paths instead"
            )
        if type(self.max_import_depth) is not int or self.max_import_depth < -1:
            raise ValueError("max_import_depth must be an integer >= -1")
        # These historical knobs never reached this solver. Refuse requests for
        # behavior they cannot implement; retain defaults for legacy files.
        unsupported = {
            "max_points_to_size": None,
            "enable_instrumentation": False,
            "build_class_hierarchy": True,
            "use_mro_resolution": True,
            "max_import_depth": 2,
        }
        for name, default in unsupported.items():
            if getattr(self, name) != default:
                raise ValueError(
                    f"{name} is not configurable by this KCFA solver; see configuration_diagnostics()"
                )

    def configuration_diagnostics(self) -> List[str]:
        """Explicit support boundary for legacy options retained for roundtrip."""
        return [
            "max_points_to_size: unsupported; points-to sets are not truncated",
            "enable_instrumentation: unsupported; use solver stats",
            "build_class_hierarchy/use_mro_resolution: fixed enabled by current driver",
            "max_import_depth/library_paths: import traversal is owned by Pipeline import_level/library_paths",
            "project_path: used for debug export naming; module resolution is owned by Pipeline",
        ]

    def __str__(self):
        return f"""Pointer Analysis Config: {json.dumps(self.to_dict(), indent=4)}"""

    def __repr__(self):
        return self.__str__()
