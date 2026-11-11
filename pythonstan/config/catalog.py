"""One source for public defaults, validation and option documentation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from importlib.resources import files
import copy
import json

ENGINES = ("native", "native_then_ai", "ai_primary", "hybrid")
CONTEXT_POLICIES = (
    "0-cfa", "1-cfa", "2-cfa", "3-cfa", "1-obj", "2-obj", "3-obj",
    "1-type", "2-type", "3-type", "1-rcv", "2-rcv", "3-rcv",
    "1-param", "2-param", "3-param", "1c1o", "1c2o", "2c1o",
)


@dataclass(frozen=True)
class OptionDescriptor:
    path: str
    kind: str
    default: object
    consumer: str
    engines: tuple[str, ...] = ENGINES
    minimum: int | float | None = None
    choices: tuple = ()
    configurable: bool = True
    note: str = ""

    def to_dict(self):
        return asdict(self)


def _descriptor(path, default, consumer, **kw):
    kind = "boolean" if type(default) is bool else (
        "integer" if type(default) is int else "number" if type(default) is float
        else "string" if type(default) is str else "array" if isinstance(default, list)
        else "object" if isinstance(default, dict) else "nullable")
    return OptionDescriptor(path, kind, default, consumer, **kw)


def _build():
    legacy = json.loads(files(__package__).joinpath("legacy_defaults.json").read_text())
    result = []
    common = {
        "schema_version": (1, "config.resolve.resolve_config"),
        "preset": ("balanced", "config.resolve.resolve_config"),
        "engine": ("native", "session.AnalysisSession.execute"),
        "semantic_profile": ("python-core-v1", "analysis.engines.abstract.driver.InterproceduralDriver"),
        "inputs.project_root": (None, "inputs.snapshot.snapshot_target"),
        "inputs.source_roots": ([], "inputs.snapshot.snapshot_target"),
        "inputs.exclusions": ([".git", ".venv", "__pycache__", "build", "dist"], "inputs.snapshot.snapshot_target"),
        "inputs.entries": ([], "inputs.snapshot.snapshot_target"),
        "inputs.bindings": ({}, "analysis.engines.abstract.driver.InterproceduralDriver.request_module"),
        "resources.max_work": (100000, "analysis.engines.abstract.driver.InterproceduralDriver"),
        "resources.max_seconds": (30.0, "session.AnalysisSession.execute"),
        "resources.max_memory_mb": (1024, "compat.legacy_runner.run_legacy"),
        "resources.max_reanalyses": (2, "llm.strategy.RefinementController"),
        "regions": ([], "analysis.composition.plan.build_plan"),
        "llm.enabled": (False, "session.AnalysisSession.execute"),
        "llm.level1": (False, "llm.strategy.RefinementController"),
        "llm.level2": (False, "llm.candidates.CandidateProvider"),
        "llm.provider": ("offline", "llm.providers"),
        "llm.proposals": ([], "llm.strategy.RefinementController"),
        "llm.max_proposals": (2, "llm.strategy.RefinementController"),
        "llm.checker_budget": (10000, "analysis.verification.guarded_dispatch.checker"),
        "queries": (["summary"], "session.AnalysisSession.execute"),
        "output.views": (["summary"], "results.schema.AnalysisResult.to_dict"),
        "output.modules": ([], "results.schema.AnalysisResult.to_dict"),
        "output.functions": ([], "results.schema.AnalysisResult.to_dict"),
        "output.regions": ([], "results.schema.AnalysisResult.to_dict"),
        "output.contexts": ([], "results.schema.AnalysisResult.to_dict"),
        "output.fields": ([], "results.schema.AnalysisResult.to_dict"),
        "output.max_items": (10000, "results.schema.AnalysisResult.to_dict"),
        "output.format": ("json", "results.schema.AnalysisResult.write"),
        "observation.enabled": (False, "session.AnalysisSession.execute"),
        "observation.max_events": (10000, "analysis.contracts.observation"),
    }
    for path, (default, consumer) in common.items():
        extra = {}
        if path == "engine": extra["choices"] = ENGINES
        if path == "schema_version": extra["choices"] = (1,)
        if path == "preset": extra["choices"] = ("balanced", "precise", "fast")
        if path == "semantic_profile": extra["choices"] = ("python-core-v1", "python-protocol-v1")
        if path == "output.format": extra["choices"] = ("json", "text")
        if path == "llm.provider": extra["choices"] = ("offline", "recorded", "adversarial")
        if path.startswith("resources.") or path in {"output.max_items", "llm.checker_budget", "observation.max_events"}:
            extra["minimum"] = 0
        result.append(_descriptor(path, default, consumer, **extra))
    fixed_native = {"max_points_to_size", "enable_instrumentation", "build_class_hierarchy",
                    "use_mro_resolution", "max_import_depth", "library_paths", "type"}
    for group, values in legacy.items():
        for name, default in values.items():
            if group == "abstract" and name in {"pta_analysis_name", "plan_scopes", "entry_scope", "type"}:
                configurable = False
            else:
                configurable = not (group == "andersen" and name in fixed_native)
            if group == "andersen" and name in {"enable_ai_intrinsics", "enable_synthetic_method_contexts"}:
                default = False
            consumer = ("analysis.pointer.kcfa.config.Config" if group == "andersen"
                        else "analysis.pointer.ai.config.Config")
            extra = {"configurable": configurable}
            if type(default) is int and name.startswith("max_"): extra["minimum"] = 1
            if name == "context_policy":
                extra["choices"] = CONTEXT_POLICIES + (("inherit",) if group == "abstract" else ())
            result.append(_descriptor(group + "." + name, default, consumer, **extra))
    result += [
        _descriptor("andersen.heap_context_policy", None, "analysis.pointer.kcfa.context_selector.ContextSelector.select_alloc_context"),
        _descriptor("andersen.heap_context_depth", None, "analysis.pointer.kcfa.context_selector.ContextSelector.select_alloc_context"),
        _descriptor("abstract.context_depth", 2, "analysis.engines.abstract.driver.InterproceduralDriver", minimum=0),
        _descriptor("abstract.relations", True, "analysis.engines.abstract.driver.InterproceduralDriver"),
        _descriptor("abstract.int_domain_max_consts", 10, "analysis.domains.bundle.DomainBundle", minimum=1),
        _descriptor("abstract.max_scalar_pairs", 256, "analysis.domains.values.ValueOperations.scalar_binary", minimum=1),
        _descriptor("abstract.max_range_size", 1000, "analysis.engines.abstract.transfer.Transfer.builtin", minimum=1),
    ]
    metadata = json.loads(files(__package__).joinpath("control_metadata.json").read_text())
    native = {row["path"]: row for row in metadata["native_options"] + metadata["native_heap_options"]}
    abstract = {row["new_path"]: row for row in metadata["ai_budget_options"]}
    abstract.update({row["path"]: row for row in metadata["legacy_ai_driver_options"]})
    enriched = []
    for option in result:
        row = native.get(option.path)
        if row:
            engines = tuple(row.get("applies_to_engines", ("native", "native_then_ai")))
            if option.path == "andersen.max_iterations": engines += ("hybrid",)
            option = replace(option, engines=engines,
                             consumer=row.get("actual_consumer", row.get("consumer", option.consumer)),
                             note=row.get("meaning", ""))
        row = abstract.get(option.path)
        if row:
            active = row["new_ai_status"] in {"supported", "supported_subset"}
            option = replace(option, engines=("native_then_ai", "ai_primary", "hybrid") if active else (),
                             consumer=row.get("new_ai_consumer") or option.consumer,
                             note=row.get("new_ai_meaning", row.get("meaning", "")))
        if option.path.startswith("abstract.") and option.path not in abstract:
            option = replace(option, engines=("native_then_ai", "ai_primary", "hybrid"))
        if option.path == "resources.max_memory_mb":
            option = replace(option, consumer="analysis.engines.abstract.driver.InterproceduralDriver.charge / compat.native_worker.main",
                             note="Native child address-space hard cap; new driver sampled resident-memory bound. Zero disables the bound.")
        enriched.append(option)
    return tuple(enriched)


OPTIONS = _build()
CATALOG = {option.path: option for option in OPTIONS}


def default_config():
    result = {}
    for option in OPTIONS:
        node = result
        parts = option.path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = copy.deepcopy(option.default)
    return result


def catalog():
    return [option.to_dict() for option in OPTIONS]


def json_schema():
    result = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False, "properties": {}}
    for option in OPTIONS:
        node = result
        parts = option.path.split(".")
        for part in parts[:-1]:
            node = node.setdefault("properties", {}).setdefault(part, {"type": "object", "additionalProperties": False, "properties": {}})
        value = {"default": copy.deepcopy(option.default), "description": option.note or option.consumer}
        if option.kind != "nullable": value["type"] = option.kind
        if option.choices: value["enum"] = list(option.choices)
        if option.minimum is not None: value["minimum"] = option.minimum
        node.setdefault("properties", {})[parts[-1]] = value
    return result
