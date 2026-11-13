"""Strict deterministic configuration resolution before analysis construction."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping, Sequence
from importlib.resources import files
from pathlib import Path
from typing import TypeGuard

import yaml
from yaml.nodes import MappingNode, Node

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    JsonValue,
    require_array,
    require_object,
    require_string,
    thaw_json,
    validate_json,
    validate_json_object,
)
from pythonstan.results.output import parse_output
from pythonstan.results.queries import parse_queries

from .catalog import CATALOG, REGIONAL_SOLVER_OPTIONS, default_config
from .errors import ConfigurationError
from .model import AnalysisConfig as AnalysisConfig
from .model import ConfigInput, ConfigTarget
from .model import EffectiveConfig as EffectiveConfig


def _raw_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigurationError("Configuration must contain an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ConfigurationError("Configuration keys must be strings")
        result[key] = item
    return result


def _unique_pairs(pairs: Iterable[tuple[object, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if not isinstance(key, str):
            raise ConfigurationError("Configuration keys must be strings")
        if key in result:
            raise ConfigurationError(f"Duplicate configuration key: {key}")
        result[key] = item
    return result


class _UniqueLoader(yaml.SafeLoader):
    """Safe YAML loader whose mapping construction retains duplicate evidence."""


_YAML_NODE: type[object] = Node


def _is_yaml_node(value: object) -> TypeGuard[Node]:
    return isinstance(value, _YAML_NODE)


def _yaml_mapping(loader: yaml.SafeLoader, node: MappingNode) -> dict[object, object]:
    # YAML permits integer block IDs in solver_options.loop_iters. Keep those
    # external keys until the field decoder validates and canonicalizes them.
    result: dict[object, object] = {}
    raw_pairs: object = node.value
    if not isinstance(raw_pairs, (list, tuple)):
        raise ConfigurationError("YAML mapping must contain node pairs")
    pairs: Sequence[object] = raw_pairs
    for pair in pairs:
        if not isinstance(pair, (list, tuple)):
            raise ConfigurationError("YAML mapping must contain node pairs")
        nodes: Sequence[object] = pair
        if len(nodes) != 2:
            raise ConfigurationError("YAML mapping must contain node pairs")
        key_node, value_node = nodes
        if not _is_yaml_node(key_node) or not _is_yaml_node(value_node):
            raise ConfigurationError("YAML mapping must contain node pairs")
        key: object = loader.construct_object(key_node)
        value: object = loader.construct_object(value_node)
        if key in result:
            raise ConfigurationError(f"Duplicate configuration key: {key}")
        result[key] = value
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def _load(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
        data: object
        if path.suffix.lower() == ".json":
            data = json.loads(text, object_pairs_hook=_unique_pairs)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.load(text, Loader=_UniqueLoader)
        else:
            raise ConfigurationError("Configuration must be YAML or JSON")
        return _raw_mapping(data)
    except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError) as error:
        if isinstance(error, ConfigurationError):
            raise
        raise ConfigurationError(f"Cannot parse configuration: {error}") from error


def _flatten(value: Mapping[str, object], prefix: str = "") -> Iterator[tuple[str, object]]:
    for name, item in value.items():
        path = f"{prefix}.{name}" if prefix else name
        if path in CATALOG:
            yield path, item
        elif isinstance(item, Mapping) and any(key.startswith(path + ".") for key in CATALOG):
            yield from _flatten(_raw_mapping(item), path)
        else:
            raise ConfigurationError(f"Unknown configuration key: {path}")


def _set(data: JsonObject, path: str, value: JsonValue) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        node = require_object(node[part])
    node[parts[-1]] = validate_json(value)


def _get(data: JsonObject, path: str) -> JsonValue:
    node: JsonValue = data
    for part in path.split("."):
        node = require_object(node)[part]
    return node


def _normalize_aliases(raw: Mapping[str, object]) -> dict[str, object]:
    result = dict(raw)
    for alias, canonical in (("mode", "engine"), ("ai", "abstract"), ("kcfa", "andersen")):
        if alias in result:
            if canonical in result:
                raise ConfigurationError(f"Conflicting alias {alias}/{canonical}")
            result[canonical] = result.pop(alias)
    engine = result.get("engine")
    if isinstance(engine, str) and engine in {"kcfa", "ai"}:
        result["engine"] = {"kcfa": "native", "ai": "native_then_ai"}[engine]
    return result


def _solver_json(value: object) -> JsonObject:
    options = _raw_mapping(value)
    loops = options.get("loop_iters")
    if isinstance(loops, Mapping):
        converted: JsonObject = {}
        for key, delay in loops.items():
            if not (type(key) is int and key >= 0 or isinstance(key, str) and key.isdecimal()):
                raise ConfigurationError("loop_iters maps block IDs to nonnegative delays")
            name = str(key)
            if name in converted:
                raise ConfigurationError(f"Duplicate canonical loop block ID: {name}")
            converted[name] = validate_json(delay)
        options["loop_iters"] = converted
    return validate_json_object(options)


def _leaf_value(path: str, value: object) -> JsonValue:
    if path in {"inputs.project_root", "andersen.project_path", "andersen.debug_output_dir"}:
        if isinstance(value, os.PathLike):
            path_value: object = os.fspath(value)
            value = path_value
    elif path == "inputs.source_roots" and isinstance(value, (list, tuple)):
        roots: Sequence[object] = value
        path_values: list[object] = []
        for item in roots:
            if isinstance(item, os.PathLike):
                path_item: object = os.fspath(item)
                path_values.append(path_item)
            else:
                path_values.append(item)
        value = path_values
    data = _solver_json(value) if path == "abstract.solver_options" else validate_json(value)
    option = CATALOG.get(path)
    if option is None:
        raise ConfigurationError(f"Unknown configuration key: {path}")
    kind = option.kind
    valid = (
        kind == "boolean"
        and type(data) is bool
        or kind == "integer"
        and type(data) is int
        or kind == "number"
        and type(data) in {int, float}
        or kind == "string"
        and isinstance(data, str)
        or kind == "array"
        and isinstance(data, list)
        or kind == "object"
        and isinstance(data, dict)
        or kind == "nullable"
    )
    if not valid:
        raise ConfigurationError(f"{path} must have type {kind}")
    if kind == "nullable" and data is not None:
        if path in {
            "inputs.project_root",
            "andersen.project_path",
            "andersen.heap_context_policy",
        } and not isinstance(data, str):
            raise ConfigurationError(f"{path} must be a path/string or null")
        if path in {"andersen.entry_points", "andersen.library_paths", "abstract.plan_scopes"}:
            _strings(path, data)
    if option.choices and data not in option.choices:
        raise ConfigurationError(f"Unsupported {path}: {data!r}; choose {option.choices}")
    if path in {
        "inputs.source_roots",
        "inputs.entries",
        "inputs.exclusions",
        "output.views",
        "output.modules",
        "output.functions",
        "output.regions",
        "output.contexts",
        "output.fields",
    }:
        _strings(path, data)
    if option.minimum is not None and (
        not isinstance(data, (int, float)) or isinstance(data, bool) or data < option.minimum
    ):
        raise ConfigurationError(f"{path} must be >= {option.minimum}")
    if not option.configurable and data != thaw_json(option.default):
        raise ConfigurationError(
            f"{path} is fixed/unsupported; supported value: {thaw_json(option.default)!r}"
        )
    return data


def _strings(path: str, value: JsonValue) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigurationError(f"{path} must contain strings")


def _scalar(value: JsonValue) -> bool:
    return value is None or isinstance(value, (str, bool, int, float))


def _validate_bindings(bindings: JsonObject) -> None:
    sorts: dict[str, type[object]] = {
        "str": str,
        "int": int,
        "bool": bool,
        "float": float,
        "none": type(None),
    }
    for name, descriptor in bindings.items():
        if "." not in name or not name.rsplit(".", 1)[1].isidentifier():
            raise ConfigurationError("Input bindings use module.global names")
        if _scalar(descriptor):
            continue
        if not isinstance(descriptor, dict):
            raise ConfigurationError(f"{name}: input must be a scalar or abstract descriptor")
        if set(descriptor) - {"type", "top", "value", "constants", "input_contract"}:
            raise ConfigurationError(f"{name}: unsupported abstract input fields")
        sort = descriptor.get("type")
        if sort is not None and (
            not isinstance(sort, str)
            or sort not in {*sorts, "any", "object", "list", "tuple", "dict", "set", "function"}
        ):
            raise ConfigurationError(f"{name}: unsupported abstract input sort")
        if "top" in descriptor and type(descriptor["top"]) is not bool:
            raise ConfigurationError(f"{name}: top must be boolean")
        finite = [key for key in ("value", "constants") if key in descriptor]
        if len(finite) > 1 or finite and descriptor.get("top"):
            raise ConfigurationError(f"{name}: finite and Top input forms conflict")
        values = descriptor.get("constants", [descriptor["value"]] if "value" in descriptor else [])
        if not isinstance(values, list) or any(not _scalar(value) for value in values):
            raise ConfigurationError(f"{name}: constants must be finite JSON scalars")
        if finite and not values:
            raise ConfigurationError(f"{name}: empty finite input bound")
        if (
            isinstance(sort, str)
            and sort in sorts
            and any(type(value) is not sorts[sort] for value in values)
        ):
            raise ConfigurationError(f"{name}: scalar type conflicts with declared sort")
        if not finite and not descriptor.get("top") and sort is None:
            raise ConfigurationError(
                f"{name}: abstract input requires a finite value or explicit covering Top"
            )
        if "input_contract" in descriptor and (
            not isinstance(descriptor["input_contract"], str) or finite
        ):
            raise ConfigurationError(
                f"{name}: a finite sample cannot claim an arbitrary input contract"
            )


def _validate_solver(solver: JsonObject) -> None:
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
    if set(solver) - allowed:
        raise ConfigurationError(f"Unsupported solver options: {sorted(set(solver) - allowed)}")
    for key, value in solver.items():
        if key in {"enable_trace_partitioning", "infer_merge_points"}:
            if type(value) is not bool:
                raise ConfigurationError(f"solver_options.{key} must be boolean")
        elif key == "merge_points":
            if not isinstance(value, list) or any(
                type(item) is not int or item < 0 for item in value
            ):
                raise ConfigurationError("merge_points must contain nonnegative block IDs")
        elif key == "loop_iters":
            if not isinstance(value, dict) or any(
                not name.isdecimal() or type(delay) is not int or delay < 0
                for name, delay in value.items()
            ):
                raise ConfigurationError("loop_iters maps block IDs to nonnegative delays")
        elif type(value) is not int or value < (1 if key.startswith("max_") else 0):
            raise ConfigurationError(f"solver_options.{key} is outside its supported range")
    if (
        "widen_delay" in solver
        and "loop_unroll" in solver
        and solver["widen_delay"] != solver["loop_unroll"]
    ):
        raise ConfigurationError("widen_delay and loop_unroll aliases conflict")


def _metadata_rows(metadata: JsonObject, field: str) -> tuple[JsonObject, ...]:
    return tuple(require_object(row) for row in require_array(metadata[field]))


def _validate_controls(data: JsonObject, provenance: Mapping[str, str]) -> None:
    enumerations = {
        "andersen.log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
        "abstract.log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
        "andersen.work_scheduling_policy": ("legacy", "fair"),
        "andersen.points_work_policy": ("fifo", "consumer_fair"),
        "andersen.builtin_bootstrap_policy": ("queued", "eager_inert"),
        "andersen.keyword_context_model": ("none", "receiver_key_v1"),
    }
    for key, choices in enumerations.items():
        if _get(data, key) not in choices:
            raise ConfigurationError(f"Unsupported {key}: {_get(data, key)!r}")
    solver = require_object(_get(data, "abstract.solver_options"))
    _validate_solver(solver)
    from pythonstan.analysis.contracts.budgets import validate_budget_options

    validate_budget_options(require_object(_get(data, "andersen.ai_budget")))
    depth = _get(data, "andersen.heap_context_depth")
    if depth is not None and (type(depth) is not int or not 0 <= depth <= 3):
        raise ConfigurationError("heap_context_depth must be 0..3 or null")
    policy = _get(data, "andersen.heap_context_policy")
    if policy is not None and policy not in CATALOG["andersen.context_policy"].choices:
        raise ConfigurationError("Unsupported heap_context_policy")
    from .llm_provider import ProviderOptions

    llm = require_object(data["llm"])
    ProviderOptions.from_wire(llm)
    if llm["enabled"] and not (llm["level1"] or llm["level2"]):
        raise ConfigurationError("Enabled LLM requires level1 or level2")
    raw_metadata: object = json.loads(
        files(__package__).joinpath("control_metadata.json").read_text()
    )
    metadata = validate_json_object(raw_metadata)
    _active_controls(data, solver, metadata, provenance)
    _validate_bindings(require_object(_get(data, "inputs.bindings")))


def _active_controls(
    data: JsonObject, solver: JsonObject, metadata: JsonObject, provenance: Mapping[str, str]
) -> None:
    budget = require_object(_get(data, "andersen.ai_budget"))
    for row in _metadata_rows(metadata, "ai_budget_options"):
        name = require_string(row["old_name"])
        if (
            row.get("legacy_status") == "retired_reject_nondefault"
            and name in budget
            and budget[name] != row["old_default"]
        ):
            raise ConfigurationError(f"andersen.ai_budget.{name} is retired for the shared core")
    engine = require_string(data["engine"])
    builtin = default_config()
    for row in _metadata_rows(metadata, "native_options"):
        key = require_string(row["path"])
        changed = _get(data, key) != _get(builtin, key)
        unavailable = row["public_recommendation"] == "reject_nondefault_or_explicit_entry_contract"
        if changed and (engine not in CATALOG[key].engines or unavailable):
            raise ConfigurationError(
                f"{key} has no active public consumer for {engine}; use inputs.entries/source_roots for input contracts"
            )
    for key in ("andersen.heap_context_policy", "andersen.heap_context_depth"):
        if engine not in ("native", "native_then_ai") and _get(data, key) != _get(builtin, key):
            raise ConfigurationError(f"{key} is supported by native/native_then_ai only")
    for key, descriptor in CATALOG.items():
        if (
            key.startswith("abstract.")
            and key != "abstract.relations"
            and _get(data, key) != _get(builtin, key)
            and engine not in descriptor.engines
        ):
            raise ConfigurationError(
                f"{key} has no active public consumer for {engine}; legacy intrinsic budgets use andersen.ai_budget"
            )
    if engine in ("native_then_ai", "ai_primary", "hybrid"):
        for row in _metadata_rows(metadata, "solver_options"):
            path = require_string(row["path"])
            name = path.rsplit(".", 1)[-1]
            if (
                name in solver
                and row["new_ai_status"] != "supported"
                and solver[name] != row["legacy_default"]
            ):
                raise ConfigurationError(f"{path} is not implemented by the selected core")
    rel_key, partition_key = "abstract.relations", "abstract.enable_value_partition"
    if provenance[rel_key] != "builtin":
        if provenance[partition_key] != "builtin" and _get(data, rel_key) != _get(
            data, partition_key
        ):
            raise ConfigurationError("abstract.relations conflicts with enable_value_partition")
        if engine == "native" and _get(data, rel_key) != _get(builtin, rel_key):
            raise ConfigurationError("abstract.relations requires an abstract core")
        _set(data, partition_key, _get(data, rel_key))
    else:
        _set(data, rel_key, _get(data, partition_key))
    if engine == "native" and require_object(data["llm"])["enabled"]:
        raise ConfigurationError(
            "LLM policies and checked summaries require native_then_ai, ai_primary, or hybrid"
        )


def _configuration_source(
    target: ConfigTarget | None, config: ConfigInput
) -> tuple[dict[str, object], Path | None]:
    if isinstance(config, EffectiveConfig):
        return dict(config.to_dict()), None
    if isinstance(config, Mapping):
        return _raw_mapping(config), None
    if isinstance(config, (str, os.PathLike)):
        path = Path(config).resolve()
        return _load(path), path
    if config is not None:
        raise ConfigurationError("config must be a mapping, AnalysisConfig, or path")
    root = None if target is None else target.config_search_root
    if root is None:
        return {}, None
    directory = root.resolve()
    while True:
        candidates = [
            directory / name
            for name in ("pythonstan.yaml", "pythonstan.json")
            if (directory / name).is_file()
        ]
        if len(candidates) > 1:
            raise ConfigurationError(f"Conflicting default configurations in {directory}")
        if candidates:
            return _load(candidates[0]), candidates[0]
        if target is None or target.kind == "project" or directory.parent == directory:
            return {}, None
        directory = directory.parent


def _apply(
    data: JsonObject, provenance: dict[str, str], raw: Mapping[str, object], origin: str
) -> None:
    seen: set[str] = set()
    for key, value in _flatten(_normalize_aliases(raw)):
        if key in seen:
            raise ConfigurationError(f"Duplicate canonical configuration key: {key}")
        seen.add(key)
        _set(data, key, _leaf_value(key, value))
        provenance[key] = origin


def _relative_paths(data: JsonObject, provenance: Mapping[str, str], path: Path) -> None:
    for key in ("inputs.project_root", "andersen.project_path", "andersen.debug_output_dir"):
        value = _get(data, key)
        if value is not None and provenance[key].startswith("config:"):
            text = require_string(value)
            if not Path(text).is_absolute():
                _set(data, key, str((path.parent / text).resolve()))
    roots = require_array(_get(data, "inputs.source_roots"))
    if roots:
        _set(
            data,
            "inputs.source_roots",
            [str((path.parent / require_string(root)).resolve()) for root in roots],
        )


def resolve_config(
    target: ConfigTarget | None = None,
    config: ConfigInput = None,
    overrides: Mapping[str, object] | None = None,
    *,
    queries: Sequence[object] | None = None,
    output: object = None,
) -> EffectiveConfig:
    """Validate each raw tier, then publish one typed immutable configuration."""
    try:
        return _resolve_config(target, config, overrides, queries=queries, output=output)
    except (TypeError, ValueError) as error:
        if isinstance(error, ConfigurationError):
            raise
        raise ConfigurationError(str(error)) from error


def normalize_region_abstract(abstract: JsonObject, *, engine: str) -> JsonObject:
    """Validate only declared regional capabilities and retain resolved aliases.

    Resolving a region as an independent global document used to discard the
    normalized result. It also admitted settings without regional consumers.
    The returned patch is detached and can be inherited by nested regions.
    """
    scalar_options = {
        "max_const_strings",
        "int_domain_max_consts",
        "max_addr_set_size",
        "max_scalar_pairs",
    }
    for name in abstract:
        path = "abstract." + name
        option = CATALOG.get(path)
        if option is None or "region" not in option.scopes:
            raise ConfigurationError(f"{path} has no regional execution consumer")
        if engine == "native" and name not in scalar_options:
            raise ConfigurationError(f"{path} has no native-straightline regional consumer")
    solver = require_object(abstract.get("solver_options", {}))
    unsupported = set(solver) - REGIONAL_SOLVER_OPTIONS
    if unsupported:
        raise ConfigurationError(
            f"abstract.solver_options has no regional consumer for {sorted(unsupported)}"
        )
    _validate_solver(solver)
    validation_input = dict(abstract)
    if "solver_options" in validation_input:
        canonical_solver = dict(solver)
        if "loop_unroll" in canonical_solver:
            canonical_solver["widen_delay"] = canonical_solver.pop("loop_unroll")
        validation_input["solver_options"] = canonical_solver
    resolved = resolve_config(config={"engine": "ai_primary", "abstract": validation_input})
    settings = require_object(resolved.to_dict()["abstract"])
    normalized = {name: settings[name] for name in abstract}
    if "relations" in abstract or "enable_value_partition" in abstract:
        normalized["relations"] = settings["relations"]
        normalized["enable_value_partition"] = settings["enable_value_partition"]
    if "solver_options" in normalized:
        child = dict(require_object(normalized["solver_options"]))
        if "loop_unroll" in child:
            child["widen_delay"] = child.pop("loop_unroll")
        normalized["solver_options"] = child
    return normalized


def inherit_region_abstract(parent: JsonObject, patch: JsonObject) -> JsonObject:
    """Inherit unmentioned settings, including individual solver children."""
    merged = {**parent, **patch}
    if "solver_options" in parent or "solver_options" in patch:
        merged["solver_options"] = {
            **require_object(parent.get("solver_options", {})),
            **require_object(patch.get("solver_options", {})),
        }
    return merged


def _normalize_regions(data: JsonObject) -> None:
    regions: list[JsonValue] = []
    for value in require_array(data["regions"]):
        rule = dict(require_object(value))
        engine = require_string(rule.get("engine", "ai_primary"))
        if engine not in {"native", "ai_primary"}:
            raise ConfigurationError("Region engines are native or ai_primary")
        rule["abstract"] = normalize_region_abstract(
            require_object(rule.get("abstract", {})), engine=engine
        )
        regions.append(rule)
    data["regions"] = regions


def _resolve_config(
    target: ConfigTarget | None,
    config: ConfigInput,
    overrides: Mapping[str, object] | None,
    *,
    queries: Sequence[object] | None,
    output: object,
) -> EffectiveConfig:
    data = default_config()
    provenance = dict.fromkeys(CATALOG, "builtin")
    loaded, path = _configuration_source(target, config)
    raw = _normalize_aliases(loaded)
    preset = raw.get("preset", "balanced")
    if not isinstance(preset, str) or preset not in {"balanced", "precise", "fast"}:
        raise ConfigurationError(f"Unknown preset: {preset}")
    normalized_overrides = (
        _normalize_aliases(_raw_mapping(overrides)) if overrides is not None else {}
    )
    engine = normalized_overrides.get("engine", raw.get("engine", data["engine"]))
    precise: dict[str, JsonValue] = (
        {"andersen.context_policy": "3-cfa"}
        if engine == "native"
        else {"abstract.max_const_strings": 50, "abstract.int_domain_max_consts": 20}
    )
    presets: dict[str, dict[str, JsonValue]] = {
        "balanced": {},
        "precise": precise,
        "fast": {"resources.max_work": 10000},
    }
    for key, value in presets[preset].items():
        _set(data, key, value)
        provenance[key] = "preset:" + preset
    origin = "config:" + str(path) if path else "explicit" if config is not None else "builtin"
    _apply(data, provenance, raw, origin)
    if overrides is not None:
        _apply(data, provenance, normalized_overrides, "override")
    resolved_queries = parse_queries(data["queries"] if queries is None else queries)
    if queries is not None:
        provenance["queries"] = "query_argument"
    data["queries"] = [query.to_dict() for query in resolved_queries]
    if any(query.to_dict().get("kind") == "events" for query in resolved_queries):
        _set(data, "observation.enabled", True)
        provenance["observation.enabled"] = "query_requirement"
    resolved_output = parse_output(data["output"] if output is None else output)
    data["output"] = resolved_output.to_dict()
    if output is not None:
        for key in resolved_output.to_dict():
            provenance["output." + key] = "output_argument"
    if path is not None:
        _relative_paths(data, provenance, path)
    _validate_controls(data, provenance)
    if data["regions"]:
        _normalize_regions(data)
    return EffectiveConfig(
        data,
        provenance,
        queries=resolved_queries,
        output=resolved_output,
        config_file=str(path) if path else None,
    )
