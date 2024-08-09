from typing import Set, List, Optional

from .work_list import Worklist
from .cs_manager import CSManager
from .heap_model import HeapModel
from .context_selector import ContextSelector
from .cs_call_graph import CSCallGraph, CSCallEdge
from pythonstan.graph.call_graph import CallEdge, CallKind
from .pointer_flow_graph import PointerFlowGraph, PointerFlowEdge, FlowKind, EdgeTransfer
from .elements import *
from ..analysis import AnalysisConfig
from .plugins import Plugin
from pythonstan.ir import IRScope, IRFunc
from .solver_interface import SolverInterface


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
            elif isinstance(entry, tuple) and isinstance(entry[0], CSCallEdge):
                edge, frame = entry
                self.process_call_edge(edge, frame)
            elif isinstance(entry, tuple):
                p, pts = entry
                diff = self.propagate(p, pts)
                if not diff.is_empty() and isinstance(p, Var):
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

    def process_instance_store(self, var: Var, pts: PointsToSet):
        for stmt in var.get_stmt_collector().get_store_attrs():
            from_var = stmt.get_rval()
            for obj in pts:
                field = self.c.get_property(obj, stmt.get_field(), True)
                self.c.add_pfg_edge(from_var, field, FlowKind.INSTANCE_STORE)

    def process_instance_load(self, var: Var, pts: PointsToSet):
        for stmt in var.get_stmt_collector().get_load_attrs():
            to_var = stmt.get_lval()
            for obj in pts:
                field = self.c.get_property(obj, stmt.get_field(), False)
                if field is None:
                    self.c.new_property(obj, stmt.get_field())
                    field = self.c.get_property(obj, stmt.get_field(), False)
                self.c.add_pfg_edge(field, to_var, FlowKind.INSTANCE_LOAD)

    def process_call(self, func: Var, pts: PointsToSet):
        for stmt in func.get_stmt_collector().get_invokes():
            for obj in pts:
                if isinstance(obj, FunctionObj):
                    callee_ctx = self.c.context_selector.select_static_context(stmt, obj)
                    frame = self.c.cs_manager.get_frame(stmt, obj, callee_ctx)
                    call_edge = CSCallEdge(CallKind.FUNCTION, stmt, obj)
                    self.c.add_call_edge(call_edge, frame)
                elif isinstance(obj, MethodObj):
                    host_obj = obj.get_obj()
                    callee_ctx = self.c.context_selector.select_instance_context(stmt, host_obj, obj)
                    frame = self.c.cs_manager.get_frame(stmt, obj, callee_ctx)
                    call_edge = CallEdge(CallKind.INSTANCE, stmt, obj)
                    self.c.add_call_edge(call_edge, frame)
                elif isinstance(obj, ClassObj):
                    target = stmt.get_target()
                    if target is not None:
                        alloc = PtAllocation(stmt.get_ir(), stmt.get_frame(), obj)
                        instance_obj = self.c.heap_model.get_obj(alloc, obj)
                        init_func_obj = obj.get_init_method()
                        if init_func_obj is not None:
                            callee_ctx = self.c.context_selector.select_instance_context(stmt, instance_obj, init_func_obj)
                            frame = self.c.cs_manager.get_frame(stmt, init_func_obj, callee_ctx)
                            call_edge = CallEdge(CallKind.INSTANCE, stmt, init_func_obj)
                            self.c.add_call_edge(call_edge, frame)
                        self.c.add_points_to_obj(target, instance_obj)
                else:
                    pass

    def process_call_edge(self, edge: CSCallEdge, frame: PtFrame):
        if self.c.call_graph.add_edge(edge):
            callee = edge.get_callee()
            assert isinstance(callee, CallableObj), f'callee is not CallableObj: {callee}'
            self.c.call_graph.set_edge_to_frame(edge, frame)
            self.add_scope(callee)
            if edge.get_kind() != CallKind.OTHER:
                caller_ctx = edge.get_callsite().get_context()
                call_site = edge.get_callsite()

                # input args should be more elegent and robust
                if isinstance(callee, FunctionObj):
                    for arg, param in zip(call_site.get_args(), callee.get_params()):
                        self.c.add_pfg_edge(arg, param, FlowKind.PARAM)
                elif isinstance(callee, MethodObj):
                    recv_obj = call_site.get_args()[0]
                    # TODO add method obj

                if call_site.get_target() is not None:
                    ret_var = self.c.get_return_var(frame)
                    self.c.add_pfg_edge(ret_var, call_site.get_target(), FlowKind.RETURN)

            self.plugin.on_new_call_edge(edge)

    def process_new_scope(self, scope: CallableObj):
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

    def add_scope(self, scope: CallableObj):
        if scope not in self.c.call_graph.reachable_scopes:
            self.c.call_graph.reachable_scopes.add(scope)
            self.plugin.on_new_cs_scope(scope)
            self.process_new_scope(scope)

    def add_entry_point(self):
        ...

