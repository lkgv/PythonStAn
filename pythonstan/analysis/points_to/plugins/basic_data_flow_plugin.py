from .plugin import Plugin
from ..cs_call_graph import CSCallEdge
from ..solver_interface import SolverInterface
from ..context import Context
from ..elements import *
from ..pointer_flow_graph import FlowKind
from pythonstan.graph.call_graph import CallKind
from pythonstan.utils.persistent_rb_tree import PersistentMap, PersistentSet
from pythonstan.ir import *

from typing import Iterable, Tuple, Optional, Dict, Set
import ast


ALLOW_UNKNOWN_OBJ = True


# Used in the abstract interpretation phase, store value for each pointer(var, index, ...)
class Values:
    objs: Set[Obj]

    def __init__(self, objs: Optional[Set[Obj]]):
        if objs is None:
            self.objs = set()
        else:
            self.objs = objs

    def get_objs(self) -> Set[Obj]:
        return self.objs


class State:
    mem: PersistentMap[str, Var]
    val_map: PersistentMap[Pointer, Values]

    def __init__(self, c: SolverInterface):
        self.mem = PersistentMap()
        self.val_map = PersistentMap()
        self.c = c

    def items(self) -> Iterable[Tuple[str, Var]]:
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
                    new_var = ctx, raw_var_src.copy()
                    result.mem[name] = new_var
                    self.c.add_pfg_edge(var_src, new_var, FlowKind.LOCAL_ASSIGN)
                    self.c.add_pfg_edge(var, new_var, FlowKind.LOCAL_ASSIGN)
            else:
                result.mem[name] = var
                change = True
        return change, result

    def get(self, name: str) -> Optional[Var]:
        return self.mem.get(name)

    def set(self, name: str, var: Var):
        self.mem[name] = var

    def copy(self) -> 'State':
        result = State(self.c)
        result.mem.recover(self.mem.backup())
        return result

    @staticmethod
    def search_vars(var_name: str, states: Iterable['State']) -> Set[Var]:
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
    ret_var: Var
    context: Context
    frame: PtFrame

    # do weak update just in special cases
    # do dataflow, add edges in PFG just in static case, eg. y = 3, y = x.
    # staticproperty and class property seen as instance property of class obj.


    def __init__(self, c: SolverInterface, recv_obj: Obj, frame: PtFrame):
        self.c = c

    def visit_stmts(self, stmts: Iterable[IRStatement], init_state: State, context: Context):
        self.context = context
        for stmt in stmts:
            self.visit(stmt)

    def visit_IRAssign(self, stmt: IRAssign):
        lval = self.frame.get_var_write(stmt.lval.id)

        # Copy
        if isinstance(stmt.rval, ast.Name):
            rval = self.frame.get_var_read(stmt.rval.id)
            self.c.add_pfg_edge(rval, lval, FlowKind.LOCAL_ASSIGN)

        # Assign Constant
        elif isinstance(stmt.rval, ast.Constant):
            obj = self.c.heap_model.get_constant_obj(stmt.rval.value)
            self.c.add_points_to_obj(lval, obj)

        # Assign Tuple
        elif isinstance(stmt.rval, ast.Tuple):
            ...

        elif isinstance(stmt.rval, ast.BinOp):
            # generate 2 instructions. op = a1.__add__; call(op, a1, a2);
            expr = stmt.rval
            assert isinstance(expr.left, ast.Name), "Left operand of BinOp should be Name"
            assert isinstance(expr.right, ast.Name), "Right operand of BinOp should be Name"
            operand1 = self.frame.get_var_read(expr.left.id)
            operand2 = self.frame.get_var_read(expr.right.id)
            if isinstance(expr.op, ast.Add):
                op = '__add__'
            elif isinstance(expr.op, ast.Sub):
                op = '__sub__'
            elif isinstance(expr.op, ast.Mult):
                op = '__mul__'
            elif isinstance(expr.op, ast.Div):
                op = '__div__'
            else:
                raise NotImplementedError
            func = self.frame.get_var_read('..')
            loadattr = PtLoadAttr(stmt, self.frame, func, operand1, op)
            invoke = PtInvoke(stmt, self.frame, CallKind.INSTANCE, op, [operand1, operand2])
            self.stmt_collector.add_load_attr(loadattr)
            self.stmt_collector.add_invoke(invoke)

    def visit_IRStoreAttr(self, stmt: IRStoreAttr):
        base = self.frame.get_var_read(stmt.get_obj().id)
        field = stmt.get_attr()
        rval = self.frame.get_var_read(stmt.get_rval().id)
        base.get_stmt_collector().add_store_attr(PtStoreAttr(stmt, self.frame, base, rval, field))

    def visit_IRLoadAttr(self, stmt: IRLoadAttr):
        base = self.frame.get_var_write(stmt.get_obj().id)
        field = stmt.get_attr()
        lval = self.get_var(stmt.get_lval().id)
        base.get_stmt_collector().add_load_attr(PtLoadAttr(stmt, self.frame, lval, base, field))

    def visit_IRCall(self, stmt: IRCall):
        func = self.frame.get_var_read(stmt.get_func_name())
        args = []
        for arg_name, is_starred in stmt.get_args():
            arg = self.frame.get_var_read(arg_name)
            args.append(arg)
        target = None
        if stmt.target is not None:
            target = self.frame.get_var_write(stmt.target)
        stmt = PtInvoke(stmt, self.frame, CallKind.FUNCTION, func, args, target)
        func.get_stmt_collector().add_invoke(stmt)

    def visit_IRClass(self, stmt: IRClass):
        alloc = PtAllocation(stmt, self.frame, ClassTypeObject)
        cls_name = stmt.get_name()
        cls_var = self.frame.get_var_write(cls_name)
        cls_obj = self.c.heap_model.get_cls_obj(alloc)
        self.c.add_points_to_obj(cls_var, cls_obj)

        heap_ctx = self.c.context_selector.select_heap_context(self.frame)
        bases = [self.get_var(base_name) for base_name in stmt.get_bases()]
        cls_create_invoke = PtInvoke(stmt, self.frame, CallKind.CLASS, cls_var, bases, None)
        cls_create_frame = self.c.cs_manager.get_frame(cls_create_invoke, ClassTypeObject, heap_ctx)
        call_edge = CSCallEdge(CallKind.CLASS, cls_create_invoke, cls_obj)
        self.c.add_call_edge(call_edge, cls_create_frame)
        self.c.work_list.add_edge(call_edge)


    def visit_IRFunc(self, stmt: IRFunc):
        alloc = PtAllocation(stmt, self.frame, FunctionTypeObject)
        fn_name = stmt.get_name()
        fn_var = self.frame.get_var_write(fn_name)
        fn_obj = self.c.heap_model.get_func_obj(alloc)
        self.c.add_points_to_obj(fn_var, fn_obj)

    def visit_IRReturn(self, stmt: IRReturn):
        ret_var = self.c.get_return_var(self.frame)
        if stmt.value is not None:
            val = self.frame.get_var_read(stmt.value.id)
            self.c.add_pfg_edge(val, ret_var, FlowKind.RETURN)
        else:
            none_obj = self.c.heap_model.get_constant_obj(None)
            self.c.add_points_to_obj(ret_var, none_obj)

    def visit_IRYield(self, stmt: IRYield):
        ret_var = self.c.get_yield_var(self.frame)
        if stmt.value is not None:
            val = self.frame.get_var_read(stmt.value.id)
            self.c.add_pfg_edge(val, ret_var, FlowKind.RETURN)
        else:
            none_obj = self.c.heap_model.get_constant_obj(None)
            self.c.add_points_to_obj(ret_var, none_obj)

    def visit_import(self, stmt: IRImport):
        if stmt.module is not None:
            module = self.c.get_world().scope_manager.get_module(stmt.module)

            self.c.add_call_edge(...)



class BasicDataFlowPlugin(Plugin):
    def __init__(self, c: SolverInterface):
        self.c = c

    def on_new_call_edge(self, edge: CSCallEdge, frame: PtFrame):
        callee_obj = edge.get_callee()
        stmts = self.c.scope_manager.get_ir(callee_obj.get_allocation().get_ir(), "ir")
        stmt_processor = StmtProcessor(self.c)
        stmt_processor.set_frame(frame)
        stmt_processor.visit(stmts)


    def on_new_cs_scope(self, cs_scope: CSScope):
        if isinstance(cs_scope.get_scope(), IRClass):



        

