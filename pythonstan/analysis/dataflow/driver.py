from typing import TypeVar, Generic, Dict

from pythonstan.graph.cfg.models import CFGScope
from ..analysis import Analysis, AnalysisDriver, AnalysisConfig
from .analysis import DataflowAnalysis
from .solver import Solver, WorklistSolver

Fact = TypeVar('Fact')


class DataflowAnalysisDriver(Generic[Fact], AnalysisDriver):
    analysis: DataflowAnalysis
    solver: Solver
    results: Dict

    def __init__(self, config: AnalysisConfig):
        super().__init__(config)
        self.analysis = DataflowAnalysis.get_analysis(config.id)
        if 'solver' in config.options:
            self.solver = Solver.get_solver(config.options['solver'])[Fact]
        else:
            self.solver = WorklistSolver[Fact]
        self.results = {}

    def analyze(self, scope: CFGScope):
        analyzer = self.analysis(scope, self.config)
        facts_in, facts_out = self.solver.solve(analyzer)
        self.results = {'in': facts_in,
                        'out': facts_out}
