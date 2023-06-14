import ast
from abc import ABC, abstractmethod

from ..graph import Edge, Node
from .base_block import BaseBlock

__all__ = ["CFGEdge", "NormalEdge", "IfEdge", "CallEdge",
           "WhileEdge", "WhileElseEdge",
           "WithEdge", "WithEndEdge",
           "ExceptionEdge", "ExceptionEndEdge",
           "FinallyEdge", "FinallyEndEdge"]


class CFGEdge(Edge):
    src: BaseBlock
    tgt: BaseBlock

    @abstractmethod
    def __init__(self, src, tgt):
        self.src = src
        self.tgt = tgt

    def set_src(self, node: Node):
        assert isinstance(node, BaseBlock)
        self.src = node

    def set_tgt(self, node: Node):
        assert isinstance(node, BaseBlock)
        self.tgt = node

    def get_src(self) -> BaseBlock:
        return self.src

    def get_tgt(self) -> BaseBlock:
        return self.tgt

    def get_name(self) -> str:
        return ""


class NormalEdge(CFGEdge):
    def __init__(self, src, tgt):
        super().__init__(src, tgt)


class IfEdge(CFGEdge):
    test: ast.expr
    value: bool

    def __init__(self, src, tgt, test, value):
        super().__init__(src, tgt)
        self.test = test
        self.value = value

    def get_name(self) -> str:
        return f"if_{ast.unparse(self.test)}_is_{self.value}"


class CallEdge(CFGEdge):
    def __init__(self, src, tgt):
        super().__init__(src, tgt)

    def get_name(self) -> str:
        return 'call'


class WhileEdge(CFGEdge):
    def __init__(self, start, end):
        super().__init__(start, end)

    def get_name(self) -> str:
        return "while"


class WhileElseEdge(CFGEdge):
    def __init__(self, start, end):
        super().__init__(start, end)

    def get_name(self) -> str:
        return "while_else"


class WithEdge(CFGEdge):
    def __init__(self, start, end, var):
        super().__init__(start, end)
        self.var = var

    def get_name(self) -> str:
        return "with"


class WithEndEdge(CFGEdge):
    def __init__(self, start, end, var):
        super().__init__(start, end)
        self.var = var

    def get_name(self) -> str:
        return "with_end"


class ExceptionEdge(CFGEdge):
    def __init__(self, start, end, e):
        super().__init__(start, end)
        self.e = e

    def get_name(self) -> str:
        return ast.unparse(self.e)


class ExceptionEndEdge(CFGEdge):
    def __init__(self, start, end, e):
        super().__init__(start, end)
        self.e = e

    def get_name(self) -> str:
        return "end: " + ast.unparse(self.e)


class FinallyEdge(CFGEdge):
    def __init__(self, start, end, stmt):
        super().__init__(start, end)
        self.stmt = stmt

    def get_name(self) -> str:
        return "finally"


class FinallyEndEdge(CFGEdge):
    def __init__(self, start, end, stmt):
        super().__init__(start, end)
        self.stmt = stmt

    def get_name(self) -> str:
        return "finally_end"
