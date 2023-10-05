import copy
from typing import Dict, Any
from abc import abstractmethod

from pythonstan.ir import IRScope
from .node_transfer import NodeTransfer
from .edge_transfer import EdgeTransfer
from ...analysis import Analysis, AnalysisConfig
from .solver_interface import SolverInterface
from .context_sensitive_strategy import ContextSensitiveStrategy
from .lattice.execution_context import ExecutionContext
from .lattice.analysis_lattice_element import AnalysisLatticeElement
from .lattice.call_graph import CallGraph
from .predefined_functions import PREDEFINED_FUNCTIONS_LIST


__all__ = ['MiniCGAnalysis']


class MiniCGAnalysis(Analysis):
    module: IRScope
    inputs: Dict[str, Any]
    results: Any
    solver_interface: SolverInterface
    context_sensitive_strategy: ContextSensitiveStrategy
    node_transfer: NodeTransfer
    edge_transfer: EdgeTransfer

    @abstractmethod
    def __init__(self, config: AnalysisConfig):
        super().__init__(config)
        self.inputs = {}

    def set_solver_interface(self, c: SolverInterface):
        self.solver_interface = c

    def set_input(self, key, value):
        self.inputs[key] = value

    def get_input(self, key):
        return self.inputs[key]

    def get_solver_interface(self):
        return self.solver_interface

    def make_analysis_lattice(self, cg: CallGraph) -> AnalysisLatticeElement:
        return AnalysisLatticeElement({}, cg)

    def get_node_transfer_functions(self) -> NodeTransfer:
        assert hasattr(self, "solver_interface"), "Solver Interface has not been assigned"
        return NodeTransfer(self.solver_interface)

    def get_edge_transfer_functions(self) -> EdgeTransfer:
        return EdgeTransfer()

    def get_context_sensitive_strategy(self) -> ContextSensitiveStrategy:
        return self.context_sensitive_strategy

    def new_execution_context(self) -> ExecutionContext:
        ec = ExecutionContext()
        ec.new_scope_chain()
        ec.scope_chain.add_scope()
        for name, f_obj in PREDEFINED_FUNCTIONS_LIST:
            ec.set_var(name, f_obj)
        return ec
