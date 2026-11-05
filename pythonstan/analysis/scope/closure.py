from typing import Set

from pythonstan.graph.cfg import IRClass, IRFunc, IRModule
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
        self._builtin_names = {
            "iter", "next", "len", "enumerate", "zip", "map", "filter",
            "range", "reversed", "sorted", "sum", "min", "max", "all", "any",
            "list", "dict", "tuple", "set", "frozenset",
            "str", "int", "float", "bool", "bytes",
            "isinstance", "issubclass", "type", "hasattr", "getattr", "setattr",
            "delattr", "vars", "callable",
            "super", "print", "input", "open",
        }

    def _filter_builtin_cells(self, scope, cell_vars: Set[str]) -> None:
        if not cell_vars:
            return
        defined: Set[str] = set()
        if hasattr(scope, "cfg") and scope.cfg is not None:
            for stmt in scope.cfg.stmts:
                defined.update(stmt.get_stores())
        if isinstance(scope, IRFunc):
            defined.update(scope.get_arg_names())
        to_remove = {
            name for name in cell_vars
            if name in self._builtin_names and name not in defined
        }
        cell_vars.difference_update(to_remove)
    
    def analyze_function(self, fn: IRFunc, fact=None) -> Set[str]:
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
            self._filter_builtin_cells(fn, fact)
            fn.func_def.set_cell_vars(fact)
        return fact

    def analyze_class(self, cls: IRClass, fact=None) -> Set[str]:
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
            self._filter_builtin_cells(cls, fact)
            cls.class_def.set_cell_vars(fact)
        return fact

    def analyze_module(self, mod: IRModule, fact=None) -> Set[str]:
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
    
    def init_function(self, fn: IRFunc) -> Set[str]:
        return {*()}

    def init_class(self, cls: IRClass) -> Set[str]:
        return {*()}

    def init_module(self, mod: IRModule) -> Set[str]:
        return {*()}
