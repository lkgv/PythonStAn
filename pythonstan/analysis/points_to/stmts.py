from abc import ABC, abstractmethod

from typing import List
from pythonstan.ir import *


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
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)


class PtCopy(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)


class PtLoadSubscr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)


class PtStoreSubscr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)


class PtLoadAttr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)


class PtStoreAttr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)


class StmtCollector:
    assigns: List[IRAssign]
    store_attrs: List[IRStoreAttr]
    load_attrs: List[IRLoadAttr]
    store_subscrs: List[IRStoreSubscr]
    load_subscrs: List[IRLoadAttr]
    allocs: List[IRCall]
    invokes: List[IRCall]

    def __init__(self):
        self.assigns = []
        self.store_attrs = []
        self.load_attrs = []
        self.store_subscrs = []
        self.load_subscrs = []
        self.allocs = []
        self.invokes = []

    def get_assigns(self) -> List[IRAssign]:
        return self.assigns

    def get_store_attrs(self) -> List[IRStoreAttr]:
        return self.store_attrs

    def get_load_attrs(self) -> List[IRLoadAttr]:
        return self.load_attrs

    def get_store_subscrs(self) -> List[IRStoreSubscr]:
        return self.store_subscrs

    def get_load_subscrs(self) -> List[IRLoadAttr]:
        return self.load_subscrs

    def get_allocs(self) -> List[IRCall]:
        return self.allocs

    def get_invokes(self) -> List[IRCall]:
        return self.invokes
