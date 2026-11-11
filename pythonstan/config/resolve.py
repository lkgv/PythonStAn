"""Strict, deterministic configuration resolution without constructing a solver."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import copy
import hashlib
import json
import os
import math

from .catalog import CATALOG, default_config


class ConfigurationError(ValueError):
    pass


def _load(path):
    path = Path(path)
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ConfigurationError(f"Duplicate configuration key: {key}")
            value[key] = item
        return value
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            data = json.loads(text, object_pairs_hook=unique)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            import yaml
            class Loader(yaml.SafeLoader): pass
            def mapping(loader, node):
                return unique((loader.construct_object(k), loader.construct_object(v)) for k, v in node.value)
            Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
            data = yaml.load(text, Loader=Loader)
        else:
            raise ConfigurationError("Configuration must be YAML or JSON")
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, ConfigurationError): raise
        raise ConfigurationError(str(exc)) from exc
    except Exception as exc:
        raise ConfigurationError(f"Cannot parse configuration: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ConfigurationError("Configuration must contain an object")
    return dict(data)


def _flatten(value, prefix=""):
    for name, item in value.items():
        if not isinstance(name, str): raise ConfigurationError("Configuration keys must be strings")
        path = prefix + "." + name if prefix else name
        if path in CATALOG:
            yield path, item
        elif isinstance(item, Mapping):
            if not any(k.startswith(path + ".") for k in CATALOG):
                raise ConfigurationError(f"Unknown configuration key: {path}")
            yield from _flatten(item, path)
        else:
            raise ConfigurationError(f"Unknown configuration key: {path}")


def _set(data, path, value):
    parts = path.split(".")
    for part in parts[:-1]: data = data[part]
    data[parts[-1]] = copy.deepcopy(value)


def _get(data, path):
    for part in path.split("."): data = data[part]
    return data


def _normalize_aliases(raw):
    raw = copy.deepcopy(dict(raw))
    for alias, canonical in (("mode", "engine"), ("ai", "abstract"), ("kcfa", "andersen")):
        if alias in raw:
            if canonical in raw: raise ConfigurationError(f"Conflicting alias {alias}/{canonical}")
            raw[canonical] = raw.pop(alias)
    if raw.get("engine") in {"kcfa", "ai"}:
        raw["engine"] = {"kcfa": "native", "ai": "native_then_ai"}[raw["engine"]]
    return raw


def _validate(path, value):
    option = CATALOG[path]
    kind = option.kind
    valid = ((kind == "boolean" and type(value) is bool) or
             (kind == "integer" and type(value) is int) or
             (kind == "number" and type(value) in {int, float}) or
             (kind == "string" and type(value) is str) or
             (kind == "array" and isinstance(value, (list, tuple))) or
             (kind == "object" and isinstance(value, Mapping)) or kind == "nullable")
    if not valid: raise ConfigurationError(f"{path} must have type {kind}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigurationError(f"{path} must be finite")
    if kind == "nullable" and value is not None:
        if path in {"inputs.project_root", "andersen.project_path", "andersen.heap_context_policy"} and not isinstance(value, (str, os.PathLike)):
            raise ConfigurationError(f"{path} must be a path/string or null")
        if path in {"andersen.entry_points", "andersen.library_paths", "abstract.plan_scopes"} and (
                not isinstance(value, (list, tuple)) or any(not isinstance(x, str) for x in value)):
            raise ConfigurationError(f"{path} must be an array of names or null")
    if option.choices and value not in option.choices:
        raise ConfigurationError(f"Unsupported {path}: {value!r}; choose {option.choices}")
    if path in {"inputs.source_roots","inputs.entries","inputs.exclusions","output.views","output.modules","output.functions","output.regions","output.contexts","output.fields"}:
        if any(type(item) is not str for item in value):raise ConfigurationError(f"{path} must contain strings")
    if option.minimum is not None and value < option.minimum:
        raise ConfigurationError(f"{path} must be >= {option.minimum}")
    if not option.configurable and value != option.default:
        raise ConfigurationError(f"{path} is fixed/unsupported; supported value: {option.default!r}")


def _validate_bindings(bindings):
    def scalar(value):
        return type(value) in {str, bool, int, type(None)} or type(value) is float and math.isfinite(value)
    sorts={"str":str,"int":int,"bool":bool,"float":float,"none":type(None)}
    for name, descriptor in bindings.items():
        if not isinstance(name,str) or "." not in name or not name.rsplit(".",1)[1].isidentifier():
            raise ConfigurationError("Input bindings use module.global names")
        if scalar(descriptor):continue
        if not isinstance(descriptor,Mapping):raise ConfigurationError(f"{name}: input must be a scalar or abstract descriptor")
        if set(descriptor)-{"type","top","value","constants","input_contract"}:
            raise ConfigurationError(f"{name}: unsupported abstract input fields")
        sort=descriptor.get("type")
        if sort is not None and sort not in {*sorts,"any","object","list","tuple","dict","set","function"}:
            raise ConfigurationError(f"{name}: unsupported abstract input sort")
        if "top" in descriptor and type(descriptor["top"]) is not bool:
            raise ConfigurationError(f"{name}: top must be boolean")
        finite=[key for key in ("value","constants") if key in descriptor]
        if len(finite)>1 or finite and descriptor.get("top"):
            raise ConfigurationError(f"{name}: finite and Top input forms conflict")
        values=descriptor.get("constants",[descriptor["value"]] if "value" in descriptor else [])
        if not isinstance(values,(list,tuple)) or any(not scalar(value) for value in values):
            raise ConfigurationError(f"{name}: constants must be finite JSON scalars")
        if finite and not values:raise ConfigurationError(f"{name}: empty finite input bound")
        if sort in sorts and any(type(value) is not sorts[sort] for value in values):
            raise ConfigurationError(f"{name}: scalar type conflicts with declared sort")
        if not finite and not descriptor.get("top") and sort is None:
            raise ConfigurationError(f"{name}: abstract input requires a finite value or explicit covering Top")
        if "input_contract" in descriptor and (not isinstance(descriptor["input_contract"],str) or finite):
            raise ConfigurationError(f"{name}: a finite sample cannot claim an arbitrary input contract")


@dataclass(frozen=True)
class EffectiveConfig(Mapping):
    _json: str
    _provenance_json: str
    config_file: str | None = None

    def to_dict(self): return json.loads(self._json)
    @property
    def provenance(self): return json.loads(self._provenance_json)
    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    def __getitem__(self, key): return self.to_dict()[key]
    def __iter__(self): return iter(self.to_dict())
    def __len__(self): return len(self.to_dict())


AnalysisConfig = EffectiveConfig


def resolve_config(target=None, config=None, overrides=None, *, queries=None, output=None):
    defaults = default_config()
    provenance = {key: "builtin" for key in CATALOG}
    path = None
    if isinstance(config, EffectiveConfig):
        raw = config.to_dict()
    elif isinstance(config, Mapping):
        raw = dict(config)
    elif isinstance(config, (str, os.PathLike)):
        path = Path(config).resolve()
        raw = _load(path)
    elif config is None:
        raw = {}
        search_root = getattr(target, "config_search_root", None)
        if search_root is not None:
            directory = Path(search_root).resolve()
            while True:
                candidates = [directory / name for name in ("pythonstan.yaml", "pythonstan.json") if (directory / name).is_file()]
                if len(candidates) > 1: raise ConfigurationError(f"Conflicting default configurations in {directory}")
                if candidates:
                    path = candidates[0]; raw = _load(path); break
                if getattr(target, "kind", "") == "project" or directory.parent == directory: break
                directory = directory.parent
    else:
        raise ConfigurationError("config must be a mapping, AnalysisConfig, or path")
    raw = _normalize_aliases(raw)
    preset = raw.get("preset", "balanced")
    if preset not in ("balanced", "precise", "fast"): raise ConfigurationError(f"Unknown preset: {preset}")
    normalized_overrides=_normalize_aliases(overrides) if isinstance(overrides,Mapping) else {}
    preset_engine=normalized_overrides.get("engine",raw.get("engine",defaults["engine"]))
    precise = {"andersen.context_policy": "3-cfa"} if preset_engine == "native" else {"abstract.max_const_strings": 50, "abstract.int_domain_max_consts": 20}
    presets = {"balanced": {}, "precise": precise,
               "fast": {"resources.max_work": 10000}}
    for key, value in presets[preset].items():
        _set(defaults, key, value); provenance[key] = "preset:" + preset
    def apply(raw_values, origin):
        seen = set()
        for key, value in _flatten(_normalize_aliases(raw_values)):
            if key in seen: raise ConfigurationError(f"Duplicate canonical configuration key: {key}")
            seen.add(key)
            if key in {"inputs.project_root","andersen.project_path","andersen.debug_output_dir"} and isinstance(value,os.PathLike): value=os.fspath(value)
            elif key=="inputs.source_roots" and isinstance(value,(tuple,list)):value=[os.fspath(item) if isinstance(item,os.PathLike) else item for item in value]
            _validate(key, value)
            _set(defaults, key, value); provenance[key] = origin
    apply(raw, "config:" + str(path) if path else "explicit" if config is not None else "builtin")
    if overrides is not None:
        if not isinstance(overrides, Mapping): raise ConfigurationError("overrides must be a mapping")
        apply(overrides, "override")
    if queries is not None:
        _set(defaults, "queries", [q.to_dict() if hasattr(q, "to_dict") else q for q in queries])
        provenance["queries"] = "query_argument"
    if any(q == "events" or isinstance(q, Mapping) and q.get("kind") == "events" for q in defaults["queries"]):
        defaults["observation"]["enabled"] = True
        provenance["observation.enabled"] = "query_requirement"
    if output is not None:
        value = output.to_dict() if hasattr(output, "to_dict") else dict(output)
        base = default_config()["output"]; base.update(value)
        defaults["output"] = base
        for key in base:
            _validate("output." + key, base[key]); provenance["output." + key] = "output_argument"
    if path:
        for key in ("inputs.project_root", "andersen.project_path", "andersen.debug_output_dir"):
            val = _get(defaults, key)
            if val is not None and provenance[key].startswith("config:") and not Path(val).is_absolute():
                _set(defaults, key, str((path.parent / val).resolve()))
        if defaults["inputs"]["source_roots"]:
            defaults["inputs"]["source_roots"] = [str((path.parent / p).resolve()) for p in defaults["inputs"]["source_roots"]]
    enumerations = {
        "andersen.log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
        "abstract.log_level": ("DEBUG", "INFO", "WARNING", "ERROR"),
        "andersen.work_scheduling_policy": ("legacy", "fair"),
        "andersen.points_work_policy": ("fifo", "consumer_fair"),
        "andersen.builtin_bootstrap_policy": ("queued", "eager_inert"),
        "andersen.keyword_context_model": ("none", "receiver_key_v1"),
    }
    for key, allowed in enumerations.items():
        if _get(defaults, key) not in allowed: raise ConfigurationError(f"Unsupported {key}: {_get(defaults, key)!r}")
    solver = defaults["abstract"]["solver_options"]
    allowed_solver = {"widen_delay", "loop_unroll", "loop_iters", "enable_trace_partitioning", "infer_merge_points", "merge_points", "max_traces_per_block", "max_total_traces"}
    if set(solver) - allowed_solver: raise ConfigurationError(f"Unsupported solver options: {sorted(set(solver)-allowed_solver)}")
    for key, value in solver.items():
        if key in {"enable_trace_partitioning", "infer_merge_points"}:
            if type(value) is not bool: raise ConfigurationError(f"solver_options.{key} must be boolean")
        elif key == "merge_points":
            if not isinstance(value, (tuple, list)) or any(type(x) is not int or x < 0 for x in value):
                raise ConfigurationError("merge_points must contain nonnegative block IDs")
        elif key == "loop_iters":
            if not isinstance(value, Mapping) or any((not (type(k) is int and k >= 0 or isinstance(k, str) and k.isdecimal())) or type(v) is not int or v < 0 for k, v in value.items()):
                raise ConfigurationError("loop_iters maps block IDs to nonnegative delays")
        elif type(value) is not int or value < (1 if key.startswith("max_") else 0):
            raise ConfigurationError(f"solver_options.{key} is outside its supported range")
    if "widen_delay" in solver and "loop_unroll" in solver and solver["widen_delay"] != solver["loop_unroll"]:
        raise ConfigurationError("widen_delay and loop_unroll aliases conflict")
    from pythonstan.analysis.contracts.budgets import validate_budget_options
    try: validate_budget_options(defaults["andersen"]["ai_budget"])
    except (ValueError,TypeError) as exc:raise ConfigurationError(str(exc)) from exc
    for key in ("heap_context_depth",):
        v = defaults["andersen"][key]
        if v is not None and (type(v) is not int or not 0 <= v <= 3): raise ConfigurationError(f"{key} must be 0..3 or null")
    if defaults["andersen"]["heap_context_policy"] is not None and defaults["andersen"]["heap_context_policy"] not in CATALOG["andersen.context_policy"].choices:
        raise ConfigurationError("Unsupported heap_context_policy")
    if defaults["llm"]["enabled"] and not (defaults["llm"]["level1"] or defaults["llm"]["level2"]):
        raise ConfigurationError("Enabled LLM requires level1 or level2")
    # A selectable field is an operational promise. Keep legacy defaults for
    # round-trip compatibility, but never silently accept an inactive override.
    from importlib.resources import files
    metadata = json.loads(files(__package__).joinpath("control_metadata.json").read_text())
    for row in metadata["ai_budget_options"]:
        name=row["old_name"]
        if row.get("legacy_status")=="retired_reject_nondefault" and name in defaults["andersen"]["ai_budget"] and defaults["andersen"]["ai_budget"][name]!=row["old_default"]:
            raise ConfigurationError(f"andersen.ai_budget.{name} is retired for the shared core")
    _validate_bindings(defaults["inputs"]["bindings"])
    engine = defaults["engine"]
    builtin = default_config()
    for row in metadata["native_options"]:
        key = row["path"]
        value = _get(defaults, key)
        changed = value != _get(builtin, key)
        inactive = engine not in CATALOG[key].engines
        unavailable = row["public_recommendation"] == "reject_nondefault_or_explicit_entry_contract"
        if changed and (inactive or unavailable):
            raise ConfigurationError(f"{key} has no active public consumer for {engine}; use inputs.entries/source_roots for input contracts")
    for key in ("andersen.heap_context_policy", "andersen.heap_context_depth"):
        if engine not in ("native", "native_then_ai") and _get(defaults, key) != _get(builtin, key):
            raise ConfigurationError(f"{key} is supported by native/native_then_ai only")
    for key, descriptor in CATALOG.items():
        if key.startswith("abstract.") and key != "abstract.relations" and _get(defaults, key) != _get(builtin, key) and engine not in descriptor.engines:
            raise ConfigurationError(f"{key} has no active public consumer for {engine}; legacy intrinsic budgets use andersen.ai_budget")
    if engine in ("native_then_ai", "ai_primary", "hybrid"):
        for row in metadata["solver_options"]:
            name = row["path"].rsplit(".", 1)[-1]
            if name in solver and row["new_ai_status"] != "supported" and solver[name] != row["legacy_default"]:
                raise ConfigurationError(f"{row['path']} is not implemented by the selected core")
    # relations is a public spelling of the actual partition switch. Resolve
    # aliases per explicit provenance instead of leaving a policy-only flag.
    rel_key, partition_key = "abstract.relations", "abstract.enable_value_partition"
    explicit_rel = provenance[rel_key] not in ("builtin",)
    explicit_partition = provenance[partition_key] not in ("builtin",)
    if explicit_rel:
        if explicit_partition and _get(defaults, rel_key) != _get(defaults, partition_key):
            raise ConfigurationError("abstract.relations conflicts with enable_value_partition")
        if engine == "native" and _get(defaults, rel_key) != _get(builtin, rel_key):
            raise ConfigurationError("abstract.relations requires an abstract core")
        _set(defaults, partition_key, _get(defaults, rel_key))
    else:
        _set(defaults, rel_key, _get(defaults, partition_key))
    if engine == "native" and defaults["llm"]["enabled"]:
        raise ConfigurationError("LLM policies and checked summaries require native_then_ai, ai_primary, or hybrid")
    from pythonstan.results import normalize_query
    from pythonstan.results.schema import VIEWS
    try:
        for query in defaults["queries"]:normalize_query(query)
    except (ValueError,TypeError,KeyError) as exc:raise ConfigurationError(str(exc)) from exc
    if set(defaults["output"]["views"])-set(VIEWS):raise ConfigurationError("Unknown output views")
    return EffectiveConfig(json.dumps(defaults, sort_keys=True), json.dumps(provenance, sort_keys=True), str(path) if path else None)
