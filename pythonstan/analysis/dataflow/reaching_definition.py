"""Statement-granularity may-reaching definitions over the actual CFG."""

from typing import override

from pythonstan.analysis.analysis import AnalysisConfig
from pythonstan.graph.cfg import BaseBlock, CFGEdge, ControlFlowGraph
from pythonstan.ir import IRScope, IRStatement

from .analysis import DataflowAnalysis


def advance_definitions(fact: set[IRStatement], statement: IRStatement) -> set[IRStatement]:
    """Apply a write without losing other targets of a compound definition.

    Facts identify defining statements rather than individual variable writes.
    A definition must remain a may-witness until every name it defines has
    been overwritten. Keeping such a statement can add an old witness for an
    already overwritten name; it cannot lose its still-live other targets.
    """
    written = statement.get_stores()
    result: set[IRStatement] = set()
    for definition in fact:
        targets = definition.get_stores()
        if not targets or not targets <= written:
            result.add(definition)
    if written:
        result.add(statement)
    return result


class ReachingDefinitionAnalysis(DataflowAnalysis[set[IRStatement]]):
    defs: dict[str, set[IRStatement]]

    def __init__(self, scope: IRScope, cfg: ControlFlowGraph, config: AnalysisConfig) -> None:
        self.is_forward = True
        self.inter_procedure = False
        self.defs = self.compute_defs(scope, cfg)
        super().__init__(scope, cfg, config)
        self.config = config

    @override
    def new_boundary_fact(self) -> set[IRStatement]:
        return self.new_init_fact()

    @override
    def new_init_fact(self) -> set[IRStatement]:
        return {*()}

    @override
    def meet(self, fact_1: set[IRStatement], fact_2: set[IRStatement]) -> set[IRStatement]:
        return fact_1.union(fact_2)

    @override
    def need_transfer_edge(self, edge: CFGEdge) -> bool:
        return False

    def compute_defs(self, scope: IRScope, cfg: ControlFlowGraph) -> dict[str, set[IRStatement]]:
        definitions: dict[str, set[IRStatement]] = {}
        for statement in cfg.stmts:
            for name in statement.get_stores():
                definitions.setdefault(name, set()).add(statement)
        return definitions

    @override
    def transfer_node(self, node: BaseBlock, fact: set[IRStatement]) -> set[IRStatement]:
        result = fact.copy()
        for statement in node.stmts:
            result = advance_definitions(result, statement)
        return result
