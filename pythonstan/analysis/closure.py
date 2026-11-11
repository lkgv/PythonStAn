from ast import stmt
from typing import Set, Iterable

from pythonstan.ir import IRScope, IRStatement, IRFunc, IRModule
from pythonstan.graph.cfg import ControlFlowGraph
from pythonstan.utils.var_collector import VarCollector
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
            # Closure consumes each scope's entry fact immediately. Keeping all
            # dense per-node liveness maps would retain a project-wide history
            # that no subsequent closure transfer reads.
            options={"type": "dataflow analysis", "retain_scope_results": False})
        self.liveness_analysis = DataflowAnalysisDriver[Set[stmt]](live_config)
        representation = config.options.get("liveness_representation", "compact")
        if representation not in ("set", "compact"):
            raise ValueError("liveness_representation must be 'set' or 'compact'")
        # Ordinary public liveness and explicitly retained debug facts keep
        # their established mutable-set representation. The private closure
        # consumer needs only one entry fact and can decode diagnostics lazily.
        if representation == "compact" and not self.liveness_analysis.retain_scope_results:
            from .compact_liveness import CompactLivenessSolver
            self.liveness_analysis.solver = CompactLivenessSolver
            self._compact_liveness_solver = CompactLivenessSolver

        from pythonstan.world import World
        self.world = World()

        super().__init__(config)

        # Builtins should not be treated as closure cells unless shadowed.
        
        from pythonstan.models.builtins import LEGACY_BUILTIN_FUNCTIONS
        self._builtin_names = LEGACY_BUILTIN_FUNCTIONS

    def _filter_builtin_cells(self, scope: IRScope, cell_vars: Set[str]) -> None:
        if not cell_vars:
            return
        ir = self.world.scope_manager.get_ir(scope, "ir")
        defined: Set[str] = set()
        if ir is not None:
            for stmt in ir:
                defined.update(stmt.get_stores())
        if isinstance(scope, IRFunc):
            defined.update(scope.get_arg_names())
        # Drop builtins that are not explicitly defined in this scope.
        to_remove = {
            name for name in cell_vars
            if name in self._builtin_names and name not in defined
        }
        cell_vars.difference_update(to_remove)
    
    def module_scopes(self, module: IRModule):
        """Return one lexical tree by object identity, without scanning World.

        Names are not identities: conditional definitions may have the same
        qualified name. The project lifetime owner checks cache collisions
        separately before using this partition for early consumption.
        """
        if not isinstance(module, IRModule):
            raise ValueError("module closure requires an IRModule")
        scopes = []
        seen = set()
        pending = [module]
        while pending:
            scope = pending.pop()
            if scope in seen:
                raise ValueError("lexical scope tree contains a repeated scope")
            seen.add(scope)
            scopes.append(scope)
            pending.extend(self.world.scope_manager.get_subscopes(scope))
        return tuple(scopes)

    def analyze_module(self, module: IRModule, prev_results=None):
        """Materialize cells for one complete lexical tree, children first."""
        self.analyze_scopes(self.module_scopes(module))

    def analyze(self, scope: IRScope, prev_results):
        # The generic Pipeline still computes the complete World forest and
        # retains its CFGs. Project streaming is a separate, explicit owner.
        self.analyze_scopes(self.world.scope_manager.get_scopes())

    def analyze_scopes(self, selected: Iterable[IRScope]):
        """Consume a lexical-child-closed forest with the ordinary transfer."""
        scope_manager = self.world.scope_manager
        scopes = set(selected)
        parent_scope = {}
        subscopes = {}

        from queue import Queue
        q = Queue()

        for scope in scopes:
            subscopes[scope] = set()
            for subscope in scope_manager.get_subscopes(scope):
                if subscope not in scopes:
                    raise ValueError("closure scope selection omits a lexical child")
                parent_scope[subscope] = scope
                subscopes[scope].add(subscope)
            if len(subscopes[scope]) == 0:
                q.put(scope)
        
        while not q.empty():
            scope = q.get()

            cfg = scope_manager.get_ir(scope, "cfg")

            if cfg:
                if (self.liveness_analysis.retain_scope_results
                        and getattr(self, "_compact_liveness_solver", None) is not None
                        and self.liveness_analysis.solver is getattr(self, "_compact_liveness_solver", None)):
                    # Honor diagnostic retention enabled after construction as
                    # well. Do not replace an explicitly injected solver.
                    from .dataflow.solver import WorklistSolver
                    self.liveness_analysis.solver = WorklistSolver
                self.liveness_analysis.analyze(scope, cfg)

                try:
                    # Cell ownership is independent of the transient flow map.
                    # Filtering must not mutate a captured raw liveness fact.
                    entry = cfg.get_entry()
                    cell_vars = set(self.liveness_analysis.results["out"][entry])
                    if isinstance(scope, IRFunc):
                        arguments = scope.get_arg_names()
                        cell_vars.difference_update(arguments)
                    self._filter_builtin_cells(scope, cell_vars)
                    if not isinstance(scope, IRModule):
                        scope.cell_vars = cell_vars
                finally:
                    if not self.liveness_analysis.retain_scope_results:
                        # Drop containers, not fact sets. Explicit diagnostic
                        # consumers can still keep their own observation copies.
                        self.liveness_analysis.results = {"per_scope": {}}

            parent = parent_scope.get(scope, None)
            if parent:
                subscopes[parent].remove(scope)
                if len(subscopes[parent]) == 0:
                    q.put(parent)
        
        self.results = None
