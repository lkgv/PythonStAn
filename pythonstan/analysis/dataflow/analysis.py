from abc import ABC, abstractmethod
from typing import TypeVar, Generic

from pythonstan.graph.cfg.models import CFGScope, Edge, BaseBlock
from ..analysis import Analysis, AnalysisConfig

Fact = TypeVar('Fact')


class DataflowAnalysis(Generic[Fact], Analysis):
    is_forward: bool
    scope: CFGScope
    
    @abstractmethod
    def __init__(self, scope: CFGScope, config: AnalysisConfig):
        super().__init__(config)
        self.scope = scope

    @abstractmethod
    def analysis():
        pass

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
    def need_transfer_edge(self, edge: Edge) -> bool:
        return False

    @abstractmethod
    def transfer_edge(self, edge: Edge, node_fact: Fact) -> Fact:
        pass

    def get_scope(self) -> CFGScope:
        return self.scope
