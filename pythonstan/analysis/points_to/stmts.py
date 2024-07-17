from abc import ABC, abstractmethod
from typing import List, Optional

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

    def get_ir(self) -> IRStatement:
        return self.ir_stmt


class PtAllocation(AbstractPtStmt):
    _type: CSObj

    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope, type_obj: CSObj):
        super().__init__(ir_stmt, container_scope)
        from .heap_model import Type
        assert isinstance(type_obj.get_obj(), Type), "type of the type cs_obj should be Type"
        self._type = type_obj

    def get_type(self) -> CSObj:
        return self._type

    def __eq__(self, other):
        return isinstance(other, PtAllocation) and other.get_ir() == self.get_ir()


class PtInvoke(AbstractPtStmt):
    _call_kind: CallKind
    _func: CSVar
    _args: List[CSVar]

    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope, call_kind: CallKind,
                 func: CSVar, args: List[CSVar]):
        super().__init__(ir_stmt, container_scope)
        self._call_kind = call_kind
        self._func = func
        self._args = args

    def get_call_kind(self) -> CallKind:
        return self._call_kind

    def get_func(self) -> CSVar:
        return self._func

    def get_args(self) -> List[CSVar]:
        return self._args


class PtLoadSubscr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)


class PtStoreSubscr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: IRScope):
        super().__init__(ir_stmt, container_scope)


class PtLoadAttr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: CSScope, lval: CSVar, rval: CSVar, field: str):
        super().__init__(ir_stmt, container_scope.get_scope())
        self.lval = lval
        self.rval = rval
        self.field = field

    def get_lval(self) -> CSVar:
        return self.lval

    def get_rval(self) -> CSVar:
        return self.rval

    def get_field(self) -> str:
        return self.field


class PtStoreAttr(AbstractPtStmt):
    def __init__(self, ir_stmt: IRStatement, container_scope: CSScope, lval: CSVar, rval: CSVar, field: str):
        super().__init__(ir_stmt, container_scope.get_scope())
        self.lval = lval
        self.rval = rval
        self.field = field

    def get_lval(self) -> CSVar:
        return self.lval

    def get_rval(self) -> CSVar:
        return self.rval

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

    def add_store_attr(self, stmt: PtStoreAttr):
        self.store_attrs.append(stmt)

    def add_invoke(self, stmt: PtInvoke):
        self.invokes.append(stmt)

    def get_store_attrs(self, var: Optional[CSVar] = None) -> List[PtStoreAttr]:
        if var is not None:
            return [stmt for stmt in self.store_attrs if stmt.get_rval() == var]
        else:
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
