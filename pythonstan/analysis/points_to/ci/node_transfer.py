import ast
from typing import Set

from .solver import SolverInterface
from .lattice.value import Value
from .lattice.state import State
from .lattice.obj import Obj
from .lattice.obj_label import ObjLabel, LabelKind
from pythonstan.ir import *


class VarHelper:
    def get_var(self, vname: str, s: State) -> Value:


    def write_var(self, vname: str, v: Value, s: State):
        ...

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
    v: VarHelper

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
            rval = self.v.get_var(rval_ast.id, s)
            self.v.write_var(lval_id, rval, s)

        elif isinstance(rval_ast, ast.BinOp):
            left_val = self.v.get_var(rval_ast.left.id, s)
            right_val = self.v.get_var(rval_ast.right.id, s)
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
        ...

    def visit_IRStoreAttr(self, ir: IRStoreAttr):
        ...

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

    def visit_IRFunc(self, ir: IRFunc):
        ...

    def visit_IRClass(self, ir: IRClass):
        ...

    def visit_IRModule(self, ir: IRModule):
        ...
