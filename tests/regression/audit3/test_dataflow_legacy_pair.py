"""Custom solver pairs retain Python's legacy sequence iteration contract."""

from collections.abc import Mapping

from pythonstan.analysis.dataflow.execution import DataflowExecution
from pythonstan.graph.cfg import BaseBlock

from .test_dataflow_execution_contracts import _subject


def test_injected_solver_accepts_getitem_only_pair_and_preserves_fact_maps() -> None:
    driver, scope, entry = _subject()
    incoming: dict[BaseBlock, object] = {entry: object()}
    outgoing: dict[BaseBlock, object] = {entry: object()}
    reads: list[int] = []

    class LegacyPair:
        def __getitem__(self, index: int) -> Mapping[BaseBlock, object]:
            reads.append(index)
            if index == 0:
                return incoming
            if index == 1:
                return outgoing
            raise IndexError(index)

    class ExternalSolver:
        @staticmethod
        def solve(analysis: DataflowExecution) -> LegacyPair:
            return LegacyPair()

    driver.solver = ExternalSolver
    driver.analyze(scope, {})
    assert reads == [0, 1, 2]
    assert driver.facts("in") is incoming
    assert driver.facts("out") is outgoing
