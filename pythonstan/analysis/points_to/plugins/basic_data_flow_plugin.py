from .plugin import Plugin
from ..cs_call_graph import CSCallEdge
from ..solver_interface import SolverInterface
from ..context import Context
from ..elements import *
from ..pointer_flow_graph import FlowKind
from ..heap_model import Obj
from pythonstan.graph.call_graph import CallKind
from pythonstan.utils.persistent_rb_tree import PersistentMap, PersistentSet
from pythonstan.ir import *

from typing import Iterable, Tuple, Optional, Dict, Set
import ast

ALLOW_UNKNOWN_OBJ = True


# Used in the abstract interpretation phase, store value for each pointer(var, index, ...)
class Values:
    objs: Set[CSObj]

    def __init__(self, objs: Optional[Set[CSObj]]):
        if objs is None:
            self.objs = set()
        else:
            self.objs = objs

    def get_objs(self) -> Set[CSObj]:
        return self.objs


class State:
    mem: PersistentMap[str, CSVar]
    val_map: Dict[Pointer, Values]

    def __init__(self, c: SolverInterface):
        self.mem = PersistentMap()
        self.c = c

    def items(self) -> Iterable[Tuple[CSVar, CSVar]]:
        return self.mem.items()

    def meet(self, rhs: 'State') -> Tuple[bool, 'State']:
        change = False
        result = State(self.c)
        result.mem.recover(self.mem.backup())
        for name, var in rhs.mem.items():
            if name in result.mem:
                var_src = result.mem[name]
                if var_src != var:
                    ctx, raw_var_src = var_src
                    new_var = ctx, Var.copy_from(raw_var_src)
                    result.mem[name] = new_var
                    self.c.add_pfg_edge(var_src, new_var, FlowKind.LOCAL_ASSIGN)
                    self.c.add_pfg_edge(var, new_var, FlowKind.LOCAL_ASSIGN)
            else:
                result.mem[name] = var
                change = True
        return change, result

    def get(self, name: str) -> Optional[CSVar]:
        return self.mem.get(name)

    def set(self, name: str, var: CSVar):
        self.mem[name] = var

    def copy(self) -> 'State':
        result = State(self.c)
        result.mem.recover(self.mem.backup())
        return result


class StmtProcessor(IRVisitor):
    cur_state: State
    state_before: Dict[IRStatement, State]
    state_after: Dict[IRStatement, State]
    context: Context


    def __init__(self, c: SolverInterface, scope: CSScope):
        self.c = c
        self.scope = scope
        self.state_before = {}
        self.state_after = {}

    def visit_stmts(self, stmts: Iterable[IRStatement], init_state: State, context: Context):
        self.cur_state = init_state
        self.context = context
        for stmt in stmts:
            self.state_before[stmt] = self.cur_state.copy()
            self.visit(stmt)
            self.state_after[stmt] = self.cur_state.copy()

    def generate_new_var(self, var_name: str, old_vars: Optional[Iterable[CSVar]] = None) -> CSVar:
        new_var = self.c.cs_manager.get_var(self.context, Var(var_name))
        if old_vars is not None:
            for old_var in old_vars:
                self.c.add_pfg_edge(old_var, new_var, FlowKind.LOCAL_ASSIGN)
        self.cur_state.set(var_name, new_var)
        return new_var

    @staticmethod
    def collect_vars(var_name: str, states: Iterable[State]) -> Iterable[CSVar]:
        ret = []
        for state in states:
            var = state.get(var_name)
            if var is not None:
                ret.append(var)
        return ret

    def visit_IRAssign(self, stmt: IRAssign):
        lval = self.generate_new_var(stmt.lval.id, self.collect_vars(stmt.lval.id, [self.cur_state]))
        if isinstance(stmt.rval, ast.Name):
            rval = self.cur_state.get(stmt.rval.id)
            if rval is None:
                if ALLOW_UNKNOWN_OBJ:
                    unknown_obj = self.c.heap_model.get_unknown_obj()
                    rval = self.generate_new_var(stmt.rval.id)
                    self.c.add_var_points_to_heap_obj(self.context, rval.get_var(), self.context, unknown_obj)
                    self.c.add_pfg_edge(rval, lval, FlowKind.LOCAL_ASSIGN)
                else:
                    raise ValueError(f"Unresolved variable {stmt.rval.id}")
            else:
                self.c.add_pfg_edge(rval, lval, FlowKind.LOCAL_ASSIGN)
        elif isinstance(stmt.rval, ast.Constant):
            obj = self.c.heap_model.get_constant_obj(stmt.rval.value)
            heap_ctx = self.c.context_selector.select_heap_context(self.scope, obj)
            self.c.add_var_points_to_heap_obj(self.context, lval.get_var(), heap_ctx, obj)
        elif isinstance(stmt.rval, ast.Tuple):
            ...
        elif isinstance(stmt.rval, ast.Add):
            ...

    # TODO fix it
    def visit_IRStoreAttr(self, stmt: IRStoreAttr):
        base = self.cur_state.get(stmt.base.id)
        if base is None:
            if ALLOW_UNKNOWN_OBJ:
                base = self.c.heap_model.get_unknown_obj()
            else:
                raise ValueError(f"Unresolved variable {stmt.base.id}")
        field = stmt.field
        for base_obj in base:
            if base_obj.is_functional():
                inst_field = self.c.cs_manager.get_instance_field(base_obj, field)
                self.c.add_pfg_edge(base_obj, inst_field, FlowKind.INSTANCE_STORE)


    def visit_IRLoadAttr(self, stmt: IRLoadAttr):
        lval = self.generate_new_var(stmt.lval.id, self.collect_vars(stmt.lval.id, [self.cur_state]))
        base = self.cur_state.get(stmt.get_obj().id)
        if base is None:
            if ALLOW_UNKNOWN_OBJ:
                unknown_obj = self.c.heap_model.get_unknown_obj()
                self.c.add_var_points_to_heap_obj(self.context, lval.get_var(), self.context, unknown_obj)
            else:
                raise ValueError(f"Unresolved variable {stmt.get_obj().id}")
        field = stmt.get_attr()
        for obj in base.get_points_to_set():
            inst_field = self.c.cs_manager.get_instance_field(obj, field)
            self.c.add_pfg_edge(inst_field, lval, FlowKind.INSTANCE_LOAD)

    # TODO fix it
    def visit_IRCall(self, stmt: IRCall):
        fn_var = self.cur_state.get(stmt.get_func_name())
        if fn_var is not None:
            for fn_obj in fn_var.get_points_to_set():
                if fn_obj.is_functional():
                    for callee in self.c.cs_manager.get_callees(self.context, fn_obj):
                        cs_callee = self.c.cs_manager.get_scope(self.context, callee)
                        self.c.add_call_edge(CSCallEdge(self.c.get_call_kind(stmt), self.context, cs_callee))



class BasicDataFlowPlugin(Plugin):
    def __init__(self, c: SolverInterface):
        self.c = c

    def on_new_call_edge(self, edge: CSCallEdge):
        callee_ctx, callee_scope = edge.get_callee()
        init_state = State(self.c, callee_ctx)
        for var in callee_scope.get_vars():
            init_state.mem[var.get_name()] = callee_ctx, var
        stmts = self.c.scope_manager.get_ir(callee_scope, "ir")
        stmt_processor = StmtProcessor(self.c, callee_scope)
        stmt_processor.visit(stmts)


        

