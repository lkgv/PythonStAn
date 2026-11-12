"""A registry cannot establish Fact, and custom solver facts retain their identity."""

import ast
from collections.abc import Mapping
from typing import get_origin

import pytest

from pythonstan.analysis.analysis import Analysis, AnalysisConfig
from pythonstan.analysis.dataflow.driver import DataflowAnalysisDriver
from pythonstan.analysis.dataflow.execution import DataflowExecution
from pythonstan.analysis.dataflow.liveness import LivenessAnalysis
from pythonstan.analysis.dataflow.solver import Solver, WorklistSolver
from pythonstan.graph.cfg import BaseBlock, ControlFlowGraph
from pythonstan.ir import IRModule
from pythonstan.world.world import World


def test_registry_hooks_preserve_ordinary_registration_and_initialize_generic_subclasses() -> None:
    assert Analysis.get_analysis("LivenessAnalysis") is LivenessAnalysis
    assert Solver.get_solver("WorklistSolver") is WorklistSolver
    assert get_origin(WorklistSolver[int]) is WorklistSolver
    previous = dict(Analysis.analysis_dict)
    try:

        class ParameterizedAnalysis[Element](Analysis):
            def __init__(self, config: AnalysisConfig) -> None:
                super().__init__(config)

        assert Analysis.get_analysis("ParameterizedAnalysis") is ParameterizedAnalysis
        assert get_origin(ParameterizedAnalysis[int]) is ParameterizedAnalysis
    finally:
        Analysis.analysis_dict.clear()
        Analysis.analysis_dict.update(previous)


def _subject(*, retain: bool = True) -> tuple[DataflowAnalysisDriver[object], IRModule, BaseBlock]:
    World.setup()
    World().scope_manager.build()
    scope = IRModule("subject", ast.parse(""), "subject", "subject.py")
    entry = BaseBlock(0, [])
    graph = ControlFlowGraph(entry)
    graph.add_blk(entry)
    graph.super_exit_blk = entry
    World().scope_manager.set_ir(scope, "cfg", graph)
    driver: DataflowAnalysisDriver[object] = DataflowAnalysisDriver(
        AnalysisConfig(
            "live",
            "LivenessAnalysis",
            options={"type": "dataflow analysis", "retain_scope_results": retain},
        )
    )
    return driver, scope, entry


def test_injected_solver_mapping_and_fact_objects_are_published_without_copying() -> None:
    driver, scope, entry = _subject()
    incoming = {entry: {"incoming"}}
    outgoing = {entry: {"observed"}}

    class ExternalSolver:
        @staticmethod
        def solve(
            analysis: DataflowExecution,
        ) -> tuple[Mapping[BaseBlock, object], Mapping[BaseBlock, object]]:
            return incoming, outgoing

    driver.solver = ExternalSolver
    driver.analyze(scope, {})
    assert driver.facts("in") is incoming
    assert driver.facts("out") is outgoing
    published = driver.results
    assert isinstance(published, dict)
    assert published["per_scope"][scope]["out"] is outgoing
    outgoing[entry].add("caller_owned_diagnostic")
    assert driver.facts("out")[entry] is outgoing[entry]
    driver.clear_results()
    assert driver.results == {"per_scope": {}}
    assert published["out"] is outgoing
    assert "caller_owned_diagnostic" in outgoing[entry]


@pytest.mark.parametrize("bad", [(object(), {}), ({"not-a-block": set()}, {}), ({},), ({}, {}, {})])
def test_invalid_external_fact_shapes_fail_before_replacing_previous_publication(
    bad: object,
) -> None:
    driver, scope, entry = _subject(retain=False)
    driver.analyze(scope, {})
    previous = driver.results
    previous_out = driver.facts("out")

    class ExternalSolver:
        @staticmethod
        def solve(analysis: DataflowExecution) -> object:
            return bad

    driver.solver = ExternalSolver
    with pytest.raises((TypeError, ValueError), match="fact"):
        driver.analyze(scope, {})
    assert driver.results is previous
    assert driver.facts("out") is previous_out
    assert set(previous_out) == {entry}
