"""Explicit runtime service injection for the retained legacy Pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.json_data import JsonValue

if TYPE_CHECKING:
    from pythonstan.world.analysis_manager import AnalysisManager


def configure_native_services(
    manager: AnalysisManager,
    *,
    max_work: int | None = None,
    source_metadata: dict[str, dict[str, str]] | None = None,
    input_bindings: Mapping[str, JsonValue] | None = None,
) -> tuple[str, ...]:
    from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis

    configured: list[str] = []
    metadata: dict[str, dict[str, str]] | None = None
    if source_metadata is not None:
        if type(source_metadata) is not dict or any(
            type(name) is not str
            or type(values) is not dict
            or set(values) - {"__file__", "__name__", "__package__"}
            or any(type(value) is not str for value in values.values())
            for name, values in source_metadata.items()
        ):
            raise ValueError("Source metadata must map modules to explicit string metadata")
        metadata = {name: dict(values) for name, values in source_metadata.items()}
    for name, analyzer in manager.analyzers.items():
        if not isinstance(analyzer, PointerAnalysis):
            continue
        solver = analyzer.solver
        if metadata is not None:
            if solver._work_iteration_started or solver.work_ledger.used:
                raise ValueError("Source metadata must be fixed before execution")
            solver.state.source_metadata = {name: dict(values) for name, values in metadata.items()}
        if input_bindings is not None:
            from pythonstan.analysis.pointer.kcfa.input_bindings import validate_input_bindings

            if solver._work_iteration_started or solver.work_ledger.used:
                raise ValueError("Input bindings must be fixed before execution")
            solver.state.input_bindings = validate_input_bindings(input_bindings)
        if max_work is not None:
            solver.set_global_work_limit(max_work)
        if solver.config.enable_ai_intrinsics and solver.intrinsic_provider is None:
            from pythonstan.analysis.composition.native_intrinsics import LegacyIntrinsicProvider

            solver.set_intrinsic_provider(LegacyIntrinsicProvider())
        configured.append(name)
    return tuple(configured)
