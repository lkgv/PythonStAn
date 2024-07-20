from typing import Union, Set, Dict, List, Optional, Any, Generic, TypeVar
from abc import ABC, abstractmethod

from .stmts import *
from pythonstan.ir import IRScope, IRCall, IRClass, IRFunc
from pythonstan.utils.common import Singleton

__all__ = ['Obj', 'InstanceObj', 'ClassObj', 'FunctionObj', 'UnknownObj', 'NewObj',
           'MergedObj', 'MockObj', 'HeapModel', 'AllocationSiteBasedModel',
           "ClassTypeObject", "FuncTypeObj"]


class UnknownObj(Obj):
    def __init__(self):
        ...

    def get_type(self) -> str:
        return "Unknown"

    def get_allocation(self):
        return None

    def get_container_type(self):
        return None

    def get_container_scope(self):
        return None

    def __str__(self):
        return f"UnknownObj"


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
    alloc: PtStmt
    type: str
    scope: IRScope
    is_callable: bool

    def __init__(self, desc: str, alloc: PtStmt, type: str, scope: IRScope, is_callable: bool):
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
    from .elements import CSVar

    @abstractmethod
    def get_obj(self, alloc_site: PtAllocation, type_obj: TypeObj) -> Obj:
        ...

    def get_cls_obj(self, alloc_site: PtAllocation) -> ClassObj:
        obj = self.get_obj(alloc_site, ClassTypeObject)
        assert isinstance(obj, ClassObj), "The results should be ClassObj"
        return obj

    def get_func_obj(self, alloc_site: PtAllocation) -> FunctionObj:
        obj = self.get_obj(alloc_site, FunctionTypeObject)
        assert isinstance(obj, FunctionObj), "The results should be FunctionObj"
        return obj

    @abstractmethod
    def get_constant_obj(self, value: Any) -> Obj:
        ...

    def get_unknown_obj(self) -> UnknownObj:
        ...

    @abstractmethod
    def is_str_obj(self, obj: Obj) -> bool:
        ...

    @abstractmethod
    def get_mock_obj(self, desc: str, alloc: PtStmt, type: str, scope: IRScope, is_callable: bool) -> Obj:
        ...

    @abstractmethod
    def get_cls_obj(self, alloc_site: ClassObj, parents: List[CSVar]) -> ClassObj:
        ...

    @abstractmethod
    def get_func_obj(self) -> FunctionObj:
        ...


class AbstractHeapModel(HeapModel):
    from .stmts import PtAllocation, AbstractPtStmt
    new_objs: Dict[Tuple[PtAllocation, TypeObj], NewObj]

    constant_objs: Dict[Literals, Obj]
    # new_objs: Dict[PtAllocation, NewObj]
    cls_objs: Dict[PtAllocation, ClassObj]
    merged_objs: Dict[str, MergedObj]
    mock_objs: Dict[MockObj, MockObj]

    @abstractmethod
    def __init__(self, options):
        self.constant_objs = {}
        self.new_objs = {}
        self.merged_objs = {}
        self.cls_objs = {}
        self.mock_objs = {}
        self.options = options

    def get_obj(self, alloc_site: PtAllocation, type_obj: TypeObj) -> Obj:
        return self.do_get_obj(alloc_site)

    def get_merged_obj(self, alloc_site: PtAllocation) -> MergedObj:
        t = alloc_site.get_type()
        if t not in self.merged_objs:
            self.merged_objs[t] = MergedObj(f"<Merged {t}>", t)
        merged_obj = self.merged_objs[t]
        merged_obj.add_represented_obj(self.get_new_obj(alloc_site))
        return merged_obj

    # Use the tuple <alloc_site, type_obj> to determine a obj. InstanceObj has ClassObj as type, while ...
    def get_new_obj(self, alloc_site: PtAllocation, type_obj: TypeObj) -> NewObj:
        if isinstance(type_obj, ClsTypeObj):
            self.get_new_cls_obj(...)

        elif isinstance(proto_obj, LiteralTypeObj):
            ...
        if begin_obj is ...:
            ...
        if alloc_site not in self.new_objs:
            self.new_objs[alloc_site] = NewObj(alloc_site)
        return self.new_objs[alloc_site]

    def get_constant_obj(self, value: Any) -> Obj:
        obj = self.do_get_constant_obj(value)
        return obj

    def get_mock_obj(self, desc: str, alloc: AbstractPtStmt, type: str, scope: IRScope, is_callable: bool) -> Obj:
        mock_obj = MockObj(desc, alloc, type, scope, is_callable)
        if mock_obj not in self.mock_objs:
            self.mock_objs[mock_obj] = mock_obj
        return self.mock_objs[mock_obj]

    def do_get_constant_obj(self, value: Any) -> Obj:
        if value not in self.constant_objs:
            self.constant_objs[value] = ConstantObj(value)
        return self.constant_objs[value]

    def is_str_obj(self, obj: Obj) -> bool:
        return isinstance(obj.get_allocation(), str)

    def get_unknown_obj(self) -> UnknownObj:
        return UnknownObj()

    @abstractmethod
    def do_get_obj(self, alloc_site: PtAllocation) -> Obj:
        ...

    def get_cls_obj(self, alloc_site: PtAllocation, parents) -> ClassObj:
        if alloc_site not in self.cls_objs:
            self.cls_objs[alloc_site] = ClassObj(alloc_site, parents)
        return self.cls_objs[alloc_site]

    def get_func_obj(self) -> FunctionObj:
        ...

    def get_attr(self):
        ...


class AllocationSiteBasedModel(AbstractHeapModel):
    from .stmts import PtAllocation

    def __init__(self, options, obj_groups):
        super().__init__(options)

    def do_get_obj(self, alloc_site: PtAllocation) -> Obj:
        return self.get_new_obj(alloc_site)
