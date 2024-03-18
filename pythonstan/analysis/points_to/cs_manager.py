from abc import ABC
from typing import Collection, Dict

from .context import Context, CSVar, CSObj, CSCallSite, CSScope
from .elements import Var, StaticField, InstanceField, ArrayIndex
from .heap_model import Obj
from .stmts import PtInvoke
from pythonstan.ir import IRScope


class CSManager(ABC):
    def get_var(self, ctx: Context, var: Var) -> CSVar:
        ...

    def get_obj(self, ctx: Context, obj: Obj) -> CSObj:
        ...

    def get_callsite(self, ctx: Context, call_site: PtInvoke) -> CSCallSite:
        ...

    def get_scope(self, ctx: Context, scope: IRScope) -> CSScope:
        ...

    # may be replaced by instance_field for the class object.
    def get_static_field(self, ctx: Context, field: str) -> StaticField:
        ...

    def get_instance_field(self, cs_obj: CSObj, field: str) -> InstanceField:
        ...

    def get_array_index(self, ctx: Context, array: Obj) -> ArrayIndex:
        ...

    def get_vars(self) -> Collection[Var]:
        ...

    def get_cs_vars_of(self, var: Var) -> Collection[CSVar]:
        ...

    def get_contexs_from_var(self, var: Var) -> Collection[Context]:
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

    def get_cs_var(self, ):


class MapBasedCSManager(CSManager):
    ptr_manager =
