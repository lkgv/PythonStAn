from typing import Set, Dict
from ast import stmt

from pythonstan.graph.cfg import BaseBlock, CFGScope, CFGStmt
from pythonstan.utils.var_collector import VarCollector
from .analysis import DataflowAnalysis


class ReachingDefinitionAnalysis(DataflowAnalysis[Set[CFGStmt]]):
    defs: Dict[str, Set[CFGStmt]]

    def __init__(self, scope, config):
        self.is_forward = True
        self.defs = self.compute_defs(scope)
        super().__init__(scope, config)
    
    def new_boundary_fact(self) -> Set[CFGStmt]:
        return self.new_init_fact()
    
    def new_init_fact(self) -> Set[CFGStmt]:
        return {*()}
    
    def meet(self, fact_1: Set[CFGStmt], fact_2: Set[CFGStmt]) -> Set[CFGStmt]:
        return fact_1.union(fact_2)

    def need_transfer_edge(self, edge):
        super().need_transfer_edge(edge)
    
    def compute_defs(self, scope: CFGScope):
        defs = {}
        for cur_stmt in scope.cfg.stmts:
            for var_id in cur_stmt.get_stores():
                if var_id in defs:
                    defs[var_id].add(cur_stmt)
                else:
                    defs[var_id] = {cur_stmt}
        return defs

    def transfer_node(self, node: BaseBlock, fact: Set[CFGStmt]) -> Set[CFGStmt]:
        fact_out = fact.copy()
        for cur_stmt in node.stmts:
            for var_id in cur_stmt.get_stores():
                if var_id in self.defs:
                    fact_out.difference_update(self.defs[var_id])
                    fact_out.add(cur_stmt)
        return fact_out
