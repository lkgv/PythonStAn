from typing import Union, Set, Dict, Optional
from abc import ABC, abstractmethod

from pythonstan.ir import IRScope
from .stmts import PtAllocation, AbstractPtStmt


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
    def get_allocation(self) -> Optional[PtAllocation]:
        ...

    @abstractmethod
    def get_container_scope(self) -> Optional[IRScope]:
        ...

    @abstractmethod
    def get_container_type(self) -> str:
        ...

    @abstractmethod
    def __str__(self):
        ...

    def __repr__(self):
        return str(self)


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

    def get_container_scope(self):
        return None

    def __str__(self):
        return f"ConstantObj<{self.get_type()}: {self.value}>"


class NewObj(Obj):
    alloc_site: PtAllocation

    def __init__(self, alloc_site: PtAllocation):
        self.alloc_site = alloc_site

    def get_type(self) -> str:
        return self.alloc_site.get_type()

    def get_allocation(self) -> Optional[PtAllocation]:
        return self.alloc_site

    def get_container_scope(self) -> Optional[IRScope]:
        return self.alloc_site.get_container_scope()

    def get_container_type(self) -> str:
        return self.alloc_site.get_container_type()

    def __str__(self):
        return f"NewObj<{...}>"


class MergedObj(Obj):
    name: str
    type: str
    representative: Optional[Obj]
    represented_objs: Set[Obj]

    def __init__(self, name: str, type: str):
        self.name = name
        self.type = type
        self.represented_objs = set()
        self.representative = None

    def set_representative(self, obj: Obj):
        if self.representative is None:
            self.representative = obj

    def add_represented_obj(self, obj: Obj):
        self.set_representative(obj)
        self.represented_objs.add(obj)

    def get_allocation(self) -> Set[Obj]:
        return self.represented_objs

    def get_type(self) -> str:
        return self.type

    def get_container_scope(self) -> Optional[IRScope]:
        if self.representative is not None:
            return self.representative.get_container_scope()
        else:
            return None

    def get_container_type(self) -> str:
        if self.representative is not None:
            return self.representative.get_container_type()
        else:
            return self.type

    def __str__(self):
        return f"MergedObj<{self.name}: {self.type}>"


class MockObj(Obj):
    desc: str
    alloc: AbstractPtStmt
    type: str
    scope: IRScope
    is_callable: bool

    def __init__(self, desc: str, alloc: AbstractPtStmt, type: str, scope: IRScope, is_callable: bool):
        self.desc = desc
        self.alloc = alloc
        self.type = type
        self.scope = scope
        self.is_callable = is_callable

    ...



class HeapModel(ABC):
    @abstractmethod
    def get_obj(self, alloc_site: PtAllocation) -> Obj:
        ...

    @abstractmethod
    def get_constant_obj(self, value: Literals) -> Obj:
        ...

    @abstractmethod
    def is_str_obj(self, obj: Obj) -> bool:
        ...

    @abstractmethod
    def get_mock_obj(self, alloc: PtAllocation) -> Obj:
        ...

    @abstractmethod
    def get_objs(self) -> Set[Obj]:
        ...



class AbstractHeapModel(HeapModel):
    constant_objs: Dict[Literals, Obj]


    @abstractmethod
    def __init__(self):
        ...

    def get_obj(self, alloc_site: PtAllocation) -> Obj:
        type = alloc_site.get_type()
        if


