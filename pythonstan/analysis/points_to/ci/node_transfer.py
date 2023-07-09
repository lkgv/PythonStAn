import ast
from typing import Set, Optional

from .solver import SolverInterface
from .lattice.value import Value
from .lattice.state import State
from .lattice.obj import Obj
from .lattice.obj_label import ObjLabel, LabelKind
from .lattice.value_resolver import ValueResolver
from pythonstan.ir import *


class Operators:
    @staticmethod
    def bin_op(self, left: Value, right: Value, s: State) -> Value:
        ...

    @staticmethod
    def unary_op(self, op, s: State) -> Value:
        ...

class Conversion:
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
        ...

    def visit_IRImport(self, ir: IRImport):
        from pythonstan.world import World
        world : World = self.c.get_world()
        world.scope_manager.get

    def visit_IRReturn(self, ir: IRReturn):
        ...

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
            self.v.write_var(lval_id, result, s)

        elif isinstance(rval_ast, ast.UnaryOp):
            operand = self.v.get_var(rval_ast.operand.id, s)
            result = Operators.unary_op(rval_ast.op, operand, s)
            self.v.write_var(lval_id, result, s)

        elif isinstance(rval_ast, ast.Tuple):
            obj_label = ObjLabel(blk, scope, LabelKind.Tuple), ...
            tuple_val = self.c.get_state().new_tuple(obj_label, rval_ast)
            self.v.write_var(lval_id, tuple_val, s)

        elif isinstance(rval_ast, ast.List):
            obj_label = ObjLabel(blk, scope, LabelKind.List), ...
            list_val = self.c.get_state().new_list(obj_label, rval_ast)
            self.v.write_var(lval_id, list_val, s)

        elif isinstance(rval_ast, ast.Set):
            obj_label = ObjLabel(blk, scope, LabelKind.Set)
            set_val = self.c.get_state().new_set(obj_label, rval_ast)
            self.v.write_var(lval_id, set_val, s)

        elif isinstance(rval_ast, ast.Dict):
            obj_label = ObjLabel(blk, scope, LabelKind.Dict)
            set_val = self.c.get_state().new_set(obj_label, rval_ast)
            self.v.write_var(lval_id, set_val, s)

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
            self.v.write_var(lval_id, val, s)

    def visit_IRLoadAttr(self, ir: IRLoadAttr):
        s = self.c.get_state()
        obj_val = s.read_memory(ir.get_obj().id)
        if obj_val is not None and len(obj_val.get_obj_labels()) > 0:
            vals = [self.v.retrive_property(l, ir.get_attr(), s).restrict_to_not_absent() for l in obj_val.get_obj_labels()]
            val = Value.join_values(vals)
            s.write_memory(ir.get_lval().id, val)
        else:
            s.write_memory(ir.get_lval().id, Value.make_absent())

    def visit_IRStoreAttr(self, ir: IRStoreAttr):
        s = self.c.get_state()
        obj_val = s.read_memory(ir.get_obj().id)
        src_val = s.read_memory(ir.get_rval().id)
        if src_val is None:
            src_val = Value.make_absent()
        if obj_val is not None and len(obj_val.get_obj_labels()) > 0:
            for obj_label in obj_val.get_obj_labels():
                self.v.write_property(obj_label, ir.get_attr(), src_val)

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
        self.v.write_var(lval_id, subscr, self.c.get_state())

    def visit_IRStoreSubscr(self, ir: IRStoreSubscr):
        ...

    # declare a function
    def visit_IRFunc(self, ir: IRFunc):
        s = self.c.get_state()
        name = ir.get_name()


    def visit_IRClass(self, ir: IRClass):
        ...

    def visit_IRModule(self, ir: IRModule):
        ...
