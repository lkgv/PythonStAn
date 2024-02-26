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
    
    def __eq__(self, other):
        if self == other:
            return True
        if other is None or not isinstance(other, MockObj):
            return False
        return self.desc == other.desc and self.alloc == other.alloc and self.type == other.type


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
    def get_mock_obj(self, desc: str, alloc: AbstractPtStmt, type: str, scope: IRScope, is_callable: bool) -> Obj:
        ...


class AbstractHeapModel(HeapModel):
    constant_objs: Dict[Literals, Obj]
    new_objs: Dict[PtAllocation, NewObj]
    merged_objs: Dict[str, MergedObj]
    mock_objs: Dict[MockObj, MockObj]

    @abstractmethod
    def __init__(self, options):
        self.constant_objs = {}
        self.new_objs = {}
        self.merged_objs = {}
        self.mock_objs = {}
        self.options = options

    def get_obj(self, alloc_site: PtAllocation) -> Obj:
        return self.do_get_obj(alloc_site)

    def get_merged_obj(self, alloc_site: PtAllocation) -> MergedObj:
        t = alloc_site.get_type()
        if t not in self.merged_objs:
            self.merged_objs[t] = MergedObj(f"<Merged {t}>", t)
        merged_obj = self.merged_objs[t]
        merged_obj.add_represented_obj(self.get_new_obj(alloc_site))
        return merged_obj

    def get_new_obj(self, alloc_site: PtAllocation) -> NewObj:
        if alloc_site not in self.new_objs:
            self.new_objs[alloc_site] = NewObj(alloc_site)
        return self.new_objs[alloc_site]

    def get_constant_obj(self, value: Literals) -> Obj:
        obj = self.do_get_constant_obj(value)
        return obj

    def get_mock_obj(self, desc: str, alloc: AbstractPtStmt, type: str, scope: IRScope, is_callable: bool) -> Obj:
        mock_obj = MockObj(desc, alloc, type, scope, is_callable)
        if mock_obj not in self.mock_objs:
            self.mock_objs[mock_obj] = mock_obj
        return self.mock_objs[mock_obj]

    def do_get_constant_obj(self, value: Literals) -> Obj:
        if value not in self.constant_objs:
            self.constant_objs[value] = ConstantObj(value)
        return self.constant_objs[value]

    def is_str_obj(self, obj: Obj) -> bool:
        return isinstance(obj.get_allocation(), str)

    @abstractmethod
    def do_get_obj(self, alloc_site: PtAllocation) -> Obj:
        ...


class AllocationSiteBasedModel(AbstractHeapModel):

    def __init__(self, options, obj_groups):
        super().__init__(options)

    def do_get_obj(self, alloc_site: PtAllocation) -> Obj:
        return self.get_new_obj(alloc_site)
