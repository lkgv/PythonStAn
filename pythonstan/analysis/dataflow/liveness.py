from typing import override

from pythonstan.analysis.analysis import AnalysisConfig
from pythonstan.graph.cfg import BaseBlock, CFGEdge, ControlFlowGraph
from pythonstan.ir import IRScope

from .analysis import DataflowAnalysis


class LivenessAnalysis(DataflowAnalysis[set[str]]):
    def __init__(self, scope: IRScope, cfg: ControlFlowGraph, config: AnalysisConfig) -> None:
        self.is_forward = False
        self.inter_procedure = False
        super().__init__(scope, cfg, config)

    @override
    def new_boundary_fact(self) -> set[str]:
        return self.new_init_fact()

    @override
    def new_init_fact(self) -> set[str]:
        return {*()}

    @override
    def meet(self, fact_1: set[str], fact_2: set[str]) -> set[str]:
        return fact_1.union(fact_2)

    @override
    def need_transfer_edge(self, edge: CFGEdge) -> bool:
        return False

    @override
    def transfer_node(self, node: BaseBlock, fact: set[str]) -> set[str]:
        fact_out = fact.copy()
        for stmt in node.stmts[::-1]:
            fact_out.difference_update(stmt.get_stores())
            fact_out.update(stmt.get_nostores())
            if isinstance(stmt, IRScope):
                fact_out.update(stmt.get_cell_vars() | stmt.get_nonlocal_vars())
        return fact_out
