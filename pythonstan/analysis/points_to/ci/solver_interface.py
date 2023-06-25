from typing import Optional, Callable, Tuple

from pythonstan.graph.icfg.icfg import InterControlFlowGraph
from pythonstan.graph.cfg import BaseBlock
from .lattice.state import State
from .analysis import CIPTSAnalysis
from pythonstan.analysis.points_to.ci.lattice.context import Context
from pythonstan.analysis.points_to.ci.lattice.analysis_lattice_element import AnalysisLatticeElement
from .work_list import WorkList


class SolverInterface:
    current_state: State
    current_node: Optional[BaseBlock]
    analysis: CIPTSAnalysis
    work_list: WorkList[Tuple[BaseBlock, Context]]
    graph: InterControlFlowGraph
    analysis_lattice_element: AnalysisLatticeElement

    def __init__(self, analysis: CIPTSAnalysis):
        self.analysis = analysis

    def init(self, graph: InterControlFlowGraph):
        self.graph = graph
        self.analysis_lattice_element = self.analysis.make_analysis_lattice(graph)
        self.current_node = self.global_entry_block = graph.get_entry()
        self.analysis.init_cs(graph)
        self.work_list = WorkList()

    def set_state(self, state: State):
        self.current_state = state

    def get_state(self):
        return self.current_state

    def get_node(self):
        assert self.current_node is not None, "Unexpected call to get_node"
        return self.current_node

    def get_graph(self):
        return self.graph

    def with_state(self, state: State, f: Callable):
        old = self.current_state
        self.current_state = state
        ret = f()
        self.current_state = old
        return ret

    def with_state_and_node(self, state: State, node: BaseBlock, f: Callable):
        old_node, old_state = self.current_node, self.current_state
        self.current_node, self.current_state = node, state
        ret = f()
        self.current_node, self.current_state = old_node, old_state
        return ret

    def set_analysis(self, analysis: CIPTSAnalysis):
        self.analysis = analysis

    def get_analysis(self):
        return self.analysis

    def propagate_to_base_block(self, state: State, blk: BaseBlock, ctx: Context):
        self.propagate_update_work_list(state, blk, ctx, False)

    def propagate_update_work_list(self, state: State, blk: BaseBlock, ctx: Context, localize: bool):
        changed = self.propagate(state, (blk, ctx), localize)
        if changed:
            self.add_to_work_list(blk, ctx)

    def propagate(self, state, cblk_to, localize):
        res = self.analysis_lattice_element.propagate(state, cblk_to, localize)
        return res is not None

    def propagate_to_scope_entry(self, stmt, caller_ctx, edge, edge_ctx, callee_entry):
        ...

    def return_from_function_exit(self, return_state, stmt, caller_ctx, callee_entry, edge_ctx):
        ...

    def add_to_work_list(self, blk: BaseBlock, ctx: Context):
        self.work_list.add((blk, ctx))

    def get_analysis_lattice_element(self) -> AnalysisLatticeElement:
        return self.analysis_lattice_element
