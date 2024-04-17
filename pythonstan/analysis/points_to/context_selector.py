from abc import ABC, abstractmethod

from .context import Context
from .elements import CSCallSite, CSObj, CSScope
from .heap_model import Obj
from pythonstan.ir import IRScope


class ContextSelector(ABC):
    @abstractmethod
    def get_empty_context(self) -> Context:
        ...

    @abstractmethod
    def select_static_context(self, callsite: CSCallSite, callee: IRScope) -> Context:
        ...

    @abstractmethod
    def select_instance_context(self, callsite: CSCallSite, recv: CSObj, callee: IRScope) -> Context:
        ...

    @abstractmethod
    def select_heap_context(self, scope: CSScope, obj: Obj) -> Context:
        ...
