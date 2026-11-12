"""Publication assembly from checked run facts, separate from engine execution."""

from __future__ import annotations

from collections.abc import Sequence

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    JsonValue,
    require_array,
    require_int,
    require_number,
    require_object,
    thaw_json_object,
    validate_json,
)
from pythonstan.inputs import SourceSnapshot
from pythonstan.request import ResolvedRequest
from pythonstan.results.queries import PropertyQuery, QuerySpec, resolved_query_key


def attach_checked_properties(
    result: JsonObject,
    *,
    records: Sequence[JsonObject],
    queries: tuple[QuerySpec, ...],
    source_digest: str,
    statistics: JsonObject,
) -> None:
    properties: JsonObject = {}
    query_proofs: JsonObject = {}
    for query in queries:
        if not isinstance(query, PropertyQuery):
            continue
        selected = [
            row
            for row in records
            if row.get("property") == query.property
            and (query.point == "all" or row.get("point") == query.point)
        ]
        status = (
            "verified"
            if selected and all(row.get("status") == "verified" for row in selected)
            else "unknown"
        )
        proof: JsonObject = {
            "status": status,
            "property": query.property,
            "point": query.point,
            "profile": "guarded-dispatch-v1",
            "records": list(selected),
            "scope": "Independently checked source initialization call inputs",
            "source_digest": source_digest,
        }
        query_proofs[resolved_query_key(query)] = proof
        previous = properties.get(query.property)
        if previous is None or status != "verified":
            properties[query.property] = proof
    result["proof"] = {"properties": properties, "queries": query_proofs, "records": list(records)}
    require_object(result.setdefault("llm", {}))["level2"] = statistics


def attach_request_metadata(
    result: JsonObject,
    *,
    request: ResolvedRequest,
    snapshot: SourceSnapshot,
    execution_snapshot: SourceSnapshot,
    run_id: str,
    native_phase: JsonObject | None,
    preparation_seconds: float,
    snapshot_reuse_validation_seconds: float,
    engine_seconds: float,
    total_seconds: float,
) -> None:
    config = request.config
    result.update(
        schema_version=1,
        run_id=run_id,
        source_digest=snapshot.source_digest,
        model_digest="pythonstan-builtins-v1",
        policy_digest=config.digest,
        target=thaw_json_object(snapshot.target),
        inventory=[thaw_json_object(row) for row in snapshot.inventory],
        entry_contract=snapshot.entry_contract,
        effective_config=config.to_dict(),
        config_provenance=validate_json(config.provenance),
        actual_engine=config.engine,
        profile=config.semantic_profile,
    )
    entry_contract = require_object(result["entry_contract"])
    entry_contract["execution_order"] = [
        entry.scenario_id for entry in execution_snapshot.scenarios
    ]
    if config.engine == "native" and result.get("synthetic_entry_contract"):
        entry_contract["entry_coverage"] = "initialization_and_synthetic_method_contexts"
        entry_contract["synthetic_method_contexts"] = result["synthetic_entry_contract"]
    elif (
        config.engine == "native_then_ai"
        and native_phase
        and native_phase.get("synthetic_entry_contract")
    ):
        entry_contract["native_prepass_synthetic_method_contexts"] = native_phase[
            "synthetic_entry_contract"
        ]
        entry_contract["final_query_scope"] = (
            "Declared source-driver scenarios only; Native synthetic contexts are additional prepass candidates, not AI external-input coverage."
        )
    requested_properties = {
        query.property for query in request.queries if isinstance(query, PropertyQuery)
    }
    proof = require_object(result.setdefault("proof", {"properties": {}}))
    properties = require_object(proof.setdefault("properties", {}))
    for name in requested_properties:
        properties.setdefault(
            name,
            {"status": "unknown", "reason": "Requested property has no closed source certificate"},
        )
    result["proof_status"] = (
        "verified"
        if requested_properties
        and all(
            require_object(properties[name])["status"] == "verified"
            for name in requested_properties
        )
        else "unknown"
        if requested_properties
        else "not_requested"
    )
    result.setdefault("diagnostics", [])
    result.setdefault("obligations", [])
    stats = require_object(result.setdefault("stats", {}))
    stats["total_wall_seconds"] = total_seconds
    stats["configuration_resolution_seconds"] = request.resolution_seconds
    stats["configuration_resolution_timing_status"] = (
        "measured" if request.resolution_seconds is not None else "unavailable_manual_request"
    )
    if request.resolution_seconds is None:
        stats["configuration_resolution_missing_reason"] = (
            "ResolvedRequest was constructed outside resolve_request"
        )
    stats["source_preparation_seconds"] = preparation_seconds
    stats["snapshot_reuse_validation_seconds"] = snapshot_reuse_validation_seconds
    stats["engine_execution_seconds"] = engine_seconds
    stats["phase_timing_scope"] = {
        "configuration_resolution_seconds": "resolve_request configuration and DTO resolution only",
        "source_preparation_seconds": "source inventory, parser cache ownership, SourceProgram and optional plan preparation only",
        "snapshot_reuse_validation_seconds": "frozen-source identity and entry/selector checks only",
        "engine_execution_seconds": "engine pipeline including native prepass, driver/provider setup, solving and engine publication",
        "total_wall_seconds": "request start through pre-publication metadata; includes idle between explicit stages",
    }
    result.setdefault("evidence", {"source_digest": snapshot.source_digest})


def attach_l1_feedback(
    result: JsonObject,
    *,
    feedback: JsonObject,
    requested_config: JsonObject,
    baseline_stats: JsonObject,
    baseline_trace: list[JsonValue],
    total_seconds: float,
) -> None:
    result["requested_config"] = requested_config
    require_object(result.setdefault("llm", {}))["level1"] = feedback
    stats = require_object(result.setdefault("stats", {}))
    stats["baseline_before_l1"] = baseline_stats
    stats["l1_seconds"] = feedback["seconds"]
    stats["total_work"] = feedback["total_work"]
    stats["total_wall_seconds"] = total_seconds
    trace: list[JsonValue] = list(baseline_trace)
    proposal_seconds = 0.0
    for index, value in enumerate(require_array(feedback["proposals"])):
        row = require_object(value)
        proposal_seconds += require_number(row.get("seconds", 0))
        for phase_value in require_array(row.get("phase_trace", [])):
            phase = dict(require_object(phase_value))
            phase["l1_rerun"] = index
            trace.append(phase)
    trace.append(
        {
            "phase": "l1_policy_control",
            "attempted": require_int(feedback["attempted"]),
            "seconds_excluding_reruns": max(
                0, require_number(feedback["seconds"]) - proposal_seconds
            ),
        }
    )
    result["phase_trace"] = trace
