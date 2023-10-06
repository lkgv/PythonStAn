import ast
from typing import Set, Optional, Tuple

from .operators import Operators
from .lattice.context import Context
from .lattice.scope_chain import Scope
from .lattice.value import Value
from .lattice.state import State
from .lattice.obj import Obj
from .lattice.obj_label import ObjLabel, LabelKind
from .lattice.value_resolver import ValueResolver
from pythonstan.ir import *
from pythonstan.graph.cfg import BaseBlock
from .solver_interface import SolverInterface


class Conversion:
    from .solver_interface import SolverInterface
    @classmethod
    def to_obj_labels(cls, v: Value, c: SolverInterface) -> Set[ObjLabel]:
        res = {l for l in v.get_obj_labels()}
        s = c.get_state()
        if not v.is_not_int():
            v_int = v.restrict_to_int()
            l_int = cls.make_label(c.get_node(), LabelKind.Int, v_int, c)
            s.new_obj(l_int, )
            s.write


    @classmethod
    def to_obj(cls, v: Value, c: SolverInterface) -> Value:
        ...

    @classmethod
    def make_label(cls, blk: BaseBlock, kind: LabelKind, val: Value, c: SolverInterface):
        ...




class NodeTransfer(IRVisitor):
    c: SolverInterface
    v: ValueResolver

    def __init__(self, c: SolverInterface, v: Optional[ValueResolver] = None):
        self.c = c
        self.v = v if v is not None else ValueResolver()

    def visit_IRAstStmt(self, ir: IRAstStmt):
        ...

    def visit_Phi(self, ir: Phi):
        ...

    def visit_Goto(self, ir: Goto):
        ...

    def visit_JumpIfTrue(self, ir: JumpIfTrue):
        ...

    def visit_JumpIfFalse(self, ir: JumpIfFalse):
        ...

    def visit_IRCall(self, ir: IRCall):
        state = self.c.get_state()
        fname = ir.get_func_name()
        fval = self.v.get_var(fname, state)
        if fval.is_maybe_obj():
            for l in fval.get_obj_labels():
                if l.get_kind() == LabelKind.Function:
                    ir_scope = l.get_scope()
                    assert isinstance(ir_scope, IRFunc), "Scope of a function label should be IRFunc"
                    if ir_scope.is_instance_method:
                        self_val = Value.make_obj([l.get_host_obj_label()])

                        # call method
                        ...

                    if ir_scope.is_class_method:
                        cls_val = Value.make_obj([l.get_host_obj_label()])

                        # call method
                        ...

                    else:
                        # call method
                        ...
                    ...
                elif l.get_kind() == LabelKind.Class:
                    # obj = new_obj
                    init_fn_val = self.v.retrive_property(l, "__init__", state)
                    if init_fn_val.is_maybe_obj():
                        for init_fn_label in init_fn_val.get_obj_labels():
                            if init_fn_label.get_kind() == LabelKind.Function:
                                ...
                                # call init function to the new_obj


    def visit_IRImport(self, ir: IRImport):
        from pythonstan.world import World
        world : World = self.c.get_world()
        mod = world.import_manager.get_import(self.c.get_module, ir)
        mod_cfg = world.scope_manager.get_ir(mod, "CFG")
        # go through mod_cfg
        mod_state = self.c.get_state().clone()
        ec = self.c.get_analysis().new_execution_context()
        mod_state.set_execution_context(ec)

        # mod_state =


    def visit_IRReturn(self, ir: IRReturn):
        self.transfer_return(...)

    def visit_IRYield(self, ir: IRYield):
        ...

    def visit_IRDel(self, ir: IRDel):
        ...

    def visit_IRAnno(self, ir: IRAnno):
        ...

    def visit_IRAssign(self, ir: IRAssign):
        s = self.c.get_state()
        lval_id = ir.get_lval().id
        rval_ast = ir.get_rval()
        blk = self.c.get_node()
        scope = self.c.get_scope()
        if isinstance(rval_ast, ast.Name):
            rval = s.get_memmory(rval_ast.id)
            if rval is not None:
                rval = Value.make_absent()
            s.write_memory(lval_id, rval)

        elif isinstance(rval_ast, ast.BinOp):
            left_val = s.get_memory(rval_ast.left.id)
            right_val = s.get_memory(rval_ast.right.id)
            result = Operators.bin_op(rval_ast.op, left_val, right_val, s)
            self.v.set_var(lval_id, result, s)

        elif isinstance(rval_ast, ast.UnaryOp):
            operand = self.v.get_var(rval_ast.operand.id, s)
            result = Operators.unary_op(rval_ast.op, operand, s)
            self.v.set_var(lval_id, result, s)

        elif isinstance(rval_ast, ast.Tuple):
            obj_label = ObjLabel(blk, scope, LabelKind.Tuple), ...
            tuple_val = self.c.get_state().new_tuple(obj_label, rval_ast)
            self.v.set_var(lval_id, tuple_val, s)

        elif isinstance(rval_ast, ast.List):
            obj_label = ObjLabel(blk, scope, LabelKind.List), ...
            list_val = self.c.get_state().new_list(obj_label, rval_ast)
            self.v.set_var(lval_id, list_val, s)

        elif isinstance(rval_ast, ast.Set):
            obj_label = ObjLabel(blk, scope, LabelKind.Set)
            set_val = self.c.get_state().new_set(obj_label, rval_ast)
            self.v.set_var(lval_id, set_val, s)

        elif isinstance(rval_ast, ast.Dict):
            obj_label = ObjLabel(blk, scope, LabelKind.Dict)
            set_val = self.c.get_state().new_set(obj_label, rval_ast)
            self.v.set_var(lval_id, set_val, s)

        elif isinstance(rval_ast, ast.Constant):
            if isinstance(rval_ast.value, int):
                val = Value.make_int(rval_ast.value)
            elif isinstance(rval_ast.value, float):
                val = Value.make_float(rval_ast.value)
            elif isinstance(rval_ast.value, str):
                val = Value.make_str(rval_ast.value)
            elif isinstance(rval_ast.value, bool):
                val = Value.make_bool(rval_ast.value)
            elif rval_ast.value is None:
                val = Value.make_none()
            else:
                raise ValueError(f"Not supported constant! <{ast.unparse(rval_ast)}>")
            self.v.set_var(lval_id, val, s)

    def visit_IRLoadAttr(self, ir: IRLoadAttr):
        s = self.c.get_state()
        obj_val = self.v.get_var(ir.get_obj().id, s)
        if obj_val is not None and len(obj_val.get_obj_labels()) > 0:
            vals = [self.v.retrive_property(l, ir.get_attr(), s).restrict_to_not_absent() for l in obj_val.get_obj_labels()]
            val = Value.join_values(vals)
            self.v.set_var(ir.get_lval().id, val, s)
        else:
            self.v.set_var(ir.get_lval().id, Value.make_absent(), s)

    def visit_IRStoreAttr(self, ir: IRStoreAttr):
        s = self.c.get_state()
        obj_val = self.v.get_var(ir.get_obj().id, s)
        src_val = self.v.get_var(ir.get_rval().id, s)
        if src_val is None:
            src_val = Value.make_absent()
        if obj_val is not None:
            for obj_label in obj_val.get_obj_labels():
                self.v.write_property(obj_label, ir.get_attr(), src_val, s)

    def visit_IRLoadSubscr(self, ir: IRLoadSubscr):
        lval_id = ir.get_lval().id
        base_val = self.v.get_var(ir.get_obj().id, self.c.get_state())
        if ir.has_slice():
            lower = ir.get_slice().lower
            upper = ir.get_slice().upper
            step = ir.get_slice().step

            subscr = ...
        else:
            slice = self.v.get_var(ir.get_slice().id, self.c.get_state())
            subscr = ...
        self.v.set_var(lval_id, subscr, self.c.get_state())

    def visit_IRStoreSubscr(self, ir: IRStoreSubscr):
        ...

    # declare a function
    def visit_IRFunc(self, ir: IRFunc):
        s = self.c.get_state()
        blk = s.get_block()
        fn = self.init_function(ir, s.get_execution_context().scope_chain, blk, s, self.c)
        fnval = self.get_initialized_functions(fn, ir, blk, self.c)
        self.v.set_var(ir.get_name(), fnval, s)

    def get_initialized_functions(self, fn: ObjLabel, scope: IRScope, blk, c: SolverInterface) -> Value:
        return Value.make_obj([fn])

    def init_function(self, ir: IRFunc, scope, blk, state: State, c: SolverInterface) -> ObjLabel:
        ctx = c.get_analysis().get_context_sensitive_strategy().make_function_heap_context(ir_func, c)
        func_label = ObjLabel.make_scope_with_ctx(ir, ctx)
        state.new_obj(func_label)
        fn_scope = state.get_execution_context().scope_chain.clone()
        fn_scope.add_scope(Scope(ir))
        state.write_obj_scope(func_label, fn_scope)
        self.v.write_property(func_label, "__name__", Value.make_str(ir.get_name()), state)
        return func_label

    def visit_IRClass(self, ir: IRClass):
        ...

    def visit_IRImport(self, ir: IRImport):
        # call and back the specific values (in the leave_function method)
        ...


    def transfer_return(self, blk: BaseBlock, state: State, return_val: Value, caller_bc: Tuple[BaseBlock, Context], edge_ctx: Context):

        ... # leave_function(

