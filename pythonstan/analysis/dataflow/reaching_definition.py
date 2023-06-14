from typing import Set, Dict
from ast import stmt

from pythonstan.graph.cfg import BaseBlock
from pythonstan.ir import IRScope, IRStatement
from pythonstan.utils.var_collector import VarCollector
from .analysis import DataflowAnalysis


class ReachingDefinitionAnalysis(DataflowAnalysis[Set[IRStatement]]):
    defs: Dict[str, Set[IRStatement]]

    def __init__(self, scope, cfg, config):
        self.is_forward = True
        self.inter_procedure = False
        self.defs = self.compute_defs(scope)
        super().__init__(scope, cfg, config)
    
    def new_boundary_fact(self) -> Set[IRStatement]:
        return self.new_init_fact()
    
    def new_init_fact(self) -> Set[IRStatement]:
        return {*()}
    
    def meet(self, fact_1: Set[IRStatement], fact_2: Set[IRStatement]) -> Set[IRStatement]:
        return fact_1.union(fact_2)

    def need_transfer_edge(self, edge):
        super().need_transfer_edge(edge)
    
    def compute_defs(self, scope: IRScope):
        defs = {}
        for cur_stmt in scope.cfg.stmts:
            for var_id in cur_stmt.get_stores():
                if var_id in defs:
                    defs[var_id].add(cur_stmt)
                else:
                    defs[var_id] = {cur_stmt}
        return defs

    def transfer_node(self, node: BaseBlock, fact: Set[IRStatement]) -> Set[IRStatement]:
        fact_out = fact.copy()
        for cur_stmt in node.stmts:
            for var_id in cur_stmt.get_stores():
                if var_id in self.defs:
                    fact_out.difference_update(self.defs[var_id])
                    fact_out.add(cur_stmt)
        return fact_out
