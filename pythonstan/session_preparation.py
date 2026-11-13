"""Explicit session-owned source preparation after request resolution."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.cost_model import visit_json_text
from pythonstan.analysis.contracts.identity import EntryScenario
from pythonstan.analysis.contracts.json_data import require_object, thaw_json_object
from pythonstan.analysis.contracts.resources import RunLedger
from pythonstan.analysis.contracts.wire import thaw_snapshot
from pythonstan.config.errors import ConfigurationError
from pythonstan.frontend.source import SourceProgram, parse_sources
from pythonstan.inputs import File, InputError, Project, Source, SourceSnapshot, snapshot_target
from pythonstan.request import ResolvedRequest
from pythonstan.results.queries import ValueQuery

if TYPE_CHECKING:
    from pythonstan.analysis.composition.plan import EffectiveRegionPlan


@dataclass(frozen=True)
class PreparedRun:
    snapshot: SourceSnapshot
    execution_snapshot: SourceSnapshot
    program: SourceProgram
    plan: EffectiveRegionPlan | None
    preparation_seconds: float


def validate_snapshot_request(
    request: ResolvedRequest, snapshot: SourceSnapshot, *, resource_ledger: RunLedger | None = None
) -> None:
    """A frozen-source rerun cannot silently change its source or entry scope."""
    from pythonstan.inputs.reuse import input_resolution_contract

    target = request.target
    metadata = snapshot.target
    if not snapshot.input_resolution:
        raise InputError("Frozen source snapshot has no input resolution contract")
    if isinstance(target, File):
        recorded_target = snapshot.input_resolution.get("target")
        if not isinstance(recorded_target, Mapping) or "module" not in recorded_target:
            raise InputError("Frozen source snapshot lacks File module resolution identity")
        if recorded_target["module"] != (target.module or None):
            raise InputError("File module override does not match the frozen source snapshot")
    if resource_ledger is None:
        retained_resolution = thaw_json_object(snapshot.input_resolution)
    else:
        resource_ledger.charge_work(1, "snapshot_selector_validation_dispatch")
        with resource_ledger.structural_meter("snapshot_selector_validation") as meter:
            inputs = request.config.inputs
            for text in chain(inputs.source_roots, inputs.exclusions, inputs.entries):
                visit_json_text(text, meter.visit)
            if inputs.project_root is not None:
                visit_json_text(inputs.project_root, meter.visit)
            if isinstance(target, Source):
                visit_json_text(target.code, meter.visit)
                visit_json_text(target.module, meter.visit)
                visit_json_text(target.filename, meter.visit)
                for path in target.import_roots:
                    visit_json_text(str(path), meter.visit)
            elif isinstance(target, Project):
                for path in target.source_roots:
                    visit_json_text(str(path), meter.visit)
                for text in chain(target.exclusions, target.entries):
                    visit_json_text(text, meter.visit)
            retained_resolution = require_object(
                thaw_snapshot(snapshot.input_resolution, visit=meter.visit)
            )
    expected_resolution = input_resolution_contract(target, request.config.inputs)
    if retained_resolution != expected_resolution:
        raise InputError("Frozen source snapshot cannot apply changed source roots or exclusions")
    if isinstance(target, Source):
        source = snapshot.module_map.get(target.module)
        if (
            metadata.get("kind") != "source"
            or metadata.get("module") != target.module
            or source is None
            or source.source != target.code
            or source.filename != target.filename
        ):
            raise InputError("Memory source does not match the frozen source snapshot")
    elif isinstance(target, File):
        path = str(Path(target.path).expanduser().resolve())
        if metadata.get("kind") != "file" or metadata.get("path") != path:
            raise InputError("File target does not match the frozen source snapshot")
        if target.module and metadata.get("module") != target.module:
            raise InputError("File module does not match the frozen source snapshot")
    else:
        root = str(Path(target.root).expanduser().resolve())
        if metadata.get("kind") != "project" or metadata.get("root") != root:
            raise InputError("Project target does not match the frozen source snapshot")
    declared = (
        target.entries
        if isinstance(target, Project) and target.entries
        else request.config.inputs.entries
    )
    if declared:
        expected: list[tuple[str, str | None]] = []
        for entry in declared:
            if resource_ledger is not None:
                resource_ledger.charge_work(1, "snapshot_entry_validation")
            if ":" not in entry:
                raise InputError("Entry must be module:qualname")
            module, qualname = entry.split(":", 1)
            expected.append((module, qualname or None))
    elif isinstance(target, Project):
        expected = [(module.module, None) for module in snapshot.modules]
    else:
        root_module = metadata.get("module")
        if not isinstance(root_module, str):
            raise InputError("Frozen source snapshot has no root module identity")
        expected = [(root_module, None)]
    if resource_ledger is not None:
        resource_ledger.charge_work(len(snapshot.scenarios), "snapshot_scenario_validation")
    actual = [(scenario.root_module, scenario.entry_qualname) for scenario in snapshot.scenarios]
    if actual != expected:
        raise InputError("Requested entries do not match the frozen source snapshot")


def prepare_request(
    request: ResolvedRequest,
    *,
    source_override: SourceSnapshot | None = None,
    resource_ledger: RunLedger | None = None,
) -> PreparedRun:
    started_at = time.monotonic()
    config = request.config
    if resource_ledger is not None:
        resource_ledger.charge_work(1, "source_inventory")
    if source_override is None:
        snapshot = (
            snapshot_target(request.target, config)
            if resource_ledger is None
            else snapshot_target(request.target, config, resource_ledger=resource_ledger)
        )
    else:
        if resource_ledger is not None:
            for source_module in source_override.modules:
                visit_json_text(
                    source_module.source,
                    lambda amount: resource_ledger.charge_work(amount, "source_snapshot_reuse"),
                )
        snapshot = source_override.for_rerun()
    for binding in config.inputs.bindings:
        module, _, symbol = binding.rpartition(".")
        if module not in snapshot.module_map or not symbol.isidentifier():
            raise InputError(
                f"Abstract input {binding!r} does not identify an inventoried module and global"
            )
    try:
        if resource_ledger is None:
            program = parse_sources(snapshot.module_map, parsed_trees=snapshot.parsed_trees)
        else:
            with resource_ledger.structural_meter("frontend_index") as meter:
                program = parse_sources(
                    snapshot.module_map, parsed_trees=snapshot.parsed_trees, visit=meter.visit
                )
            resource_ledger.checkpoint(force=True)
    except SyntaxError as error:
        raise InputError(str(error)) from error
    plan = None
    if (
        config.regions
        or request.output.regions
        or any(query.kind == "plan" for query in request.queries)
    ):
        from pythonstan.analysis.composition.plan import build_plan

        if config.engine in {"native", "native_then_ai"} and config.regions:
            raise ConfigurationError("Region routing requires ai_primary or hybrid")
        if resource_ledger is not None:
            # The finite planner dispatch is priced from its already-owned
            # program input before constructing policy and decision records.
            resource_ledger.charge_work(len(program.nodes), "region_plan_nodes")
        plan = build_plan(snapshot, config, resource_ledger=resource_ledger)
        if resource_ledger is not None:
            resource_ledger.checkpoint(force=True)
    if resource_ledger is not None:
        resource_ledger.charge_work(len(request.queries), "scenario_priority_configuration")
        resource_ledger.charge_work(len(snapshot.scenarios), "scenario_priority_sort")
    priority = {
        query.module: index
        for index, query in enumerate(request.queries)
        if isinstance(query, ValueQuery)
    }

    def scenario_priority(entry: EntryScenario) -> int:
        return priority.get(entry.root_module, len(priority))

    scenarios = tuple(sorted(snapshot.scenarios, key=scenario_priority))
    execution_snapshot = replace(snapshot, scenarios=scenarios)
    preparation_seconds = time.monotonic() - started_at
    return PreparedRun(snapshot, execution_snapshot, program, plan, preparation_seconds)
