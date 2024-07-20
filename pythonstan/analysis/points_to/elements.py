from typing import Union, Set, Dict, List, Optional, Any, Generic, TypeVar
from abc import abstractmethod, ABC

from .context import Context, ContextSensitive
from .stmts import *
from pythonstan.ir import IRScope, IRCall, IRClass, IRFunc
from pythonstan.utils.common import Singleton


__all__ = ['Var', 'Pointer', 'ClassField', 'CSScope', 'CSObj', 'CSVar', 'InstanceField', 'ArrayIndex', 'CSCallSite']


BUILTIN_CONTEXT = Context()

class Pointer(ContextSensitive):
    _pts: Optional['PointsToSet']

    def get_points_to_set(self) -> Optional['PointsToSet']:
        return self._pts

    def set_points_to_set(self, pts: 'PointsToSet'):
        self._pts = pts


class Type(ABC):
    @abstractmethod
    def __eq__(self, other):
        ...

    @abstractmethod
    def __hash__(self):
        ...

    @abstractmethod
    def __le__(self, rhs) -> bool:
        ...


class Obj(ABC, ContextSensitive):
    _idx: Optional[int]
    _properties: Dict[str, 'InstanceField']
    _type: Type

    @abstractmethod
    def __init__(self):
        self._properties = {}

    def set_idx(self, idx: int):
        assert self._idx is not None, "idx already set"
        assert self._idx >= 0, f"idx must be 0 or positive number, given: {idx}"
        self._idx = idx

    def get_idx(self) -> int:
        assert self._idx is not None, "idx has not been set!"
        return self._idx

    @abstractmethod
    def get_type(self) -> Type:
        return self._type

    @abstractmethod
    def get_allocation(self) -> Optional[PtAllocation]:
        ...

    @abstractmethod
    def __str__(self):
        ...

    def is_callable(self) -> bool:
        return False

    def __repr__(self):
        return str(self)

    def get_property(self, name: str) -> 'InstanceField':
        return self._properties.get(name, None)

    def set_property(self, name: str, field: 'InstanceField'):
        self._properties[name] = field


class TypeObj(Obj, Type, ABC):
    _type: 'ClsTypeObj'

    @abstractmethod
    def __init__(self):
        super().__init__()
        self.set_context(BUILTIN_CONTEXT)

    def get_type(self) -> Type:
        return ClassTypeObject


T = TypeVar('T')


class LiteralTypeObj(TypeObj, Generic[T], ABC):
    @abstractmethod
    def get_value(self) -> T:
        ...

    def get_allocation(self) -> Optional[PtAllocation]:
        return None

    def __eq__(self, other):
        return isinstance(other, self.__class__) and self.get_value() == other.get_value()

    def __hash__(self):
        return hash(self.get_value())

    def __le__(self, rhs) -> bool:
        return isinstance(rhs, self.__class__) or isinstance(rhs, ClsTypeObj)


class IntLiteralTypeObj(LiteralTypeObj[int]):
    _value: int

    def __init__(self, value: int):
        super().__init__()
        self._value = value

    def get_value(self) -> int:
        return self._value

    def __str__(self):
        return f"<IntObj {self.get_value()}>"


class FloatLiteralTypeObj(LiteralTypeObj[float]):
    _value: float

    def __init__(self, value: int):
        super().__init__()
        self._value = value

    def get_value(self) -> float:
        return self._value

    def __str__(self):
        return f"<FloatObj {self.get_value()}>"


class StrLiteralTypeObj(LiteralTypeObj[str]):
    _value: str

    def __init__(self, value: str):
        super().__init__()
        self._value = value

    def get_value(self) -> str:
        return self._value

    def __str__(self):
        return f"<StrObj '{self.get_value()}'>"


class NoneLiteralTypeObj(LiteralTypeObj[type(None)]):
    def __init__(self):
        super().__init__()

    def get_value(self) -> type(None):
        return None

    def __str__(self):
        return "<NoneObj>"


