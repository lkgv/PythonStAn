"""Read statement uses from block-entry facts and the preceding write prefix."""

from typing import override

from pythonstan.analysis.analysis import AnalysisConfig, AnalysisDriver
from pythonstan.compat.host_iteration import iter_objects
from pythonstan.graph.cfg import ControlFlowGraph
from pythonstan.ir import IRScope, IRStatement
from pythonstan.world.world import World

from .dataflow.driver import DataflowAnalysisDriver
from .dataflow.reaching_definition import advance_definitions

type DefinitionUses = dict[tuple[IRStatement, str], set[IRStatement]]
type UsedBy = dict[IRStatement, set[IRStatement]]
type DefUseResult = tuple[DefinitionUses, UsedBy]


def _reaching_definitions(value: object) -> set[IRStatement]:
    """Decode this consumer's fact domain without asserting an erased Fact."""
    iterator = iter_objects(value)
    result: set[IRStatement] = set()
    while True:
        try:
            definition = next(iterator)
        except StopIteration:
            return result
        if not isinstance(definition, IRStatement):
            raise TypeError("reaching-definition facts must contain IRStatement values")
        result.add(definition)


class DefUseAnalysis(AnalysisDriver):
    compute_stores: bool
    compute_loads: bool
    rd_analysis: DataflowAnalysisDriver[object]
    world: World

    def __init__(self, config: AnalysisConfig) -> None:
        stores = config.options.get("compute-stores", True)
        loads = config.options.get("compute-loads", True)
        if type(stores) is not bool or type(loads) is not bool:
            raise ValueError("compute-stores and compute-loads must be Boolean")
        self.compute_stores = stores
        self.compute_loads = loads
        rd_config = AnalysisConfig(
            name="reaching-definition",
            id="ReachingDefinitionAnalysis",
            options={"type": "dataflow analysis"},
        )
        self.rd_analysis = DataflowAnalysisDriver(rd_config)
        self.world = World()
        super().__init__(config)

    @override
    def analyze(
        self, scope: IRScope, prev_results: dict[str, object] | None = None
    ) -> DefUseResult:
        self.rd_analysis.analyze(scope, {} if prev_results is None else prev_results)
        cfg: object = self.world.scope_manager.get_ir(scope, "cfg")
        if not isinstance(cfg, ControlFlowGraph):
            raise TypeError("def-use analysis requires a ControlFlowGraph representation")
        incoming = self.rd_analysis.facts("in")
        stores: DefinitionUses = {}
        loads: UsedBy = {}
        for block in cfg.blks:
            reaching = _reaching_definitions(incoming[block])
            for statement in block.stmts:
                for name in statement.get_nostores():
                    definitions = {
                        definition for definition in reaching if name in definition.get_stores()
                    }
                    if self.compute_stores and definitions:
                        stores[(statement, name)] = definitions
                    if self.compute_loads:
                        for definition in definitions:
                            loads.setdefault(definition, set()).add(statement)
                reaching = advance_definitions(reaching, statement)
        result = stores, loads
        self.results = result
        return result
