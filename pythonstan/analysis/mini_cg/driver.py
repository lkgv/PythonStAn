from typing import TypeVar, Generic, Dict, Type

from pythonstan.ir import IRScope
from ..analysis import Analysis, AnalysisDriver, AnalysisConfig
from .analysis import MiniCGAnalysis
from .solver import Solver

Fact = TypeVar('Fact')

class MiniCGAnalysisDriver(Generic[Fact], AnalysisDriver):
    analysis: Type[DataflowAnalysis]
    solver: Solver
    results: Dict

    def __init__(self, config: AnalysisConfig):
        super().__init__(config)
        self.solver = Solver()
        if 'solver' in config.options:
            self.solver = Solver.get_solver(config.options['solver'])[Fact]
        else:
            self.solver = WorklistSolver[Fact]
        self.results = {}

    def analyze(self):
        from pythonstan.world import World
        entry_ir = World().get_entry_module()
        entry_cfg = World().scope_manager.get_ir(entry_ir, "cfg")


        ir_name = self.config.options.get("ir", "cfg")
        cfg = World().scope_manager.get_ir(scope, ir_name)
        analyzer = self.analysis(scope, cfg, self.config)
        for prev in self.config.prev_analysis:
            if prev in prev_results:
                analyzer.set_input(prev, prev_results[prev][scope])
        facts_in, facts_out = self.solver.solve(analyzer)
        self.results = {'in': facts_in,
                        'out': facts_out}
