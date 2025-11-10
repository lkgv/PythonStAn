from ast import stmt
from typing import Set, Iterator

from pythonstan.ir import IRScope, IRStatement
from pythonstan.graph.cfg import ControlFlowGraph
from .analysis import AnalysisConfig, AnalysisDriver
from .dataflow.driver import DataflowAnalysisDriver

__all__ = ["ClosureAnalysis"]


class ClosureAnalysis(AnalysisDriver):
    compute_stores: bool
    compute_loads: bool

    def __init__(self, config: AnalysisConfig):
        live_config = AnalysisConfig(
            name="liveness-analysis",
            id="LivenessAnalysis",
            options={"type": "dataflow analysis"})
        self.liveness_analysis = DataflowAnalysisDriver[Set[stmt]](live_config)

        from pythonstan.world import World
        self.world = World()

        super().__init__(config)
    
    def analyze(self, scope: IRScope, prev_results):
        scope_manager = self.world.scope_manager
        scopes = scope_manager.get_scopes()
        parent_scope = {}
        subscopes = {}

        from queue import Queue
        q = Queue()

        for scope in scopes:
            subscopes[scope] = set()
            for subscope in scope_manager.get_subscopes(scope):
                parent_scope[subscope] = scope
                subscopes[scope].add(subscope)
            if len(subscopes[scope]) == 0:
                q.put(scope)
        
        while not q.empty():
            scope = q.get()

            cfg = scope_manager.get_ir(scope, "cfg")

            if cfg:
                self.liveness_analysis.analyze(scope, cfg)

                entry = cfg.get_entry()
                cell_vars = self.liveness_analysis.results["in"][entry]
                for var in cell_vars:
                    scope.add_cell_var(var)

            parent = parent_scope.get(scope, None)
            if parent:
                subscopes[parent].remove(scope)
                if len(subscopes[parent]) == 0:
                    q.put(parent)
        
        self.results = None
