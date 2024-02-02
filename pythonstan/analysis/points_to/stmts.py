from abc import ABC, abstractmethod

from typing import List
from pythonstan.ir import *


class AbstractPtStmt(ABC):
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
    def get_type(self) -> str:
        ...



class PtInvoke(AbstractPtStmt):
    ...

class PtCopy(AbstractPtStmt):
    ...

class PtLoadSubscr(AbstractPtStmt):
    ...

class PtStoreSubscr(AbstractPtStmt):
    ...

class PtLoadAttr(AbstractPtStmt):
    ...

class PtStoreAttr(AbstractPtStmt):
    ...


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
