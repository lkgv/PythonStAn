from typing import Optional, Callable, Tuple

from pythonstan.graph.icfg.icfg import InterControlFlowGraph
from pythonstan.graph.cfg import BaseBlock
from pythonstan.ir import IRModule
from .lattice.state import State
from .analysis import MiniCGAnalysis
from .work_list import WorkList
from .solver_interface import SolverInterface
from .lattice.context import Context
from .lattice.analysis_lattice_element import AnalysisLatticeElement
from .lattice.call_graph import CallGraph
from .context_sensitive_strategy import ContextSensitiveStrategy


__all__ = ["Solver"]


class Solver:
    current_state: State
    current_node: Optional[BaseBlock]
    global_entry_block: BaseBlock
    analysis: MiniCGAnalysis
    work_list: WorkList[Tuple[BaseBlock, Context]]
    graph: InterControlFlowGraph
    analysis_lattice_element: AnalysisLatticeElement
    c: SolverInterface
    module: IRModule

    def __init__(self):
        self.c = SolverInterface(self)

    def init(self, analysis: MiniCGAnalysis, graph: InterControlFlowGraph, entry_module: IRModule):
        self.analysis = analysis
        self.graph = graph
        cg = CallGraph(self.c)
        self.analysis_lattice_element = self.analysis.make_analysis_lattice(cg)
        self.current_node = self.global_entry_block = graph.get_entry()
        self.module = entry_module
        self.work_list: WorkList[Tuple[BaseBlock, Context]] = WorkList()
        entry = graph.get_entry()
        init_ctx = ContextSensitiveStrategy.make_initial_context()
        self.work_list.add((entry, init_ctx))
        init_s = State.gen_init_state(self.c, entry)
        self.c.propagate_to_base_block(init_s, entry, init_ctx)
        self.analysis_lattice_element.get_call_graph().register_scope_entry((entry, init_ctx))

    def solve(self):
        while not self.work_list.empty():
            blk, ctx = self.work_list.pop()
            states = self.analysis_lattice_element.get_state(ctx, blk)
            assert len(states) > 0, "No state get here"
            state = next(iter(states))
            self.current_state = state.clone()
            # if self.global_entry_block == blk:
            #     self.current_state.localize(None)
            for stmt in blk.get_stmts():
                self.analysis.get_node_transfer_functions().visit(stmt)
            s = self.current_state
            print(str(self.graph.super_entry_blk), str(self.graph.super_exit_blk))
            for e in self.graph.out_edges_of(blk):
                self.current_state = s.clone()
                new_ctx = self.analysis.get_edge_transfer_functions().visit(e)
                if new_ctx is not None:
                    self.c.propagate_to_base_block(self.current_state, e.get_tgt(), new_ctx)

    def get_c(self):
        return self.c
