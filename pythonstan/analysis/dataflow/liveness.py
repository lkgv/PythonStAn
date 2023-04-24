from typing import Set

from pythonstan.graph.cfg.models import BaseBlock
from utils.var_collector import VarCollector
from .analysis import DataflowAnalysis


class LivenessAnalysis(DataflowAnalysis[Set[str]]):
    def __init__(self, scope, config):
        self.is_forward = False
        super().__init__(scope, config)
    
    def new_boundary_fact(self) -> Set[str]:
        return self.new_init_fact()
    
    def new_init_fact(self) -> Set[str]:
        return {*()}
    
    def meet(self, fact_1: Set[str], fact_2: Set[str]) -> Set[str]:
        return fact_1.union(fact_2)

    def transfer_edge(self, edge, node_fact):
        pass

    def need_transfer_edge(self, edge):
        super().need_transfer_edge(edge)
    
    def transfer_node(self, node: BaseBlock, fact: Set[str]) -> Set[str]:
        s_colle = VarCollector()
        l_colle = VarCollector()
        fact_out = fact.copy()
        for stmt in node.stmts:
            s_colle.reset("store")
            s_colle.visit(stmt)
            fact_out.difference_update(s_colle.get_vars())
            l_colle.reset("no_store")
            l_colle.visit(stmt)
            fact_out.update(l_colle.get_vars())
        return fact_out
