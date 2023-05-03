from typing import Set

from pythonstan.graph.cfg import CFGClass, CFGFunc, CFGModule
from pythonstan.analysis.dataflow import DataflowAnalysisDriver
from pythonstan.analysis import AnalysisConfig
from pythonstan.utils.var_collector import VarCollector
from .analysis import ScopeAnalysis


class ClosureAnalysis(ScopeAnalysis[Set[str]]):
    liveness_driver: DataflowAnalysisDriver
    in_place: bool

    def __init__(self, config: AnalysisConfig):
        super().__init__(config)
        liveness_config = AnalysisConfig("liveness", "LivenessAnalysis",
                          options={'solver': 'WorklistSolver'})
        self.liveness_driver = DataflowAnalysisDriver(liveness_config)
        self.in_place = config.options.get('in_place', False)
    
    def analyze_function(self, fn: CFGFunc, fact=None) -> Set[str]:
        if fact is None:
            fact = self.init_function(fn)
        self.liveness_driver.analyze(fn)
        result = self.liveness_driver.results
        free_vars = result['out'][fn.cfg.entry_blk]
        s_colle = VarCollector(ctx="store")
        s_colle.visit(fn.func_def.args)
        free_vars.difference_update(s_colle.get_vars())
        for cls in fn.classes:
            fact.update(self.analyze_class(cls, {*()}))
        for fn in fn.funcs:
            fact.update(self.analyze_function(fn, {*()}))
        for stmt in fn.cfg.stmts:
            s_colle.visit(stmt)
        fact.difference_update(s_colle.get_vars())
        fact.update(free_vars)
        if self.in_place:
            fn.func_def.set_cell_vars(fact)
        return fact

    def analyze_class(self, cls: CFGClass, fact=None) -> Set[str]:
        if fact is None:
            fact = self.init_class(cls)
        self.liveness_driver.analyze(cls)
        result = self.liveness_driver.results
        free_vars = result['out'][cls.cfg.entry_blk]
        for cls in cls.classes:
            fact.update(self.analyze_class(cls, {*()}))
        for fn in cls.funcs:
            fact.update(self.analyze_function(fn, {*()}))
        fact.update(free_vars)
        if self.in_place:
            cls.class_def.set_cell_vars(fact)
        return fact

    def analyze_module(self, mod: CFGModule, fact=None) -> Set[str]:
        if fact is None:
            fact = self.init_module(mod)
        self.liveness_driver.analyze(mod)
        result = self.liveness_driver.results
        free_vars = result['out'][mod.cfg.entry_blk]
        for cls in mod.classes:
            fact.update(self.analyze_class(cls, {*()}))
        for fn in mod.funcs:
            fact.update(self.analyze_function(fn, {*()}))
        s_colle = VarCollector(ctx="store")
        for stmt in mod.cfg.stmts:
            s_colle.visit(stmt)
        fact.difference_update(s_colle.get_vars())
        fact.update(free_vars)
        return fact
    
    def init_function(self, fn: CFGFunc) -> Set[str]:
        return {*()}

    def init_class(self, cls: CFGClass) -> Set[str]:
        return {*()}

    def init_module(self, mod: CFGModule) -> Set[str]:
        return {*()}
