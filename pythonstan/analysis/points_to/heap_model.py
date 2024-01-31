from typing import Union, Set, Dict, Optional
from abc import ABC, abstractmethod

from pythonstan.ir import IRCall, IRScope


class Obj(ABC):
    idx: Optional[int]

    def set_idx(self, idx: int):
        assert self.idx is not None, "idx already set"
        assert self.idx >= 0, f"idx must be 0 or positive number, given: {idx}"
        self.idx = idx

    def get_idx(self) -> int:
        assert self.idx is not None, "idx has not been set!"
        return self.idx

    @abstractmethod
    def get_type(self) -> str:
        ...

    @abstractmethod
    def get_allocation(self) -> Optional[IRCall]:
        ...

    @abstractmethod
    def get_container_scope(self) -> Optional[IRScope]:
        ...

    @abstractmethod
    def get_container_type(self) -> str:
        ...


Literals = Union[int, float, str, None]

class ConstantObj(Obj):
    value: Literals

    def __init__(self, value: Literals):
        self.value = value

    def get_type(self) -> str:
        return str(type(self.value))

    def get_allocation(self):
        return None

    def get_container_type(self):
        return None

    def __str__(self):
        return f"ConstantObj<{self.get_type()}: {self.value}>"


class NewObj(Obj):
    alloc_site: IRCall

    def __init__(self, alloc_site: IRCall):
        self.alloc_site = alloc_site

    def get_type(self) -> str:
        return ...



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
    constant_objs: Dict[Literals, Obj]


    @abstractmethod
    def __init__(self):
        ...


