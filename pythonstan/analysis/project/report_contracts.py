"""Records shared by project reporting and transient frontend ownership."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict

from pythonstan.analysis.contracts.json_data import JsonObject

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver


class DeferredModule(TypedDict):
    module: str
    reasons: list[str]
    scope_count: int


class FrontendLifetimeReport(TypedDict):
    requested_policy: str
    effective_policy: str
    canonical_ir_preserved: bool
    block_cfg_preserved: bool
    statement_cfg_inspection_available: bool
    module_closure_modules: NotRequired[int]
    module_closure_scopes: NotRequired[int]
    retired_statement_cfgs: NotRequired[int]
    deferred_modules: NotRequired[list[DeferredModule]]
    final_closure_scopes: NotRequired[int]
    ownership_valid: NotRequired[bool]


type ProjectConfigValue = str | bool | int | tuple[str, ...] | tuple[tuple[str, str], ...]
type ProjectConfigWire = dict[str, ProjectConfigValue]


class ModuleRecord(TypedDict):
    path: str
    module: str | None
    status: str
    ownership: NotRequired[str]
    bytes: NotRequired[int]
    sha256: NotRequired[str]
    module_name_syntax: NotRequired[str]
    parsed: NotRequired[bool]
    lowered: NotRequired[bool]
    translated: NotRequired[bool]
    solver_constraints_scheduled: NotRequired[bool]
    consumed_sha256: NotRequired[str]
    consumed_bytes: NotRequired[int]
    source_read_matches_inventory: NotRequired[bool]
    source_stable: NotRequired[bool]
    diagnostic: NotRequired[str]


class InspectorFailure(TypedDict):
    status: str
    errors: list[dict[str, str]]


class ScopeCoverage(TypedDict):
    name: str
    kind: str
    translated: bool
    constraints_scheduled: bool
    call_graph_contexts: int


class ProjectReport(TypedDict, total=False):
    schema_version: int
    analysis_unit: str
    solver_completed: bool
    semantic_complete: bool
    status: str
    effective_config: ProjectConfigWire
    modules: list[ModuleRecord]
    excluded_files: list[str]
    source_snapshot_stable: bool
    implementation_sha256_before: str
    implementation_sha256_after: str | None
    implementation_stable: bool
    call_edges: list[list[str]]
    import_edges: list[list[str]]
    dependency_boundaries: list[JsonObject]
    frontend_errors: list[dict[str, str | None]]
    entry_roots: list[JsonObject]
    unresolved_entries: list[str]
    analysis_executed: bool
    whole_program_soundness_proved: bool
    analysis_complete: bool
    runtime_observations_used: bool
    target_source_executed: bool
    coverage_interpretation: str
    boundary_policy: str
    requested_analysis_stage: str
    frontend_lifetime: FrontendLifetimeReport
    configuration_sha256: str
    analysis_error: str
    analysis_failure_phase: str | None
    framework_entry_discovery: JsonObject
    resolved_entry_points: list[str]
    framework_callback_entry_contracts: list[JsonObject]
    source_scope_entry_assumptions: list[str]
    boundary_contract_applications: list[JsonObject]
    exported_entry_activation: list[JsonObject]
    statistics: JsonObject
    semantic_status: JsonObject
    unknown_records: list[JsonObject]
    residual_effects: list[dict[str, object]]
    scope_coverage: list[ScopeCoverage]
    constraint_fixpoint_reached: bool
    coverage: dict[str, bool | int]
    inventory: list[ModuleRecord]
    entry_points: list[str]
    first_party_translation_complete: bool
    frontend_executed: bool
    synthetic_method_contexts_enabled: bool
    synthetic_method_context_policy: str
    implementation_verification_error: str
    wall_seconds: float
    inspector: InspectorFailure


type ProjectProgress = Callable[[JsonObject], None]


class ProjectDiagnostic(Protocol):
    def solver_ready(self, solver: PointerSolver) -> None: ...

    def project_finished(self, solver: PointerSolver | None, report: ProjectReport) -> None: ...
