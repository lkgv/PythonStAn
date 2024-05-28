from typing import Set, List, Optional

from .work_list import Worklist
from .cs_manager import CSManager
from .heap_model import HeapModel
from .context_selector import ContextSelector
from .context import CSVar, CSObj, CSCallSite, CSScope
from .cs_call_graph import CSCallGraph
from pythonstan.graph.call_graph import CallEdge, CallKind
from .pointer_flow_graph import PointerFlowGraph, PointerFlowEdge, FlowKind, EdgeTransfer
from .points_to_set import PointsToSet
from .elements import Pointer
from ..analysis import AnalysisConfig
from .plugins import Plugin
from pythonstan.ir import IRScope, IRFunc
from .solver_interface import SolverInterface
from .stmts import PtStmt, PtInvoke


class Solver:
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
        self.c.call_graph = CSCallGraph(self.c.cs_manager)
        self.c.pfg = PointerFlowGraph()
        self.c.reachable_scopes = set()
        self.plugin.on_start()

    def analyze(self):
        while not self.c.work_list.is_empty():
            entry = self.c.work_list.get()
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
                self.c.add_points_to_pts(e.get_tgt(), diff)
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
                        self.c.add_pfg_edge(cs_from_var, inst_field, FlowKind.INSTANCE_STORE)

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
                        self.c.add_pfg_edge(inst_field, cs_to_var, FlowKind.INSTANCE_LOAD)

    def process_call(self, recv: CSVar, pts: PointsToSet):
        ctx, var = recv
        stmt_colle = self.c.var2stmtcolle.get(var)
        if stmt_colle is not None:
            for stmt in stmt_colle.get_invokes():
                for recv_obj in pts:
                    callee = self.resolve_callee(recv_obj, stmt)
                    if callee is not None:
                        cs_callsite = self.c.cs_manager.get_callsite(ctx, stmt)
                        callee_ctx = self.c.context_selector.select_instance_context(cs_callsite, recv_obj, callee)
                        cs_callee = self.c.cs_manager.get_scope(callee_ctx, callee)
                        self.c.add_call_edge(CallEdge(self.c.get_call_kind(stmt), cs_callsite, cs_callee))
                        # TODO should add the self obj
                        # self.add_var_points_to(callee_ctx, callee, recv_obj)
                    else:
                        self.plugin.on_unresolved_call(recv_obj, ctx, stmt)

    def process_call_edge(self, edge: CallEdge[CSCallSite, CSScope]):
        if self.c.call_graph.add_edge(edge):
            cs_callee = edge.get_callee()
            self.add_cs_scope(cs_callee)
            if edge.get_kind() != CallKind.OTHER:
                caller_ctx = edge.get_callsite().get_context()
                call_site = edge.get_callsite().get_callsite()
                callee_ctx, callee = cs_callee

                # input args should be more elegent and robust
                if isinstance(callee, IRFunc):
                    n_args = len(call_site.get_args())
                    for i in range(n_args):
                        arg = call_site.get_args()[i]
                        param = callee.get_arg_names()[i]
                        ...

                if call_site.get_result() is not None:
                    lhs = self.c.cs_manager.get_var(caller_ctx, call_site.get_result())
                    for ret in callee.get_return_vars:
                        ...

            self.plugin.on_new_call_edge(edge)

    def process_new_scope(self, scope: IRScope):
        if scope not in self.c.reachable_scopes:
            self.c.reachable_scopes.add(scope)
            self.plugin.on_new_scope(scope)
            for stmt in self.get_pt_ir(scope):
                self.plugin.on_new_stmt(stmt, scope)

    # logic ends

    def resolve_callee(self, recv_obj: CSObj, stmt: PtInvoke) -> Optional[IRScope]:
        ...

    def get_pt_ir(self, scope: IRScope) -> List[PtStmt]:
        return self.c.scope2pt_ir.get(scope, [])

    def add_cs_scope(self, cs_scope: CSScope):
        if cs_scope not in self.c.call_graph.reachable_scopes:
            self.c.call_graph.reachable_scopes.add(cs_scope)
            _, scope = cs_scope
            self.process_new_scope(scope)
            self.plugin.on_new_cs_scope(cs_scope)

    def add_entry_point(self):
        ...


