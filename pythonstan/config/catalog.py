"""One typed source for public defaults, validation and option documentation."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from importlib.resources import files
from typing import TypedDict

from pythonstan.analysis.contracts.json_data import (
    FrozenJsonValue,
    JsonObject,
    JsonScalar,
    JsonValue,
    freeze_json,
    require_array,
    require_object,
    require_string,
    thaw_json,
    validate_json_object,
)

ENGINES = ("native", "native_then_ai", "ai_primary", "hybrid")
CONTEXT_POLICIES = (
    "0-cfa",
    "1-cfa",
    "2-cfa",
    "3-cfa",
    "1-obj",
    "2-obj",
    "3-obj",
    "1-type",
    "2-type",
    "3-type",
    "1-rcv",
    "2-rcv",
    "3-rcv",
    "1-param",
    "2-param",
    "3-param",
    "1c1o",
    "1c2o",
    "2c1o",
)


class CatalogRecord(TypedDict):
    path: str
    kind: str
    default: JsonValue
    consumer: str
    engines: tuple[str, ...]
    minimum: int | float | None
    choices: tuple[JsonScalar, ...]
    configurable: bool
    note: str
    scopes: tuple[str, ...]
    capabilities: tuple[str, ...]
    version: str
    effect: str
    evidence: tuple[str, ...]
    nested_options: tuple[JsonObject, ...]


@dataclass(frozen=True)
class OptionDescriptor:
    path: str
    kind: str
    default: FrozenJsonValue
    consumer: str
    engines: tuple[str, ...] = ENGINES
    minimum: int | float | None = None
    choices: tuple[JsonScalar, ...] = ()
    configurable: bool = True
    note: str = ""
    scopes: tuple[str, ...] = ("global",)
    capabilities: tuple[str, ...] = ()
    version: str = "python-core-v1"
    effect: str = "global_configuration"
    evidence: tuple[str, ...] = ("tests/refactor/api/test_control_applicability.py",)

    def to_dict(self) -> CatalogRecord:
        """Emit a detached record while preserving the public tuple fields."""
        return {
            "path": self.path,
            "kind": self.kind,
            "default": thaw_json(self.default),
            "consumer": self.consumer,
            "engines": self.engines,
            "minimum": self.minimum,
            "choices": self.choices,
            "configurable": self.configurable,
            "note": self.note,
            "scopes": self.scopes,
            "capabilities": self.capabilities,
            "version": self.version,
            "effect": self.effect,
            "evidence": self.evidence,
            "nested_options": tuple(regional_solver_catalog())
            if self.path == "abstract.solver_options"
            else (),
        }


# Only settings with explicit execution-context consumers can vary regionally.
# Solver child capabilities are checked individually, not inherited from their
# object-valued parent option.
REGIONAL_ABSTRACT_OPTIONS = frozenset(
    {
        "max_const_strings",
        "int_domain_max_consts",
        "max_addr_set_size",
        "max_scalar_pairs",
        "context_depth",
        "relations",
        "enable_value_partition",
        "max_value_partitions",
        "refine_booleans",
        "attribute_name_limit",
        "max_range_size",
        "max_stmt_visits",
        "enable_narrowing",
        "narrowing_iterations",
        "solver_options",
    }
)
_REGIONAL_SOLVER_CONSUMERS: dict[str, str] = {
    "widen_delay": "analysis.engines.abstract.transfer.Transfer.while_loop",
    "loop_unroll": "config.resolve.normalize_region_abstract -> widen_delay",
    "max_traces_per_block": "analysis.engines.abstract.driver.InterproceduralDriver.compact",
}
REGIONAL_SOLVER_OPTIONS = frozenset(_REGIONAL_SOLVER_CONSUMERS)


def regional_solver_catalog() -> list[JsonObject]:
    """Child capabilities belong to the same registry as their parent schema."""
    return [
        {
            "path": "abstract.solver_options." + name,
            "scopes": ["region"],
            "capabilities": ["ai_primary", "native_then_ai", "hybrid"],
            "consumer": consumer,
            "version": "python-core-v1",
            "effect": "regional_precision_budget",
            "evidence": ["tests/regression/audit3/test_region_policy_execution.py"],
        }
        for name, consumer in _REGIONAL_SOLVER_CONSUMERS.items()
    ]


def _descriptor(
    path: str,
    default: JsonValue,
    consumer: str,
    *,
    engines: tuple[str, ...] = ENGINES,
    minimum: int | float | None = None,
    choices: tuple[JsonScalar, ...] = (),
    configurable: bool = True,
    note: str = "",
) -> OptionDescriptor:
    if isinstance(default, bool):
        kind = "boolean"
    elif isinstance(default, int):
        kind = "integer"
    elif isinstance(default, float):
        kind = "number"
    elif isinstance(default, str):
        kind = "string"
    elif isinstance(default, list):
        kind = "array"
    elif isinstance(default, dict):
        kind = "object"
    else:
        kind = "nullable"
    return OptionDescriptor(
        path, kind, freeze_json(default), consumer, engines, minimum, choices, configurable, note
    )


def _resource(name: str) -> JsonObject:
    raw: object = json.loads(files("pythonstan.config").joinpath(name).read_text(encoding="utf-8"))
    return validate_json_object(raw)


def _text_field(row: JsonObject, name: str, default: str = "") -> str:
    return require_string(row.get(name, default))


def _rows(metadata: JsonObject, name: str) -> tuple[JsonObject, ...]:
    return tuple(require_object(value) for value in require_array(metadata[name]))


def _engines(row: JsonObject) -> tuple[str, ...]:
    if "applies_to_engines" not in row:
        return ("native", "native_then_ai")
    return tuple(require_string(value) for value in require_array(row["applies_to_engines"]))


def _common_options() -> list[OptionDescriptor]:
    common: dict[str, tuple[JsonValue, str]] = {
        "schema_version": (1, "config.resolve.resolve_config"),
        "preset": ("balanced", "config.resolve.resolve_config"),
        "engine": ("hybrid", "session.AnalysisSession.execute"),
        "semantic_profile": (
            "python-core-v1",
            "analysis.engines.abstract.driver.InterproceduralDriver",
        ),
        "inputs.project_root": (None, "inputs.snapshot.snapshot_target"),
        "inputs.source_roots": ([], "inputs.snapshot.snapshot_target"),
        "inputs.exclusions": (
            [".git", ".venv", "__pycache__", "build", "dist"],
            "inputs.snapshot.snapshot_target",
        ),
        "inputs.entries": ([], "inputs.snapshot.snapshot_target"),
        "inputs.bindings": (
            {},
            "analysis.engines.abstract.driver.InterproceduralDriver.request_module",
        ),
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
        "llm.model": ("gpt-6-luna", "llm.openai_transport.OpenAIResponsesTransport"),
        "llm.timeout_seconds": (30.0, "llm.openai_transport.OpenAIResponsesTransport"),
        "llm.max_output_tokens": (2048, "llm.openai_transport.OpenAIResponsesTransport"),
        "llm.max_input_bytes": (1000000, "llm.responses.ResponsesCandidateProposer"),
        "llm.max_response_bytes": (131072, "llm.responses.parse_response_candidate"),
        "llm.max_json_depth": (24, "llm.responses.parse_response_candidate"),
        "llm.max_calls": (1, "llm.responses.ResponsesCandidateProposer"),
        "llm.max_total_tokens": (8192, "llm.responses.ResponsesCandidateProposer"),
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
    choices_by_path: dict[str, tuple[JsonScalar, ...]] = {
        "engine": ENGINES,
        "schema_version": (1,),
        "preset": ("balanced", "precise", "fast"),
        "semantic_profile": ("python-core-v1", "python-protocol-v1"),
        "output.format": ("json", "text"),
        "llm.provider": ("disabled", "offline", "recorded", "replay", "adversarial", "openai_live"),
        "llm.model": ("gpt-6-luna",),
    }
    bounded = {
        "output.max_items",
        "llm.checker_budget",
        "llm.max_proposals",
        "observation.max_events",
    }
    return [
        _descriptor(
            path,
            default,
            consumer,
            choices=choices_by_path.get(path, ()),
            minimum=0 if path.startswith("resources.") or path in bounded else None,
        )
        for path, (default, consumer) in common.items()
    ]


def _legacy_options() -> list[OptionDescriptor]:
    legacy = _resource("legacy_defaults.json")
    fixed_native = {
        "max_points_to_size",
        "enable_instrumentation",
        "build_class_hierarchy",
        "use_mro_resolution",
        "max_import_depth",
        "library_paths",
        "type",
    }
    fixed_abstract = {"pta_analysis_name", "plan_scopes", "entry_scope", "type"}
    result: list[OptionDescriptor] = []
    for group, value in legacy.items():
        for name, raw_default in require_object(value).items():
            default = raw_default
            configurable = not (
                group == "abstract"
                and name in fixed_abstract
                or group == "andersen"
                and name in fixed_native
            )
            if group == "andersen" and name in {
                "enable_ai_intrinsics",
                "enable_synthetic_method_contexts",
            }:
                default = False
            consumer = (
                "analysis.pointer.kcfa.config.Config"
                if group == "andersen"
                else "analysis.pointer.ai.config.Config"
            )
            minimum: int | None = None
            if type(default) is int and name.startswith("max_"):
                minimum = (
                    0
                    if group == "abstract" and name in {"max_iterations", "max_stmt_visits"}
                    else 1
                )
            if group == "abstract" and name == "narrowing_iterations":
                minimum = 0
            choices: tuple[JsonScalar, ...] = ()
            if name == "context_policy":
                choices = CONTEXT_POLICIES + (("inherit",) if group == "abstract" else ())
            result.append(
                _descriptor(
                    f"{group}.{name}",
                    default,
                    consumer,
                    configurable=configurable,
                    minimum=minimum,
                    choices=choices,
                )
            )
    return result


def _additional_options() -> tuple[OptionDescriptor, ...]:
    return (
        _descriptor(
            "andersen.heap_context_policy",
            None,
            "analysis.pointer.kcfa.context_selector.ContextSelector.select_alloc_context",
        ),
        _descriptor(
            "andersen.heap_context_depth",
            None,
            "analysis.pointer.kcfa.context_selector.ContextSelector.select_alloc_context",
        ),
        _descriptor(
            "abstract.context_depth",
            2,
            "analysis.engines.abstract.driver.InterproceduralDriver",
            minimum=0,
        ),
        _descriptor(
            "abstract.relations",
            True,
            "config.resolve.resolve_config",
            note="Alias of enable_value_partition; conflicting explicit values reject.",
        ),
        _descriptor(
            "abstract.refine_booleans",
            True,
            "analysis.engines.abstract.transfer.Transfer.refine_test",
            note="State cofactoring; protocol truth evaluation always executes.",
        ),
        _descriptor(
            "abstract.attribute_name_limit",
            256,
            "analysis.engines.abstract.attributes.AttributeOperations.reflective",
            minimum=0,
            note="Reflective name enumeration window with covering residuals.",
        ),
        _descriptor(
            "abstract.int_domain_max_consts",
            10,
            "analysis.domains.values.ValueOperations.__init__",
            minimum=1,
        ),
        _descriptor(
            "abstract.max_scalar_pairs",
            256,
            "analysis.domains.values.ValueOperations.scalar_binary",
            minimum=1,
        ),
        _descriptor(
            "abstract.max_range_size",
            1000,
            "analysis.engines.abstract.transfer.Transfer.builtin",
            minimum=1,
        ),
    )


def _enrich_native(option: OptionDescriptor, row: JsonObject) -> OptionDescriptor:
    engines = _engines(row)
    if option.path == "andersen.max_iterations":
        engines += ("hybrid",)
    consumer = _text_field(row, "actual_consumer", _text_field(row, "consumer", option.consumer))
    return replace(
        option,
        engines=engines,
        consumer=consumer,
        note=_text_field(row, "meaning"),
        configurable=False
        if row.get("public_recommendation") == "reject_nondefault_or_explicit_entry_contract"
        else option.configurable,
    )


def _enrich_abstract(option: OptionDescriptor, row: JsonObject) -> OptionDescriptor:
    active = require_string(row["new_ai_status"]) in {"supported", "supported_subset"}
    consumer = row.get("new_ai_consumer")
    if consumer is not None:
        consumer = require_string(consumer)
    return replace(
        option,
        engines=("native_then_ai", "ai_primary", "hybrid") if active else (),
        consumer=consumer or option.consumer,
        note=_text_field(row, "new_ai_meaning", _text_field(row, "meaning")),
    )


def _build() -> tuple[OptionDescriptor, ...]:
    result = [*_common_options(), *_legacy_options(), *_additional_options()]
    metadata = _resource("control_metadata.json")
    native = {
        require_string(row["path"]): row
        for row in (*_rows(metadata, "native_options"), *_rows(metadata, "native_heap_options"))
    }
    abstract = {
        require_string(row["new_path"]): row for row in _rows(metadata, "ai_budget_options")
    }
    abstract.update(
        {require_string(row["path"]): row for row in _rows(metadata, "legacy_ai_driver_options")}
    )
    enriched: list[OptionDescriptor] = []
    for option in result:
        if option.path in native:
            option = _enrich_native(option, native[option.path])
        if option.path in abstract:
            option = _enrich_abstract(option, abstract[option.path])
        elif option.path.startswith("abstract."):
            option = replace(option, engines=("native_then_ai", "ai_primary", "hybrid"))
        if option.path == "resources.max_memory_mb":
            option = replace(
                option,
                consumer="analysis.engines.abstract.driver.InterproceduralDriver.charge / compat.native_worker.main",
                note="Native child address-space hard cap; new driver sampled resident-memory bound. Zero disables the bound.",
            )
        regional = option.path.removeprefix("abstract.") in REGIONAL_ABSTRACT_OPTIONS
        option = replace(
            option,
            scopes=("global", "region") if regional else ("global",),
            capabilities=option.engines,
            effect="regional_semantics" if regional else "global_configuration",
            evidence=("tests/regression/audit3/test_region_policy_execution.py",)
            if regional
            else option.evidence,
        )
        enriched.append(option)
    return tuple(enriched)


OPTIONS: tuple[OptionDescriptor, ...] = _build()
CATALOG: dict[str, OptionDescriptor] = {option.path: option for option in OPTIONS}


def _child_object(parent: JsonObject, key: str) -> JsonObject:
    value = parent.setdefault(key, {})
    return require_object(value)


def default_config() -> JsonObject:
    result: JsonObject = {}
    for option in OPTIONS:
        node = result
        parts = option.path.split(".")
        for part in parts[:-1]:
            node = _child_object(node, part)
        node[parts[-1]] = thaw_json(option.default)
    return result


def catalog() -> list[CatalogRecord]:
    return [option.to_dict() for option in OPTIONS]


def _schema_value(option: OptionDescriptor) -> JsonObject:
    value: JsonObject = {
        "default": thaw_json(option.default),
        "description": option.note or option.consumer,
        "x-supported-engines": list(option.engines),
        "x-configurable": option.configurable,
        "x-scopes": list(option.scopes),
        "x-capabilities": list(option.capabilities),
        "x-consumer": option.consumer,
        "x-version": option.version,
        "x-effect": option.effect,
        "x-evidence": list(option.evidence),
    }
    if option.path == "abstract.solver_options":
        value["x-region-children"] = list[JsonValue](sorted(REGIONAL_SOLVER_OPTIONS))
        value["x-region-child-options"] = list[JsonValue](regional_solver_catalog())
    if option.kind != "nullable":
        value["type"] = option.kind
    elif option.path in {
        "inputs.project_root",
        "andersen.project_path",
        "andersen.debug_output_dir",
    }:
        value["type"] = ["null", "string"]
    elif option.path == "andersen.heap_context_depth":
        value.update({"type": ["null", "integer"], "minimum": 0, "maximum": 3})
    elif option.path == "andersen.heap_context_policy":
        value.update({"type": ["null", "string"], "enum": [None, *CONTEXT_POLICIES]})
    elif option.path in {
        "andersen.entry_points",
        "andersen.library_paths",
        "abstract.plan_scopes",
    }:
        value["type"] = ["null", "array"]
    if option.choices:
        value["enum"] = list(option.choices)
    if option.minimum is not None:
        value["minimum"] = option.minimum
    if not option.configurable:
        value["const"] = thaw_json(option.default)
    return value


def _unsupported_condition(option: OptionDescriptor, unsupported: list[str]) -> JsonObject:
    condition: JsonObject = {
        "properties": {"engine": {"enum": list(unsupported)}},
        "required": ["engine"],
    }
    if CATALOG["engine"].default in unsupported:
        condition = {"anyOf": [condition, {"not": {"required": ["engine"]}}]}
    leaf: JsonObject = {"const": thaw_json(option.default)}
    for part in reversed(option.path.split(".")):
        leaf = {"properties": {part: leaf}}
    return {"if": condition, "then": leaf}


def json_schema() -> JsonObject:
    result: JsonObject = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }
    for option in OPTIONS:
        node = result
        parts = option.path.split(".")
        for part in parts[:-1]:
            properties = _child_object(node, "properties")
            node = require_object(
                properties.setdefault(
                    part, {"type": "object", "additionalProperties": False, "properties": {}}
                )
            )
        _child_object(node, "properties")[parts[-1]] = _schema_value(option)
    constraints: list[JsonValue] = []
    for option in OPTIONS:
        unsupported = [engine for engine in ENGINES if engine not in option.engines]
        if unsupported:
            constraints.append(_unsupported_condition(option, unsupported))
    if constraints:
        result["allOf"] = constraints
    return result
