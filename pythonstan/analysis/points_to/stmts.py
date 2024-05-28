from abc import ABC, abstractmethod
from typing import List

from pythonstan.ir import *
from .elements import *
from pythonstan.graph.call_graph import CallKind

__all__ = ['PtStmt', 'PtAllocation', 'PtInvoke', 'PtCopy', 'PtLoadAttr', 'PtStoreAttr', 'PtLoadSubscr', 'PtStoreSubscr',
           'StmtCollector']


class PtStmt(ABC):
    @abstractmethod
    def get_container_scope(self) -> IRScope:
        ...

    @abstractmethod
    def get_container_type(self) -> str:
        ...


class AbstractPtStmt(PtStmt):
    ir_stmt: IRStatement
    container_scope: IRScope

    @abstractmethod
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        self.ir_stmt = ir_stmt
        self.container_scope = container_scope

    def get_container_scope(self) -> IRScope:
        return self.container_scope

    def get_container_type(self) -> str:
        return self.container_scope.get_qualname()


class PtAllocation(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)

    def get_type(self) -> str:
        ...


class PtInvoke(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope, call_kind: CallKind):
        super().__init__(ir_stmt, container_scope)
        self.call_kind = call_kind

    def get_call_kind(self) -> CallKind:
        return self.call_kind



class PtCopy(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)


class PtLoadSubscr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope, ):
        super().__init__(ir_stmt, container_scope)


class PtStoreSubscr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)


class PtLoadAttr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: CSScope, lval: Var, rval: Var, field: str):
        super().__init__(ir_stmt, container_scope)
        self.lval = lval
        self.rval = rval
        self.field = field

    def get_lval(self) -> Var:
        ...

    def get_rval(self) -> Var:
        ...

    def get_field(self) -> str:
        return self.field


class PtStoreAttr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope, lval: Var, rval: Var, field: str):
        super().__init__(ir_stmt, container_scope)
        self.lval = lval
        self.rval = rval
        self.field = field

    def get_lval(self) -> Var:
        ...

    def get_rval(self) -> Var:
        ...

    def get_field(self) -> str:
        return self.field


class StmtCollector:
    copies: List[PtCopy]
    store_attrs: List[PtStoreAttr]
    load_attrs: List[PtLoadAttr]
    store_subscrs: List[PtStoreSubscr]
    load_subscrs: List[PtLoadSubscr]
    allocs: List[PtAllocation]
    invokes: List[PtInvoke]

    def __init__(self):
        self.copies = []
        self.store_attrs = []
        self.load_attrs = []
        self.store_subscrs = []
        self.load_subscrs = []
        self.allocs = []
        self.invokes = []

    def add_load_attr(self, stmt: PtLoadAttr):
        self.load_attrs.append(stmt)

    def get_copies(self) -> List[PtCopy]:
        return self.copies

    def get_store_attrs(self) -> List[PtStoreAttr]:
        return self.store_attrs

    def get_load_attrs(self) -> List[PtLoadAttr]:
        return self.load_attrs

    def get_store_subscrs(self) -> List[PtStoreSubscr]:
        return self.store_subscrs

    def get_load_subscrs(self) -> List[PtLoadSubscr]:
        return self.load_subscrs

    def get_allocs(self) -> List[PtAllocation]:
        return self.allocs

    def get_invokes(self) -> List[PtInvoke]:
        return self.invokes
