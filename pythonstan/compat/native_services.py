"""Explicit runtime service injection for the retained legacy Pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.identity import SourceModule
from pythonstan.analysis.contracts.json_data import (
    JsonValue,
    require_array,
    require_object,
    require_string,
)

if TYPE_CHECKING:
    from pythonstan.world.analysis_manager import AnalysisManager


@dataclass(frozen=True, slots=True)
class CapturedMethodInventory:
    """Parent-derived named functions bound to the exact captured source identities."""

    sources: tuple[tuple[str, str, tuple[str, ...]], ...]

    @classmethod
    def from_wire(
        cls, value: JsonValue, modules: Mapping[str, SourceModule]
    ) -> CapturedMethodInventory:
        sources: dict[str, tuple[str, tuple[str, ...]]] = {}
        for item in require_array(value):
            row = require_object(item)
            if row.keys() != {"module", "digest", "qualnames"}:
                raise ValueError("Captured method inventory fields must be module/digest/qualnames")
            name, digest = require_string(row["module"]), require_string(row["digest"])
            if name in sources or name not in modules or modules[name].digest != digest:
                raise ValueError("Captured method inventory disagrees with source identity")
            names = tuple(require_string(item) for item in require_array(row["qualnames"]))
            if len(set(names)) != len(names) or any(
                not qualname.startswith(name + ".") or qualname.endswith(".") for qualname in names
            ):
                raise ValueError(
                    "Captured method inventory requires exact named function identities"
                )
            sources[name] = digest, names
        if sources.keys() != modules.keys():
            raise ValueError("Captured method inventory disagrees with source identity")
        return cls(tuple((name, *sources[name]) for name in sorted(sources)))

    @property
    def targets(self) -> frozenset[tuple[str, str]]:
        return frozenset(
            (module, qualname) for module, _digest, names in self.sources for qualname in names
        )


def configure_native_services(
    manager: AnalysisManager,
    *,
    max_work: int | None = None,
    source_metadata: dict[str, dict[str, str]] | None = None,
    input_bindings: Mapping[str, JsonValue] | None = None,
    method_inventory: CapturedMethodInventory | None = None,
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
        if method_inventory is not None:
            if solver._work_iteration_started or solver.work_ledger.used:
                raise ValueError("Captured method inventory must be fixed before execution")
            analyzer.context_selector.validate_method_targets(method_inventory.targets)
            analyzer._captured_method_targets = method_inventory.targets
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
