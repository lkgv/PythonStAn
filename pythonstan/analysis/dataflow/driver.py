"""Execute a dynamically registered analysis without inventing its fact type."""

from collections.abc import Mapping
from typing import Literal, NotRequired, Required, TypedDict, override

from pythonstan.graph.cfg import BaseBlock, ControlFlowGraph
from pythonstan.ir import IRScope

from ..analysis import AnalysisConfig, AnalysisDriver, get_scope_result
from .analysis import DataflowAnalysis
from .execution import DataflowExecution
from .solver import Solver, WorklistSolver

ScopeResults = TypedDict(
    "ScopeResults", {"in": Mapping[BaseBlock, object], "out": Mapping[BaseBlock, object]}
)
DataflowResults = TypedDict(
    "DataflowResults",
    {
        "per_scope": Required[dict[IRScope, ScopeResults]],
        "in": NotRequired[Mapping[BaseBlock, object]],
        "out": NotRequired[Mapping[BaseBlock, object]],
    },
)


class DataflowAnalysisDriver[Fact](AnalysisDriver):
    """The legacy generic subscription is compatibility syntax, not a fact witness."""

    analysis: type[DataflowExecution]
    solver: object
    _published: DataflowResults
    _per_scope: dict[IRScope, ScopeResults]

    def __init__(self, config: AnalysisConfig) -> None:
        super().__init__(config)
        retain = config.options.get("retain_scope_results", True)
        if type(retain) is not bool:
            raise ValueError("retain_scope_results must be Boolean")
        self.retain_scope_results: bool = retain
        analysis = DataflowAnalysis.get_analysis(config.id)
        if not issubclass(analysis, DataflowExecution):
            raise TypeError("dataflow analysis must implement DataflowExecution")
        self.analysis = analysis
        if "solver" in config.options:
            solver_name = config.options["solver"]
            if not isinstance(solver_name, str):
                raise ValueError("dataflow solver must name a registered solver")
            self.solver = Solver.get_solver(solver_name)
        else:
            self.solver = WorklistSolver
        self.clear_results()

    def clear_results(self) -> None:
        """Release owned containers while observers may retain their exact old maps."""
        self._per_scope = {}
        self._published = {"per_scope": self._per_scope}
        self.results = self._published

    def facts(self, direction: Literal["in", "out"]) -> Mapping[BaseBlock, object]:
        """Read the latest map without copying it or asserting a particular Fact."""
        return self._published[direction]

    @override
    def analyze(self, scope: IRScope, prev_results: object) -> None:
        from pythonstan.world.world import World

        ir_name = self.config.options.get("ir", "cfg")
        if not isinstance(ir_name, str):
            raise ValueError("dataflow ir must name a representation")
        cfg = World().scope_manager.get_ir(scope, ir_name)
        if not isinstance(cfg, ControlFlowGraph):
            raise TypeError("dataflow analysis requires a ControlFlowGraph representation")
        analyzer = self.analysis(scope, cfg, self.config)
        for prev in self.config.prev_analysis:
            if not isinstance(prev_results, Mapping):
                raise TypeError("dataflow dependencies must be a mapping")
            dependencies: Mapping[object, object] = prev_results
            if prev in dependencies:
                analyzer.set_input(prev, get_scope_result(dependencies[prev], scope, prev))
        facts = analyzer.execute_scope(self.solver)
        scope_result: ScopeResults = {"in": facts.incoming, "out": facts.outgoing}
        if self.retain_scope_results:
            self._per_scope[scope] = scope_result
        # These last-run aliases and per-scope history point to the exact same
        # maps. Compact diagnostic views and ordinary mutable dicts stay intact.
        self._published["in"] = facts.incoming
        self._published["out"] = facts.outgoing
