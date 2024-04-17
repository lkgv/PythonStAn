from .plugin import Plugin
from ..cs_call_graph import CSCallEdge
from ..cs_manager import CSScope
from ..solver_interface import SolverInterface
from ..context import CSVar, CSObj, CSCallSite, Context
from ..elements import Var,
from ..pointer_flow_graph import FlowKind
from ..heap_model import Obj
from pythonstan.graph.call_graph import CallKind
from pythonstan.utils.persistent_rb_tree import PersistentMap, PersistentSet
from pythonstan.ir import *

from typing import Iterable, Tuple, Optional, Dict
import ast

ALLOW_UNKNOWN_OBJ = True


class State:
    mem: PersistentMap[str, CSVar]

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

    def copy(self) -> 'State':
        result = State(self.c)
        result.mem.recover(self.mem.backup())
        return result


class StmtProcessor(IRVisitor):
    cur_state: State
    state_before: Dict[IRStatement, State]
    state_after: Dict[IRStatement, State]
    context: Context


    def __init__(self, c: SolverInterface):
        self.c = c
        self.state_before = []
        self.state_after = []

    def visit_stmts(self, stmts: Iterable[IRStatement], init_state: State, context: Context):
        self.cur_state = init_state
        self.context = context
        for stmt in stmts:
            self.state_before[stmt] = self.cur_state.copy()
            self.visit(stmt)
            self.state_after[stmt] = self.cur_state.copy()


    def visit_assign(self, stmt: IRAssign):
        lval = self.c.cs_manager.get_var(self.context, Var(stmt.lval.id))
        if isinstance(stmt.rval, ast.Name):
            rval = self.cur_state.get(stmt.rval.id)
            if rval is None:
                if ALLOW_UNKNOWN_OBJ:
                    unknown_obj = self.c.heap_model.get_unknown_obj()
                else:
                    raise ValueError(f"Unresolved variable {stmt.rval.id}")
            else:
                self.c.add_pfg_edge(rval, lval, FlowKind.LOCAL_ASSIGN)



class BasicDataFlowPlugin(Plugin):
    def __init__(self, c: SolverInterface):
        self.c = c
        self.stmt_processor = StmtProcessor(c)

    def on_new_call_edge(self, edge: CSCallEdge):
        callee_ctx, callee_scope = edge.get_callee()
        init_state = State(self.c, callee_ctx)
        for var in callee_scope.get_vars():
            init_state.mem[var.get_name()] = callee_ctx, var
        stmts = self.c.scope_manager.get_ir(callee_scope, "ir")
        self.stmt_processor.visit(stmts)


        

