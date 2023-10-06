from typing import Dict, Any

from .solver import Solver
from .analysis import MiniCGAnalysis

from pythonstan.ir import IRScope, IRModule
from pythonstan.graph.icfg.icfg import InterControlFlowGraph
from pythonstan.analysis import AnalysisDriver, AnalysisConfig


__all__ = ['MiniCGAnalysisDriver']


class MiniCGAnalysisDriver(AnalysisDriver):
    def __init__(self, config: AnalysisConfig):
        self.config = config
        self.solver = Solver()
        self.analysis = MiniCGAnalysis(config)

    def analyze(self, scope: IRModule, prev_results: Dict[str, Any]):
        from pythonstan.world import World
        cfg = World().scope_manager.get_ir(scope, 'cfg')
        icfg = InterControlFlowGraph()
        icfg.add_scope(scope, cfg)
        icfg.add_entry_scope(scope)
        self.analysis.set_solver_interface(self.solver.get_c())
        self.solver.init(self.analysis, icfg, scope)
        self.solver.solve()

        c = self.solver.get_c()
        self.results = {
            'call_graph': c.get_analysis_lattice_element().get_call_graph(),
            'icfg': c.get_graph()}
        print(self.results)


