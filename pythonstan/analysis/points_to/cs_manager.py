from abc import ABC
from typing import Collection

from .context import Context, CSVar, CSObj
from .elements import Var, StaticField, InstanceField, ArrayIndex
from .heap_model import Obj
from .stmts import PtInvoke
from pythonstan.ir import IRScope

class CSManager(ABC):
    def get_var(self, ctx: Context, var: Var) -> Context:
        ...

    def get_obj(self, ctx: Context, obj: Obj) -> Context:
        ...

    def get_callsite(self, ctx: Context, call_site: PtInvoke) -> Context:
        ...

    def get_scope(self, ctx: Context, scope: IRScope) -> Context:
        ...

    def get_static_field(self, ctx: Context, static_field: StaticField) -> Context:
        ...

    def get_instance_field(self, cs_obj: CSObj, instance_field: InstanceField) -> Context:
        ...

    def get_array_index(self, ctx: Context, array: Obj) -> ArrayIndex:
        ...

    def get_vars(self) -> Collection[Var]:
        ...

    def get_contexs_from_var(self, var: Var) -> Collection[Context]:
        ...
