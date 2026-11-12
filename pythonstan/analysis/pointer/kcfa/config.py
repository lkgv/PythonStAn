"""Configuration for pointer analysis.

This module defines the configuration options for k-CFA pointer analysis.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import override

from pythonstan.analysis.contracts.budgets import validate_budget_options
from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue, validate_json_object

__all__ = ["Config"]


def _string(value: JsonValue, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _optional_string(value: JsonValue, name: str) -> str | None:
    return None if value is None else _string(value, name)


def _integer(value: JsonValue, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _optional_integer(value: JsonValue, name: str) -> int | None:
    return None if value is None else _integer(value, name)


def _boolean(value: JsonValue, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a Boolean")
    return value


def _strings(value: object, name: str) -> list[str] | None:
    if value is None:
        return None
    if type(value) is not list:
        raise ValueError(f"{name} must be a list of nonempty strings")
    result: list[str] = []
    for item in value:
        if type(item) is not str or not item:
            raise ValueError(f"{name} must be a list of nonempty strings")
        result.append(item)
    return result


def _budget(value: object) -> dict[str, int | bool]:
    if type(value) is not dict:
        raise ValueError("AI budget must be a dictionary")
    return validate_budget_options(validate_json_object(value))


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
    heap_context_policy: str | None = None
    heap_context_depth: int | None = None
    max_iterations: int = 10000
    max_points_to_size: int | None = None
    verbose: bool = False
    log_level: str = "INFO"
    enable_instrumentation: bool = False
    entry_points: list[str] | None = None
    enable_synthetic_method_contexts: bool = True
    build_class_hierarchy: bool = True
    use_mro_resolution: bool = True
    project_path: str | None = None
    library_paths: list[str] | None = None
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
    ai_budget: dict[str, int | bool] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, config_dict: object) -> Config:
        if type(config_dict) is not dict:
            raise ValueError("KCFA configuration must be a dictionary")
        entries: Mapping[object, object] = config_dict
        raw: dict[str, object] = {}
        for name, value in entries.items():
            if not isinstance(name, str):
                raise ValueError("KCFA configuration option names must be strings")
            raw[name] = value
        document = cls().to_dict()
        known = document.keys()
        driver_options = {"test_root", "test_dir", "test_files"}
        unknown = set(raw) - known - driver_options
        if unknown:
            raise ValueError(f"Unknown KCFA options: {sorted(unknown)}")
        # Preserve exact legacy container constraints before JSON copying can
        # normalize a tuple, mapping proxy, or subclass into a builtin container.
        if "ai_budget" in raw:
            _budget(raw["ai_budget"])
        for name in ("entry_points", "library_paths"):
            if name in raw:
                _strings(raw[name], name)
        document.update(
            validate_json_object({name: value for name, value in raw.items() if name in known})
        )
        return cls(
            context_policy=_string(document["context_policy"], "context_policy"),
            heap_context_policy=_optional_string(
                document["heap_context_policy"], "heap_context_policy"
            ),
            heap_context_depth=_optional_integer(
                document["heap_context_depth"], "heap_context_depth"
            ),
            max_iterations=_integer(document["max_iterations"], "max_iterations"),
            max_points_to_size=_optional_integer(
                document["max_points_to_size"], "max_points_to_size"
            ),
            verbose=_boolean(document["verbose"], "verbose"),
            log_level=_string(document["log_level"], "log_level"),
            enable_instrumentation=_boolean(
                document["enable_instrumentation"], "enable_instrumentation"
            ),
            entry_points=_strings(document["entry_points"], "entry_points"),
            enable_synthetic_method_contexts=_boolean(
                document["enable_synthetic_method_contexts"], "enable_synthetic_method_contexts"
            ),
            build_class_hierarchy=_boolean(
                document["build_class_hierarchy"], "build_class_hierarchy"
            ),
            use_mro_resolution=_boolean(document["use_mro_resolution"], "use_mro_resolution"),
            project_path=_optional_string(document["project_path"], "project_path"),
            library_paths=_strings(document["library_paths"], "library_paths"),
            max_import_depth=_integer(document["max_import_depth"], "max_import_depth"),
            track_unknowns=_boolean(document["track_unknowns"], "track_unknowns"),
            log_unknown_details=_boolean(document["log_unknown_details"], "log_unknown_details"),
            type=_string(document["type"], "type"),
            index_sensitive=_boolean(document["index_sensitive"], "index_sensitive"),
            work_scheduling_policy=_string(
                document["work_scheduling_policy"], "work_scheduling_policy"
            ),
            points_work_policy=_string(document["points_work_policy"], "points_work_policy"),
            builtin_bootstrap_policy=_string(
                document["builtin_bootstrap_policy"], "builtin_bootstrap_policy"
            ),
            keyword_context_model=_string(
                document["keyword_context_model"], "keyword_context_model"
            ),
            keyword_context_limit=_integer(
                document["keyword_context_limit"], "keyword_context_limit"
            ),
            enable_debug_monitor=_boolean(document["enable_debug_monitor"], "enable_debug_monitor"),
            debug_log_interval=_integer(document["debug_log_interval"], "debug_log_interval"),
            track_object_flow=_boolean(document["track_object_flow"], "track_object_flow"),
            track_pfg_activation=_boolean(document["track_pfg_activation"], "track_pfg_activation"),
            export_debug_data=_boolean(document["export_debug_data"], "export_debug_data"),
            debug_output_dir=_string(document["debug_output_dir"], "debug_output_dir"),
            debug_inheritance=_boolean(document["debug_inheritance"], "debug_inheritance"),
            enable_ai_intrinsics=_boolean(document["enable_ai_intrinsics"], "enable_ai_intrinsics"),
            ai_budget=_budget(document["ai_budget"]),
        )

    def to_dict(self) -> JsonObject:
        return {
            "context_policy": self.context_policy,
            "heap_context_policy": self.heap_context_policy,
            "heap_context_depth": self.heap_context_depth,
            "max_iterations": self.max_iterations,
            "max_points_to_size": self.max_points_to_size,
            "verbose": self.verbose,
            "log_level": self.log_level,
            "enable_instrumentation": self.enable_instrumentation,
            "entry_points": None
            if self.entry_points is None
            else list[JsonValue](self.entry_points),
            "enable_synthetic_method_contexts": self.enable_synthetic_method_contexts,
            "build_class_hierarchy": self.build_class_hierarchy,
            "use_mro_resolution": self.use_mro_resolution,
            "project_path": self.project_path,
            "library_paths": None
            if self.library_paths is None
            else list[JsonValue](self.library_paths),
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

    def __post_init__(self) -> None:
        """Validate configuration."""
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
        if not isinstance(self.type, str) or self.type != "pointer analysis":
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
        if not isinstance(self.log_level, str) or self.log_level not in (
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
        ):
            raise ValueError(f"Invalid log level: {self.log_level}")
        if self.project_path is not None and not isinstance(self.project_path, str):
            raise ValueError("project_path must be a string or None")
        if not isinstance(self.debug_output_dir, str):
            raise ValueError("debug_output_dir must be a string")
        if self.max_points_to_size is not None and type(self.max_points_to_size) is not int:
            raise ValueError("max_points_to_size must be an integer or None")
        boolean_options = (
            ("verbose", self.verbose),
            ("enable_instrumentation", self.enable_instrumentation),
            ("enable_synthetic_method_contexts", self.enable_synthetic_method_contexts),
            ("build_class_hierarchy", self.build_class_hierarchy),
            ("use_mro_resolution", self.use_mro_resolution),
            ("track_unknowns", self.track_unknowns),
            ("log_unknown_details", self.log_unknown_details),
            ("index_sensitive", self.index_sensitive),
            ("enable_debug_monitor", self.enable_debug_monitor),
            ("track_object_flow", self.track_object_flow),
            ("track_pfg_activation", self.track_pfg_activation),
            ("export_debug_data", self.export_debug_data),
            ("debug_inheritance", self.debug_inheritance),
            ("enable_ai_intrinsics", self.enable_ai_intrinsics),
        )
        for name, boolean_value in boolean_options:
            if type(boolean_value) is not bool:
                raise ValueError(f"{name} must be a Boolean")
        object.__setattr__(self, "entry_points", _strings(self.entry_points, "entry_points"))
        object.__setattr__(self, "library_paths", _strings(self.library_paths, "library_paths"))
        if self.library_paths:
            raise ValueError(
                "KCFA library_paths is unsupported; set Pipeline library_paths instead"
            )
        if type(self.max_import_depth) is not int or self.max_import_depth < -1:
            raise ValueError("max_import_depth must be an integer >= -1")
        # These historical knobs never reached this solver. Refuse requests for
        # behavior they cannot implement; retain defaults for legacy files.
        unsupported: tuple[tuple[str, object, object], ...] = (
            ("max_points_to_size", self.max_points_to_size, None),
            ("enable_instrumentation", self.enable_instrumentation, False),
            ("build_class_hierarchy", self.build_class_hierarchy, True),
            ("use_mro_resolution", self.use_mro_resolution, True),
            ("max_import_depth", self.max_import_depth, 2),
        )
        for name, value, default in unsupported:
            if value != default:
                raise ValueError(
                    f"{name} is not configurable by this KCFA solver; see configuration_diagnostics()"
                )

    def configuration_diagnostics(self) -> list[str]:
        """Explicit support boundary for legacy options retained for roundtrip."""
        return [
            "max_points_to_size: unsupported; points-to sets are not truncated",
            "enable_instrumentation: unsupported; use solver stats",
            "build_class_hierarchy/use_mro_resolution: fixed enabled by current driver",
            "max_import_depth/library_paths: import traversal is owned by Pipeline import_level/library_paths",
            "project_path: used for debug export naming; module resolution is owned by Pipeline",
        ]

    @override
    def __str__(self) -> str:
        return f"""Pointer Analysis Config: {json.dumps(self.to_dict(), indent=4)}"""

    @override
    def __repr__(self) -> str:
        return self.__str__()
