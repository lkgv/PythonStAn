from .plugin import Plugin
from ..cs_call_graph import CSCallEdge
from ..solver_interface import SolverInterface
from ..context import Context
from ..elements import *
from ..pointer_flow_graph import FlowKind
from ..stmts import *
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
    val_map: PersistentMap[Pointer, Values]

    def __init__(self, c: SolverInterface):
        self.mem = PersistentMap()
        self.val_map = PersistentMap()
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

    @staticmethod
    def search_vars(var_name: str, states: Iterable['State']) -> Set[CSVar]:
        ret = set()
        for state in states:
            var = state.get(var_name)
            if var is not None:
                ret.add(var)
        return ret



class StmtProcessor(IRVisitor):
    cur_state: State
    state_before: Dict[IRStatement, State]
    state_after: Dict[IRStatement, State]
    ret_var: CSVar
    context: Context
    stmt_collector: StmtCollector

    # do weak update just in special cases
    # do dataflow, add edges in PFG just in static case, eg. y = 3, y = x.
    # staticproperty and class property seen as instance property of class obj.


    def __init__(self, c: SolverInterface, scope: CSScope):
        self.c = c
        self.scope = scope
        self.state_before = {}
        self.state_after = {}
        self.stmt_collector = StmtCollector()

    def visit_stmts(self, stmts: Iterable[IRStatement], init_state: State, context: Context):
        self.cur_state = init_state
        self.context = context
        self.ret_var = self.get_var('return')
        for stmt in stmts:
            self.state_before[stmt] = self.cur_state.copy()
            self.visit(stmt)
            self.state_after[stmt] = self.cur_state.copy()

    def retrive_var(self, name: str) -> Optional[CSVar]:
        return  self.cur_state.get(name)

    def get_var(self, name: str) -> CSVar:
        var = self.retrive_var(name)
        if var is None:
            new_var = self.c.cs_manager.get_var(self.context, Var(name))
            self.cur_state.set(name, new_var)
            return new_var
        else:
            return var

    def visit_IRAssign(self, stmt: IRAssign):
        lval = self.get_var(stmt.lval.id)

        # Copy
        if isinstance(stmt.rval, ast.Name):
            rval = self.get_var(stmt.rval.id)
            self.c.add_pfg_edge(rval, lval, FlowKind.LOCAL_ASSIGN)

        # Assign Constant
        elif isinstance(stmt.rval, ast.Constant):
            obj = self.c.heap_model.get_constant_obj(stmt.rval.value)
            heap_ctx = self.c.context_selector.select_heap_context(self.scope, obj)
            self.c.add_var_points_to_heap_obj(self.context, lval.get_var(), heap_ctx, obj)

        # Assign Tuple
        elif isinstance(stmt.rval, ast.Tuple):
            ...


        elif isinstance(stmt.rval, ast.Add):
            # generate 2 instructions. op = a1.__add__; call(op, a1, a2);

            expr = stmt.rval
            assert isinstance(expr, ast.Add)
            expr.
            if isinstance(stmt.)



    # StoreAttr should just emit the PtIR but resolving the points-to relation.
    def visit_IRStoreAttr(self, stmt: IRStoreAttr):
        base = self.get_var(stmt.get_obj().id)
        field = stmt.get_attr()
        rval = self.get_var(stmt.get_rval().id)
        self.stmt_collector.add_store_attr(PtStoreAttr(stmt, self.scope, base, rval, field))

    '''
        for obj in base.get_points_to_set():
            inst_field = self.c.cs_manager.get_instance_field(obj, field)
            rval = self.retrive_var(stmt.get_rval().id)
            if rval is None:
                if ALLOW_UNKNOWN_OBJ:
                    unknown_obj = self.c.heap_model.get_unknown_obj()
                    rval = self.get_var(stmt.get_rval().id)
                    self.c.add_var_points_to_heap_obj(self.context, rval.get_var(), self.context, unknown_obj)
                    self.c.add_pfg_edge(rval, inst_field, FlowKind.INSTANCE_STORE)
                else:
                    raise ValueError(f"Unresolved variable {stmt.get_rval().id}")
            else:
                self.c.add_pfg_edge(rval, inst_field, FlowKind.INSTANCE_STORE)

            # Generate PtIR
            self.stmt_collector.add_store_attr(PtStoreAttr(stmt, self.scope, inst_field, rval, field))
    '''

    def visit_IRLoadAttr(self, stmt: IRLoadAttr):
        base = self.get_var(stmt.get_obj().id)
        field = stmt.get_attr()
        lval = self.get_var(stmt.get_lval().id)
        self.stmt_collector.add_load_attr(PtLoadAttr(stmt, self.scope, lval, base, field))


    def generate_call_instr(self, fn_var: CSVar, args: List[Tuple[str, bool]], keywords: List[Tuple[Optional[str], str]]
                            ) -> PtInvoke:
        ...


    # TODO fix it
    # Just emit the call IR
    def visit_IRCall(self, stmt: IRCall):

        fn_var = self.cur_state.get(stmt.get_func_name())
        for arg in stmt.get_args():
            var_arg = self.cur_state.get()
        if fn_var is not None:
            for fn_obj in fn_var.get_points_to_set():
                if fn_obj.is_functional():
                    for callee in self.c.cs_manager.get_callees(self.context, fn_obj):
                        cs_callee = self.c.cs_manager.get_scope(self.context, callee)
                        self.c.add_call_edge(CSCallEdge(self.c.get_call_kind(stmt), self.context, cs_callee))

    def visit_IRClass(self, stmt: IRClass):
        cls_name = stmt.get_name()
        cls_var = self.get_var(cls_name)

        cls_obj = self.c.heap_model.get_class

        self.c.add_points_to_obj(cls_var, cls_obj)

    def visit_IRFunc(self, stmt: IRFunc):
        func_obj = ...
        ...

    def visit_IRReturn(self, stmt: IRReturn):
        val = self.get_var(stmt.value)
        if val is None:
            none_obj = self.c.heap_model.get_constant_obj(None)
            self.c.add_var_points_to_heap_obj(self.context, self.ret_var.get_var(), self.context, none_obj)
        else:
            self.c.add_pfg_edge(val, self.ret_var, FlowKind.RETURN)

    def visit_IRYield(self, stmt: IRYield):
        val = self.get_var(stmt.get)


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


        

