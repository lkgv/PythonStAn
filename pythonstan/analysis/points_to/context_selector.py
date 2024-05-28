from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from .context import Context, TrieContextHelper
from .elements import CSCallSite, CSObj, CSScope
from .heap_model import Obj, NewObj
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

T = TypeVar('T')


class AbstractContextSelector(Generic[T], ContextSelector):
    context_helper = TrieContextHelper[T]()

    def get_empty_context(self) -> Context[T]:
        return self.context_helper.get_empty_context()

    def select_heap_context(self, scope: CSScope, obj: Obj) -> Context[T]:
        if isinstance(obj, NewObj):
            return self.select_new_obj_context(scope, obj)
        else:
            return self.get_empty_context()

    @abstractmethod
    def select_new_obj_context(self, scope: CSScope, obj: NewObj) -> Context[T]:
        ...


class KLimitingSelector(Generic[T], AbstractContextSelector[T], ABC):
    _limit: int
    _h_limit: int

    def __init__(self, k: int, hk: int):
        self._limit = k
        self._h_limit = hk

    def select_new_obj_context(self, scope: CSScope, obj: NewObj) -> Context[T]:
        return self.context_helper.make_last_k(scope.get_context(), self._h_limit)


class KObjSelector(KLimitingSelector[Obj]):
    def select_static_context(self, cs: CSCallSite, callee: IRScope) -> Context[Obj]:
        return cs.get_context()

    def select_instance_context(self, cs: CSCallSite, recv: CSObj, callee: IRScope) -> Context[Obj]:
        return self.context_helper.append(recv.get_context(), recv.get_obj(), self._limit)
