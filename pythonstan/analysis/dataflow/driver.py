from typing import TypeVar, Generic, Dict, Type

from pythonstan.ir import IRScope
from ..analysis import Analysis, AnalysisDriver, AnalysisConfig, get_scope_result
from .analysis import DataflowAnalysis
from .solver import Solver, WorklistSolver

Fact = TypeVar('Fact')


class DataflowAnalysisDriver(Generic[Fact], AnalysisDriver):
    analysis: Type[DataflowAnalysis]
    solver: Solver
    results: Dict

    def __init__(self, config: AnalysisConfig):
        super().__init__(config)
        self.retain_scope_results = config.options.get("retain_scope_results", True)
        if type(self.retain_scope_results) is not bool:
            raise ValueError("retain_scope_results must be Boolean")
        self.analysis = DataflowAnalysis.get_analysis(config.id)
        if 'solver' in config.options:
            self.solver = Solver.get_solver(config.options['solver'])[Fact]
        else:
            self.solver = WorklistSolver[Fact]
        self.results = {"per_scope": {}}

    def analyze(self, scope: IRScope, prev_results):
        from pythonstan.world import World
        ir_name = self.config.options.get("ir", "cfg")
        cfg = World().scope_manager.get_ir(scope, ir_name)

        analyzer = self.analysis(scope, cfg, self.config)
        for prev in self.config.prev_analysis:
            if prev in prev_results:
                analyzer.set_input(prev, get_scope_result(prev_results[prev], scope, prev))

        facts_in, facts_out = self.solver.solve(analyzer)

        scope_result = {"in": facts_in, "out": facts_out}
        if self.retain_scope_results:
            self.results["per_scope"][scope] = scope_result
        # Preserve the established single-scope interface. Multi-scope clients
        # must select per_scope explicitly; these aliases describe the last run.
        self.results.update(scope_result)
