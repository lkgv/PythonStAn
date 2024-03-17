from typing import Set, List

from .work_list import Worklist
from .cs_manager import CSManager
from .heap_model import HeapModel
from .context_selector import ContextSelector
from .context import CSVar
from .cs_call_graph import CSCallGraph, CallEdge
from .pointer_flow_graph import PointerFlowGraph, PointerFlowEdge, FlowKind
from .points_to_set import PointsToSet
from .elements import Pointer
from ..analysis import AnalysisConfig
from .plugin import Plugin
from pythonstan.ir import IRScope
from .solver_interface import SolverInterface
from .stmts import PtStmt


class StmtProcessor:
    ...


class Solver:
    work_list: Worklist
    plugin: Plugin
    c: SolverInterface

    def __init__(self, config: AnalysisConfig, heap_model: HeapModel,
                 context_selector: ContextSelector, cs_manager: CSManager):
        self.c = SolverInterface(config, heap_model, context_selector, cs_manager)

    def get_points_to_set_of(self, pointer: Pointer) -> PointsToSet:
        pts = pointer.get_points_to_set()
        if pts is None:
            pts = PointsToSet()
            pointer.set_points_to_set(pts)
        return pts

    def set_plugin(self, plugin: Plugin):
        self.plugin = plugin

    def solve(self):
        self.initialize()
        self.analyze()

    def initialize(self):
        self.work_list = Worklist()
        self.c.call_graph = CSCallGraph(self.cs_manager)
        self.c.pfg = PointerFlowGraph()
        self.c.reachable_scopes = set()
        self.plugin.on_start()

    def analyze(self):
        while not self.work_list.is_empty():
            entry = self.work_list.get()
            if entry is None:
                break
            elif isinstance(entry, CallEdge):
                self.process_call_edge(entry)
            elif isinstance(entry, tuple):
                p, pts = entry
                diff = self.propagate(p, pts)
                if not diff.is_empty() and isinstance(p, CSVar):
                    self.process_instance_store(p, diff)
                    self.process_instance_load(p, diff)
                    self.process_call(p, diff)
                    self.plugin.on_new_pts(p, diff)
        self.plugin.on_finish()

    def propagate(self, pointer: Pointer, pts: PointsToSet) -> PointsToSet:
        diff = self.get_points_to_set_of(pointer) - pts
        self.get_points_to_set_of(pointer).add_all(pts)
        if not diff.is_empty():
            for e in self.c.pfg.get_out_edges_of(pointer):
                self.add_points_to(e.tgt, diff)
        return diff

    def process_instance_store(self, base_var: CSVar, pts: PointsToSet):
        ctx, var = base_var
        stmt_colle = self.c.var2stmtcolle.get(var)
        if stmt_colle is not None:
            for stmt in stmt_colle.get_store_attrs():
                from_var = stmt.get_rval()
                cs_from_var = self.c.cs_manager.get_var(ctx, from_var)
                field = stmt.get_field()
                for base_obj in pts:
                    if base_obj[1].is_functional():
                        inst_field = self.c.cs_manager.get_instance_field(base_obj, field)
                        self.add_pfg_edge(cs_from_var, inst_field, FlowKind.INSTANCE_STORE)

    def process_instance_load(self, base_var: CSVar, pts: PointsToSet):
        ctx, var = base_var
        stmt_colle = self.c.var2stmtcolle.get(var)
        if stmt_colle is not None:
            for stmt in stmt_colle.get_load_attrs():
                to_var = stmt.get_lval()
                cs_to_var = self.c.cs_manager.get_var(ctx, to_var)
                field = stmt.get_field()
                for base_obj in pts:
                    if base_obj[1].is_functional():
                        inst_field = self.c.cs_manager.get_instance_field(base_obj, field)
                        self.add_pfg_edge(inst_field, cs_to_var, FlowKind.INSTANCE_LOAD)

    def process_call(self, recv: CSVar, pts: PointsToSet):
        ctx, var = recv
        stmt_colle = self.c.var2stmtcolle.get(var)
        if stmt_colle is not None:
            for stmt in stmt_colle.get_invokes():
                callee: OptiIRScope = self.c.call_graph.get_callees_of(stmt)



    def process_new_scope(self, scope: IRScope):
        if scope not in self.c.reachable_scopes:
            self.c.reachable_scopes.add(scope)
            self.plugin.on_new_scope(scope)
            for stmt in self.get_pt_ir(scope):
                self.plugin.on_new_stmt(stmt, scope)


    # logic ends

    def resolve_field(self, var: Var, field: str) -> :
        self.c.

    def get_pt_ir(self, scope: IRScope) -> List[PtStmt]:
        return self.c.scope2pt_ir.get(scope, [])

    def add_cs_method(self):
        ...

    def add_stmts(self):
        ...

    def init_class(self):
        ...

    def add_pdg_edge(self):
        ...

    def add_points_to(self, pointer: Pointer, pts: PointsToSet):
        self.work_list.add_pts(pointer, pts)

    def add_entry_point(self):
        ...

    def add_var_points_to(self):
        ...
