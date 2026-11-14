"""Finite checked-summary host capability without optional provider imports."""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NewType, Protocol

from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.identity import IdentityEpoch, ObjectId, ProgramPointId
from pythonstan.analysis.observations.driver_bridge import SummaryProcedureExit
from pythonstan.analysis.state.store import FunctionInfo

SummaryHostId = NewType("SummaryHostId", object)
type SummaryChargeCategory = Literal[
    "llm_proposer_checker_application_work", "llm_state_invalidation_work"
]


@dataclass(frozen=True)
class SummaryApplicationContext:
    """The actual finite host capability consumed by the checked bridge."""

    host_token: SummaryHostId
    functions: Mapping[ObjectId, FunctionInfo]
    modules: Mapping[str, SourceModule]
    trees: Mapping[str, ast.Module]
    policy_digest: str
    model_digest: str
    scenario: str
    current_epoch: Callable[[], IdentityEpoch]
    point: Callable[[ast.AST | None], ProgramPointId]
    remaining_work: Callable[[], int]
    charge: Callable[[int, SummaryChargeCategory], None]
    record_call: Callable[[str, str, ProgramPointId], None]
    record_read: Callable[[ObjectId, str, ProgramPointId], None]
    record_write: Callable[[ObjectId, str, ProgramPointId], None]
    record_exception: Callable[[str], None]
    record_binding_read: Callable[[str, str, ProgramPointId], None] | None = None
    record_procedure_exits: Callable[[tuple[SummaryProcedureExit, ...]], None] | None = None
    context_depth: int = 0

    def __post_init__(self) -> None:
        # Read-only live views avoid copying the host registry on every call.
        object.__setattr__(
            self, "functions", MappingProxyType[ObjectId, FunctionInfo](self.functions)
        )
        object.__setattr__(self, "modules", MappingProxyType[str, SourceModule](self.modules))
        object.__setattr__(self, "trees", MappingProxyType[str, ast.Module](self.trees))


class SummaryChargePort(Protocol):
    def charge(self, amount: int, category: SummaryChargeCategory) -> None: ...
