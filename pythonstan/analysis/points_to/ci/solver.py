from typing import Optional, Callable, Tuple

from pythonstan.ir import IRScope
from pythonstan.graph.icfg.icfg import InterControlFlowGraph
from pythonstan.graph.cfg import BaseBlock
from .lattice.state import State
from .analysis import CIPTSAnalysis
from pythonstan.analysis.points_to.ci.lattice.context import Context
from pythonstan.analysis.points_to.ci.lattice.analysis_lattice_element import AnalysisLatticeElement
from .work_list import WorkList


class SolverInterface:
    solver: 'Solver'

    def __init__(self, solver: 'Solver'):
        self.solver = solver

    def get_graph(self):
        return self.solver.graph

    def get_state(self):
        return self.solver.current_state

    def get_node(self):
        assert self.solver.current_node is not None, "Unexpected call to get_node"
        return self.solver.current_node

    def with_state(self, state: State, f: Callable):
        old = self.solver.current_state
        self.solver.current_state = state
        ret = f()
        self.solver.current_state = old
        return ret

    def with_state_and_node(self, state: State, node: BaseBlock, f: Callable):
        old_node, old_state = self.solver.current_node, self.solver.current_state
        self.solver.current_node, self.solver.current_state = node, state
        ret = f()
        self.solver.current_node, self.solver.current_state = old_node, old_state
        return ret

    def set_analysis(self, analysis: CIPTSAnalysis):
        self.solver.analysis = analysis

    def get_analysis(self):
        return self.solver.analysis

    def propagate_to_base_block(self, state: State, blk: BaseBlock, ctx: Context):
        self.propagate_update_work_list(state, blk, ctx, False)

    def propagate_update_work_list(self, state: State, blk: BaseBlock, ctx: Context, localize: bool):
        changed = self.propagate(state, (blk, ctx), localize)
        if changed:
            self.add_to_work_list(blk, ctx)

    def propagate(self, state, cblk_to, localize):
        res = self.solver.analysis_lattice_element.propagate(state, cblk_to, localize)
        return res is not None

    def add_to_work_list(self, blk: BaseBlock, ctx: Context):
        self.solver.work_list.add((blk, ctx))

    def get_scope(self) -> IRScope:
        return self.solver.graph.blk2scope[self.solver.current_node]

    def get_analysis_lattice_element(self) -> AnalysisLatticeElement:
        return self.solver.analysis_lattice_element

    def get_world(self):
        from pythonstan.world import World
        return World()


class Solver:
    current_state: State
    current_node: Optional[BaseBlock]
    global_entry_block: BaseBlock
    analysis: CIPTSAnalysis
    work_list: WorkList[Tuple[BaseBlock, Context]]
    graph: InterControlFlowGraph
    analysis_lattice_element: AnalysisLatticeElement
    c : SolverInterface

    def __init__(self, analysis: CIPTSAnalysis):
        self.c = SolverInterface(self)
        self.analysis = analysis

    def init(self, graph: InterControlFlowGraph):
        self.graph = graph
        self.analysis_lattice_element = self.analysis.make_analysis_lattice(graph)
        self.current_node = self.global_entry_block = graph.get_entry()
        self.analysis.init_cs(graph)
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
