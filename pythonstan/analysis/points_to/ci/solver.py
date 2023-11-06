from typing import Set, Dict, Tuple
import ast

from .pointer import Obj, Pointer, Var, ObjAttr
from .pointer_flow_graph import PointerFlowGraph
from .call_graph import CallSite, CallGraph
from pythonstan.ir import *


class StmtProcessor:
    def process(self, func: IRFunc, stmts: Set[IRStatement]):


class PointerManager:
    vars: Dict[str, Var]
    obj_attr: Dict[Tuple[Obj, str], ObjAttr]
    funcs: Dict[Obj, IRFunc]
    method_self: Dict[IRFunc, Var]

    def __init__(self):
        self.vars = {}
        self.obj_attr = {}
        self.funcs = {}
        self.method_self

    def get_var(self, var_name: str) -> Var:
        self.vars.setdefault(var_name, Var(var_name))
        return self.vars[var_name]

    def get_obj_attr(self, obj: Obj, attr: str) -> ObjAttr:
        self.obj_attr.setdefault((obj, attr), ObjAttr(obj, attr))
        return self.obj_attr[(obj, attr)]

    def get_obj_method(self, obj: Obj) -> IRFunc:
        if obj in self.funcs:
            return self.funcs[obj]

    def add_obj_method(self, obj: Obj, func: IRFunc):
        self.funcs[obj] = func

    def get_self(self, func: IRFunc):
        self.method_self.setdefault(func, Var("self"))
        return self.method_self[func]


class Solver:
    pfg: PointerFlowGraph

    def __init__(self):
        ...

    def init(self):
        self.cg = CallGraph()
        self.wl = []
        self.reachable_func = set()
        self.inited_cls = set()
        self.ignored_func = set()
        self.stmt_processor = StmtProcessor()
        self.pm = PointerManager()

    def analyze(self):
        while len(self.wl) > 0:
            label, content = self.wl.pop()
            if label == 'var':
                p, pts = content
                diff = self.propagate(p, pts)
                if len(diff) > 0:
                    self.process_instance_store(p, diff)
                    self.process_instance_load(p, diff)
                    self.process_call(p, diff)
            elif label == 'call':
                callsite, callee = content
                self.process_call_edge(callsite, callee)

    def propagate(self, p: Pointer, pts: Set[Obj]) -> Set[Obj]:
        diff = pts.difference(p.get_pts())
        p.get_pts().update(pts)
        # if len(diff) > 0:
        #     for e in self.pfg.get_out_edges_of(p):
        #         for transfer in e.get_transfers():
        #             self.add_points_to(e.tgt, transfer.apply(e, diff))
        return diff

    def process_instance_store(self, base_var: Var, pts: Set[Obj]):
        for stmt in base_var.get_store_attrs():
            from_expr = stmt.get_rval()
            assert isinstance(from_expr, ast.Name), "rval of store_attr should be ast.Name!"
            from_var = self.pm.get_var(from_expr.id)
            for obj in pts:
                attr = self.pm.get_obj_attr(obj, stmt.get_attr())
                self.pfg.add_edge(from_var, attr)

    def process_instance_load(self, base_var: Var, pts: Set[Obj]):
        for stmt in base_var.get_load_attrs():
            to_expr = stmt.get_lval()
            assert isinstance(to_expr, ast.Name), "lval of load_attr should be ast.Name!"
            to_var = self.pm.get_var(to_expr.id)
            for obj in pts:
                attr = self.pm.get_obj_attr(obj, stmt.get_attr())
                self.pfg.add_edge(attr, to_var)

    def resolve_callee(self, obj: Obj, stmt: IRCall) -> Set[IRFunc]:
        Obj.get_cls()

    def process_call(self, var: Var, pts: Set[Obj]):
        for call in var.get_calls():
            for obj in pts:
                for callee in self.resolve_callee(obj, call):
                    callsite = self.pm.get_callsite(call)
                    self.wl.append(("call", (callsite, callee)))
                    self.wl.append(("var", (self.pm.get_self(callee), {obj})))

    def add_func(self, func: IRFunc):
        if self.cg.add_reachable_func(func):
            from pythonstan import world
            cfg = world.World().scope_manager.get_ir(func, "cfg")
            self.stmt_processor.process(func, cfg.get_stmts())

    def process_call_edge(self, callsite: CallSite, callee: IRFunc):
        if self.cg.add_edge(callsite, callee):
            self.add_func(callee)
            

