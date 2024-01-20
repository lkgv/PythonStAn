from typing import Optional, Callable, Tuple

from pythonstan.graph.icfg.icfg import InterControlFlowGraph
from pythonstan.graph.cfg import BaseBlock
from pythonstan.analysis.points_to.ci.lattice.context import Context
from pythonstan.analysis.points_to.ci.lattice.analysis_lattice_element import AnalysisLatticeElement
from pythonstan.ir import IRModule
from .lattice.state import State
from .analysis import CIPTSAnalysis
from .work_list import WorkList
from .solver_interface import SolverInterface


class Solver:
    current_state: State
    current_node: Optional[BaseBlock]
    global_entry_block: BaseBlock
    analysis: CIPTSAnalysis
    work_list: WorkList[Tuple[BaseBlock, Context]]
    graph: InterControlFlowGraph
    analysis_lattice_element: AnalysisLatticeElement
    c : SolverInterface
    module: IRModule

    def __init__(self, analysis: CIPTSAnalysis):
        self.c = SolverInterface(self)
        self.analysis = analysis

    def init(self, graph: InterControlFlowGraph, entry_module: IRModule):
        self.graph = graph
        self.analysis_lattice_element = self.analysis.make_analysis_lattice(graph)
        self.current_node = self.global_entry_block = graph.get_entry()
        self.analysis.init_cs(graph)
        self.module = entry_module
        self.work_list = WorkList()

    def solve(self):
        while not self.work_list.empty():
            blk, ctx = self.work_list.pop()
            state = self.analysis_lattice_element.get_state(ctx, blk)
            self.current_state = state.clone()
            if self.global_entry_block == blk:
                self.current_state.localize(None)
            for stmt in blk.get_stmts():
                self.analysis.get_node_transfer_functions().transfer(stmt)
            s = self.current_state
            for e in self.graph.out_edges_of(blk):
                self.current_state = s.clone()
                new_ctx = self.analysis.get_edge_transfer_functions().transfer(e)
                if new_ctx is not None:
                    self.c.propagate_to_base_block(self.current_state, e.get_tgt(), new_ctx)

    def get_c(self):
        return self.c