class ClsTypeObj(TypeObj, Singleton):
    def __init__(self):
        super().__init__()

    def get_type(self) -> Type:
        return self

    def get_allocation(self) -> Optional[PtAllocation]:
        return None

    def get_container_scope(self) -> Optional[IRScope]:
        return None

    def __str__(self):
        return "<class 'type'>"

    def __eq__(self, other):
        return isinstance(other, ClsTypeObj)

    def __hash__(self):
        return hash(ClassTypeObject)

    def __le__(self, rhs) -> bool:
        return isinstance(rhs, ClsTypeObj)


ClassTypeObject = ClsTypeObj()


class FuncTypeObj(TypeObj, Singleton):
    def __init__(self):
        super().__init__()

    def get_type(self) -> Type:
        return self

    def get_allocation(self) -> Optional[PtAllocation]:
        return None

    def get_container_scope(self) -> Optional[IRScope]:
        return None

    def __str__(self):
        return "<class 'function'>"

    def __eq__(self, other):
        return isinstance(other, FuncTypeObj)

    def __hash__(self):
        return hash(FunctionTypeObject)

    def __le__(self, rhs) -> bool:
        return isinstance(rhs, FuncTypeObj) or isinstance(rhs, ClsTypeObj)


FunctionTypeObject = FuncTypeObj()


class ClassObj(TypeObj):
    _alloc_site: PtAllocation
    _ir: IRClass
    _parents: List['Var']

    def __init__(self, alloc_site: PtAllocation, parents: List['Var'], ctx: Context):
        super().__init__()
        ir = alloc_site.get_ir()
        assert isinstance(ir, IRClass), "The ir of the allocation(PtAllocation) of ClassObj should be IRClass!"
        self._alloc_site = alloc_site
        self._ir = ir
        self._parents = parents
        self.set_context(ctx)

    def get_parents(self) -> List['Var']:
        return self._parents

    def get_ir(self) -> IRClass:
        return self._ir

    def get_type(self) -> Type:
        return ClassTypeObject

    def get_allocation(self) -> PtAllocation:
        return self._alloc_site

    def is_callable(self) -> bool:
        return True

    def __str__(self):
        return f"<class '{self._ir.get_qualname()}'>"

    def __eq__(self, other):
        return isinstance(other, ClassObj) and \
            (self.get_allocation(), self.get_context()) == (other.get_allocation(), other.get_context())

    def __hash__(self):
        return hash((self.get_allocation(), self.get_context(), self.get_parents()))

    def __le__(self, other) -> bool:
        raise NotImplementedError


class InstanceObj(Obj):
    _type: ClassObj
    _alloc_site: PtAllocation

    def __init__(self, alloc_site: PtAllocation, type_obj: ClassObj):
        super().__init__()
        self._alloc_site = alloc_site
        self._type = type_obj
        self._scope = alloc_site.get_container_scope()

    def get_type(self) -> ClassObj:
        return self._type

    def get_allocation(self) -> PtAllocation:
        return self._alloc_site

    def get_container_scope(self) -> IRScope:
        return self._scope

    def __str__(self):
        return f'<InstanceObj :{str(self.get_type())}>'


class AbstractFunctionObj(Obj, ABC):
    _scope: IRScope
    _ir: IRFunc

    def __init__(self, scope: IRScope, ir: IRFunc):
        super().__init__()
        self._scope = scope
        self._ir = ir

    def get_ir(self) -> IRFunc:
        return self._ir

    def is_callable(self) -> bool:
        return True


class BuiltinFunctionObj(AbstractFunctionObj):
    ...

class FunctionObj(AbstractFunctionObj):
    _vars: List['Var']
    ...


class AwaitableObj(Obj):
    _scope: IRScope
    _value: IRCall
    _alloc: PtAllocation

    def __init__(self, scope: IRScope, alloc: PtAllocation, value: IRCall):
        self._scope = scope
        self._alloc = alloc
        self._value = value

    def get_type(self) -> str:
        return f"<Awaitable {str(self._value)}>"

    def get_allocation(self) -> PtAllocation:
        return self._alloc

    def get_container_scope(self) -> Optional[IRScope]:
        return self._scope

    def get_value(self) -> IRCall:
        return self._value


