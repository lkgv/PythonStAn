from abc import abstractmethod
from typing import override

from pythonstan.graph.cfg import BaseBlock, CFGEdge, ControlFlowGraph
from pythonstan.ir import IRScope

from ..analysis import Analysis, AnalysisConfig
from .execution import DataflowExecution, ScopeFactViews, solve_with_custom_solver


class DataflowAnalysis[Fact](DataflowExecution):
    is_forward: bool
    scope: IRScope
    inputs: dict[str, object]
    cfg: ControlFlowGraph
    inter_procedure: bool

    @abstractmethod
    def __init__(self, scope: IRScope, cfg: ControlFlowGraph, config: AnalysisConfig) -> None:
        super(Analysis, self.__class__).__init__(config)
        self.cfg = cfg
        self.scope = scope
        self.inputs = {}

    @abstractmethod
    def new_boundary_fact(self) -> Fact:
        pass

    @abstractmethod
    def new_init_fact(self) -> Fact:
        pass

    @abstractmethod
    def meet(self, fact_1: Fact, fact_2: Fact) -> Fact:
        pass

    @abstractmethod
    def transfer_node(self, node: BaseBlock, fact: Fact) -> Fact:
        pass

    @abstractmethod
    def need_transfer_edge(self, edge: CFGEdge) -> bool:
        return False

    def transfer_edge(self, edge: CFGEdge, node_fact: Fact) -> Fact:
        return node_fact

    def get_scope(self) -> IRScope:
        return self.scope

    def get_cfg(self) -> ControlFlowGraph:
        return self.cfg

    @override
    def set_input(self, item: str, value: object) -> None:
        self.inputs[item] = value

    def get_input(self, item: str) -> object:
        return self.inputs[item]

    @override
    def execute_scope(self, solver: object) -> ScopeFactViews:
        from .solver import WorklistSolver

        if solver is WorklistSolver:
            incoming, outgoing = WorklistSolver[Fact].solve(self)
            return ScopeFactViews(incoming, outgoing)
        return solve_with_custom_solver(solver, self)
