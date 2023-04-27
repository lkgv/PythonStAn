from typing import Set, Dict
from ast import stmt

from pythonstan.graph.cfg.models import BaseBlock, CFGScope
from pythonstan.utils.var_collector import VarCollector
from .analysis import DataflowAnalysis


class ReachingDefinitionAnalysis(DataflowAnalysis[Set[stmt]]):
    defs: Dict[str, Set[stmt]]

    def __init__(self, scope, config):
        self.is_forward = True
        print(scope)
        print(config)
        self.defs = self.compute_defs(scope)
        super().__init__(scope, config)
    
    def new_boundary_fact(self) -> Set[stmt]:
        return self.new_init_fact()
    
    def new_init_fact(self) -> Set[stmt]:
        return {*()}
    
    def meet(self, fact_1: Set[stmt], fact_2: Set[stmt]) -> Set[stmt]:
        return fact_1.union(fact_2)

    def need_transfer_edge(self, edge):
        super().need_transfer_edge(edge)
    
    def compute_defs(self, scope: CFGScope):
        defs = {}
        def_colle = VarCollector()
        for cur_stmt in scope.cfg.stmts:
            def_colle.reset("store")
            def_colle.visit(cur_stmt)
            for var_id in def_colle.get_vars():
                if var_id in defs:
                    defs[var_id].append(cur_stmt)
                else:
                    defs[var_id] = [cur_stmt]
        return defs

    def transfer_node(self, node: BaseBlock, fact: Set[stmt]) -> Set[stmt]:
        fact_out = fact.copy()
        def_colle = VarCollector("store")
        for cur_stmt in node.stmts:
            def_colle.visit(cur_stmt)
            for var_id in def_colle.get_vars():
                if var_id in self.defs:
                    fact_out.difference_update(self.defs[var_id])
                    fact_out.add(cur_stmt)
        return fact_out
