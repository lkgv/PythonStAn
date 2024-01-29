from typing import Union, Set
from abc import ABC, abstractmethod

from pythonstan.ir import IRCall
from .elements import Obj


class HeapModel(ABC):
    @abstractmethod
    def get_obj(self, alloc_site) -> Obj:
        ...

    @abstractmethod
    def get_constant_obj(self, value: Union[str, int, float]) -> Obj:
        ...

    @abstractmethod
    def is_str_obj(self, obj: Obj) -> bool:
        ...

    @abstractmethod
    def get_mock_obj(self, alloc: IRCall) -> Obj:
        ...

    @abstractmethod
    def get_objs(self) -> Set[Obj]:
        ...


class AbstractHeapModel(HeapModel):

    @abstractmethod
    def __init__(self):
        ...


