from abc import ABC, abstractmethod
from typing import Collection, Dict, Union, List

from .context import Context
from .elements import *
from pythonstan.ir import IRScope


class CSManager(ABC):

    @abstractmethod
    def get_callsite(self, ctx: Context, call_site: PtInvoke) -> PtCallSite:
        ...

    @abstractmethod
    def get_frame(self, call_site: PtInvoke, code_obj: Obj, ctx: Context) -> PtFrame:
        ...



class PointerManager:
    vars: Dict
    static_fields: Dict
    instance_fields: Dict
    array_indexes: Dict
    counter: int

    def __init__(self):
        self.vars = {}
        self.static_fields = {}
        self.instance_fields = {}
        self.array_indexes = {}
        self.counter = 0

    def get_cs_var(self, context: Context, name: str) -> Var:
        ...


class MapBasedCSManager(CSManager):
    ptr_manager = PointerManager()

    def get_var(self, ctx: Context, var: Var) -> Union[Var, ClassField]:
        # if context belongs to a class then return the Class Instance, and add it into class manager
        ...

    def get_obj(self, ctx: Context, obj: Obj) -> CSObj:
        ...

    def get_callsite(self, ctx: Context, call_site: PtInvoke) -> CSCallSite:
        ...

    def get_scope(self, ctx: Context, scope: IRScope) -> CSScope:
        ...

    def get_field(self, cs_obj: CSObj, field: str, allow_none: bool = True) -> Optional[CSVar]:
        type_obj = cs_obj.get_obj().get_type()
        if isinstance(type_obj, ClassObj):
            field = self.do_get_field(cs_obj, field)
            if field is None:
                field = self.do_get_field(type_obj, field)

            self.get_obj
        elif isinstance(type_obj, LiteralObj):
            ...

    def do_get_field(self, cs_obj: CSObj, field: str) -> ...:
        ...

    def get_parents(self, cs_obj: CSObj) -> List[CSObj]:
        ...

    def get_array_index(self, ctx: Context, array: Obj) -> ArrayIndex:
        ...

    def get_vars(self) -> Collection[Var]:
        ...

    def get_cs_vars_of(self, var: Var) -> Collection[CSVar]:
        ...

    def get_contexs_from_var(self, var: Var) -> Collection[Context]:
        ...
