from .solver_interface import SolverInterface
from pythonstan.ir import *


class NodeTransfer(IRVisitor):
    c: SolverInterface

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
        ...

    def visit_IRReturn(self, ir: IRReturn):
        ...

    def visit_IRYield(self, ir: IRYield):
        ...

    def visit_IRDel(self, ir: IRDel):
        ...

    def visit_IRAnno(self, ir: IRAnno):
        ...

    def visit_IRAssign(self, ir: IRAssign):
        ...

    def visit_IRLoadAttr(self, ir: IRLoadAttr):
        ...

    def visit_IRStoreAttr(self, ir: IRStoreAttr):
        ...

    def visit_IRLoadSubscr(self, ir: IRLoadSubscr):
        ...

    def visit_IRStoreSubscr(self, ir: IRStoreSubscr):
        ...