class Var(Pointer):
    _name: str
    _is_global: bool

    def __init__(self, name: str, ctx: Context, is_global: bool = False):
        self._name = name
        self.set_context(ctx)
        self._is_global = is_global

    def get_name(self) -> str:
        return self._name

    def is_global(self) -> bool:
        return self._is_global

    def __eq__(self, other):
        return isinstance(other, Var) and \
            (self._name, self.get_context(), self._is_global) == (other._name, other.get_context(), other._is_global)

    def __hash__(self):
        return hash((self._name, self.get_context(), self._is_global))

    def __str__(self):
        return f"<{'Global' if self._is_global else ''}Var {self._name}>"


class ClassField(Pointer):
    _base: ClassObj
    _field: str

    def __init__(self, base: ClassObj, field: str):
        self._base = base
        self._field = field

    def get_base(self) -> Obj:
        return self._base

    def get_field(self) -> str:
        return self._field

    def __eq__(self, other):
        if not isinstance(other, ClassField):
            return False
        return (self._base, self._field) == (other._base, other._field)

    def __hash__(self):
        return hash((self._base, self._field))

    def get_context(self) -> Context:
        return self.get_base().get_context()

    def set_context(self, ctx: Context):
        raise NotImplementedError()


class InstanceField(Pointer):
    _base: Obj
    _field: str

    def __init__(self, base: Obj, field: str):
        self._base = base
        self._field = field

    def get_base(self) -> Obj:
        return self._base

    def get_field(self) -> str:
        return self._field

    def __eq__(self, other):
        if not isinstance(other, InstanceField):
            return False
        return self._base == other._base and self._field == other._field

    def __hash__(self):
        return hash((self._base, self._field))

    def get_context(self) -> Context:
        return self.get_base().get_context()

    def set_context(self, ctx: Context):
        raise NotImplementedError()


class ArrayIndex(Pointer):
    _base: Obj


class PtCallSite:
    _callsite: PtInvoke
    _context: Context
    _container: CSScope

    # callees: Set[CSScope]

    def __init__(self, callsite: PtInvoke, context: Context, container: CSScope):
        self._context = context
        self._callsite = callsite
        self._container = container

    def get_callsite(self) -> PtInvoke:
        return self._callsite

    def get_context(self) -> Context:
        return self._context

    def get_container(self) -> CSScope:
        return self._container

    def __hash__(self):
        return hash((self._callsite, self._context, self._container))

    def __eq__(self, other):
        if not isinstance(other, CSCallSite):
            return False
        return (self._callsite == other._callsite and self._context == other._context
                and self._container == other._container)




class PointsToSet:
    pts: Set[Obj]

    def __init__(self, obj: Optional[Obj] = None):
        if obj is None:
            self.pts = set()
        else:
            self.pts = {obj}

    def add_obj(self, obj: Obj) -> bool:
        ret = obj in self.pts
        self.pts.add(obj)
        return ret

    def add_all(self, pts: 'PointsToSet') -> bool:
        if self.pts.issuperset(pts):
            return False
        else:
            self.pts.update(pts)
            return True

    @classmethod
    def from_set(cls, pts: Set[Obj]) -> 'PointsToSet':
        ret = cls()
        ret.pts = {x for x in pts}
        return ret

    @classmethod
    def from_obj(cls, obj: Obj) -> 'PointsToSet':
        return cls(obj)

    def has(self, obj: Obj) -> bool:
        return obj in self.pts

    def is_empty(self) -> bool:
        return len(self.pts) == 0

    def size(self) -> int:
        return len(self.pts)

    def get_objs(self) -> Set[Obj]:
        return self.pts

    def __sub__(self, other: 'PointsToSet') -> 'PointsToSet':
        return self.from_set(self.pts - other.pts)

    def __str__(self):
        return str(self.pts)

    def __repr__(self):
        return str(self.pts)

    def __iter__(self):
        return iter(self.pts)
