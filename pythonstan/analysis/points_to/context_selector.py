from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from .context import Context, TrieContextHelper
from .elements import *


class ContextSelector(ABC):
    @abstractmethod
    def get_empty_context(self) -> Context:
        ...

    @abstractmethod
    def select_static_context(self, callsite: PtInvoke, callee: FunctionObj) -> Context:
        ...

    @abstractmethod
    def select_instance_context(self, callsite: PtInvoke, recv: Obj, callee: MethodObj) -> Context:
        ...

    @abstractmethod
    def select_heap_context(self, frame: PtFrame) -> Context:
        ...

T = TypeVar('T')


class AbstractContextSelector(Generic[T], ContextSelector):
    context_helper = TrieContextHelper[T]()

    def get_empty_context(self) -> Context[T]:
        return self.context_helper.get_empty_context()

    def select_heap_context(self, frame: PtFrame) -> Context[T]:
        return self.select_new_obj_context(frame)

    @abstractmethod
    def select_new_obj_context(self, frame: PtFrame) -> Context[T]:
        ...


class KLimitingSelector(Generic[T], AbstractContextSelector[T], ABC):
    _limit: int
    _h_limit: int

    def __init__(self, k: int, hk: int):
        self._limit = k
        self._h_limit = hk

    def select_new_obj_context(self, frame: PtFrame) -> Context[T]:
        return self.context_helper.make_last_k(frame.get_context(), self._h_limit)


class KObjSelector(KLimitingSelector[Obj]):
    def select_static_context(self, callsite: PtInvoke, callee: FunctionObj) -> Context:
        return callsite.get_context()

    def select_instance_context(self, callsite: PtInvoke, recv: Obj, callee: FunctionObj) -> Context:
        return self.context_helper.append(recv.get_context(), recv, self._limit)


class KCallSelector(KLimitingSelector[PtInvoke]):
    def select_static_context(self, callsite: PtInvoke, callee: FunctionObj) -> Context:
        return self.context_helper.append(callsite.get_context(), callsite, self._limit)

    def select_instance_context(self, callsite: PtInvoke, recv: Obj, callee: FunctionObj) -> Context:
        return self.context_helper.append(callsite.get_context(), callsite, self._limit)
