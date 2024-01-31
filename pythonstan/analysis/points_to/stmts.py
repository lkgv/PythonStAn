from abc import ABC, abstractmethod

from typing import List
from pythonstan.ir import *


class AbstractPtStmt(ABC):
    ir_stmt: IRStatement

    @abstractmethod
    def __init__(self, ir_stmt: IRStatement):
        self.ir_stmt = ir_stmt


class PtAllocation(AbstractPtStmt):
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
