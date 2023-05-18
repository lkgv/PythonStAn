from typing import Set

from pythonstan.graph.cfg import BaseBlock
from . import DataflowAnalysis


class LivenessAnalysis(DataflowAnalysis[Set[str]]):
    def __init__(self, scope, config):
        self.is_forward = False
        self.inter_procedure = False
        super().__init__(scope, config)
    
    def new_boundary_fact(self) -> Set[str]:
        return self.new_init_fact()
    
    def new_init_fact(self) -> Set[str]:
        return {*()}
    
    def meet(self, fact_1: Set[str], fact_2: Set[str]) -> Set[str]:
        return fact_1.union(fact_2)

    def need_transfer_edge(self, edge):
        return False
    
    def transfer_node(self, node: BaseBlock, fact: Set[str]) -> Set[str]:
        fact_out = fact.copy()
        for stmt in node.stmts[::-1]:
            fact_out.difference_update(stmt.get_stores())
            fact_out.update(stmt.get_nostores())
        return fact_out
