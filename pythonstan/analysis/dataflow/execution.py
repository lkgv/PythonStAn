"""Fact-erased execution and the explicit custom solver compatibility boundary.

The registry chooses an analysis implementation, not a type witness for its
fact domain. Published mappings therefore expose opaque facts and retain the
solver's exact mapping and fact objects.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeGuard

from pythonstan.analysis.analysis import Analysis, AnalysisConfig
from pythonstan.compat.host_iteration import iter_objects
from pythonstan.graph.cfg import BaseBlock, ControlFlowGraph
from pythonstan.ir import IRScope


@dataclass(frozen=True)
class ScopeFactViews:
    incoming: Mapping[BaseBlock, object]
    outgoing: Mapping[BaseBlock, object]


def _is_fact_map(value: object) -> TypeGuard[Mapping[BaseBlock, object]]:
    if not isinstance(value, Mapping):
        return False
    entries: Mapping[object, object] = value
    return all(isinstance(node, BaseBlock) for node in entries)


def solve_with_custom_solver(solver: object, analysis: DataflowExecution) -> ScopeFactViews:
    """Call one actual plugin service without claiming its erased fact type."""
    service: object = getattr(solver, "solve", None)
    if not callable(service):
        raise TypeError("custom dataflow solver must expose a callable solve service")
    result: object = service(analysis)
    try:
        iterator = iter_objects(result)
    except TypeError as exc:
        raise TypeError("custom dataflow solver must return a pair of fact mappings") from exc
    try:
        incoming, outgoing = next(iterator), next(iterator)
    except StopIteration as exc:
        raise ValueError("custom dataflow solver must return exactly two fact mappings") from exc
    try:
        next(iterator)
    except StopIteration:
        pass
    else:
        raise ValueError("custom dataflow solver must return exactly two fact mappings")
    if not _is_fact_map(incoming) or not _is_fact_map(outgoing):
        raise TypeError("custom dataflow solver facts must map BaseBlock nodes to fact objects")
    return ScopeFactViews(incoming, outgoing)


class DataflowExecution(Analysis):
    """An analysis runs with its own fact domain hidden behind this interface."""

    @abstractmethod
    def __init__(self, scope: IRScope, cfg: ControlFlowGraph, config: AnalysisConfig) -> None: ...

    @abstractmethod
    def set_input(self, item: str, value: object) -> None: ...

    @abstractmethod
    def execute_scope(self, solver: object) -> ScopeFactViews: ...
