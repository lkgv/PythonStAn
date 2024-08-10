from typing import Union, Set, Dict, List, Optional, Any, Generic, TypeVar
from abc import abstractmethod, ABC

from .context import Context, ContextSensitive
from pythonstan.ir import *
from pythonstan.utils.common import Singleton
from pythonstan.graph.call_graph import CallKind

from abc import ABC, abstractmethod
from typing import List, Optional

BUILTIN_CONTEXT = Context()


class Pointer(ContextSensitive):
    _pts: Optional['PointsToSet']

    def get_points_to_set(self) -> Optional['PointsToSet']:
        return self._pts

    def set_points_to_set(self, pts: 'PointsToSet'):
        self._pts = pts


class Var(Pointer):
    _name: str
    _stmt_collector: 'StmtCollector'
    _is_global: bool

    def __init__(self, name: str, ctx: Context, is_global: bool = False):
        self._name = name
        self.set_context(ctx)
        self._is_global = is_global
        self._stmt_collector = StmtCollector()

    def get_name(self) -> str:
        return self._name

    def is_global(self) -> bool:
        return self._is_global

    def get_stmt_collector(self) -> 'StmtCollector':
        return self._stmt_collector

    def __eq__(self, other):
        return isinstance(other, Var) and \
            (self._name, self.get_context(), self._is_global) == (other._name, other.get_context(), other._is_global)

    def __hash__(self):
        return hash((self._name, self.get_context(), self._is_global))

    def __str__(self):
        return f"<{'Global' if self._is_global else ''}Var {self._name}>"


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
    def get_allocation(self) -> Optional['PtAllocation']:
        ...

    @abstractmethod
    def __str__(self):
        ...

    def is_callable(self) -> bool:
        return False

    def __repr__(self):
        return str(self)

    def get_property(self, name: str) -> Optional['InstanceField']:
        return self._properties.get(name, None)

    def set_property(self, name: str, field: 'InstanceField'):
        self._properties[name] = field


class SymbolTable:
    _mem: Dict[str, Pointer]

    def __init__(self):
        self._mem = {}

    def __contains__(self, item: str) -> bool:
        return item in self._mem

    def get(self, name: str) -> Pointer:
        return self._mem[name]

    def set(self, name: str, ptr: Pointer):
        self._mem[name] = ptr


class PtFrame(ContextSensitive):
    _locals: SymbolTable
    _globals: SymbolTable
    _writable_globals: Set[str]
    _call_site: 'PtInvoke'
    _code_obj: Obj

    def __init__(self, call_site: 'PtInvoke', code_obj: Obj, ctx: Context):
        self._locals = SymbolTable()
        self._globals = SymbolTable()
        self._writable_globals = set()
        self._call_site = call_site
        self._code_obj = code_obj
        self.set_context(ctx)

    def get_callsite(self) -> 'PtInvoke':
        return self._call_site

    def gen_var(self, name: str, is_global: bool = False) -> Pointer:
        if isinstance(self.get_code_obj(), ClassObj):
            var = InstanceField(self.get_code_obj(), name)
        else:
            var = Var(name, self.get_context(), is_global)
        if is_global:
            self._globals.set(name, var)
        else:
            self._locals.set(name, var)
        return var

    def get_var_write(self, name: str) -> Pointer:
        if name in self._locals:
            return self._locals.get(name)
        elif name in self._globals and name in self._writable_globals:
            return self._globals.get(name)
        else:
            return self.gen_var(name, False)

    def get_var_read(self, name: str) -> Optional[Pointer]:
        if name in self._locals:
            return self._locals.get(name)
        elif name in self._globals:
            return self._globals.get(name)
        else:
            return None

    def get_locals(self) -> SymbolTable:
        return self._locals

    def get_code_obj(self) -> Obj:
        return self._code_obj

    def get_ir(self) -> IRScope:
        if isinstance(self._code_obj, ClassObj):
            return self._code_obj.get_ir()
        elif isinstance(self._code_obj, FunctionObj):
            return self._code_obj.get_ir()
        elif isinstance(self._code_obj, MethodObj):
            return self._code_obj.get_ir()


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

    def get_allocation(self) -> Optional['PtAllocation']:
        return None

    def __eq__(self, other):
        return isinstance(other, self.__class__) and self.get_value() == other.get_value()

    def __hash__(self):
        return hash(self.get_value())

    def __le__(self, rhs) -> bool:
        return isinstance(rhs, self.__class__) or isinstance(rhs, ClsTypeObj)


class BoolLiteralTypeObj(LiteralTypeObj[int]):
    _value: bool

    def __init__(self, value: bool):
        super().__init__()
        self._value = value

    def get_value(self) -> bool:
        return self._value

    def __str__(self):
        return f"<BoolObj {self.get_value()}>"


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

    def get_allocation(self) -> Optional['PtAllocation']:
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

    def get_allocation(self) -> Optional['PtAllocation']:
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
    _alloc_site: 'PtAllocation'
    _ir: IRClass
    _parents: List['Var']
    _init_method: Optional['MethodObj']

    def __init__(self, alloc_site: 'PtAllocation', parents: List['Var'], ctx: Context):
        super().__init__()
        ir = alloc_site.get_ir()
        assert isinstance(ir, IRClass), "The ir of the allocation(PtAllocation) of ClassObj should be IRClass!"
        self._alloc_site = alloc_site
        self._ir = ir
        self._parents = parents
        self._init_method = None
        self.set_context(ctx)

    def get_parents(self) -> List['Var']:
        return self._parents

    def get_ir(self) -> IRClass:
        return self._ir

    def get_type(self) -> Type:
        return ClassTypeObject

    def get_allocation(self) -> 'PtAllocation':
        return self._alloc_site

    def is_callable(self) -> bool:
        return True

    def get_init_method(self) -> Optional['MethodObj']:
        return self._init_method

    def set_init_method(self, method: 'MethodObj'):
        self._init_method = method

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
    _alloc_site: 'PtAllocation'

    def __init__(self, alloc_site: 'PtAllocation', type_obj: ClassObj):
        super().__init__()
        self.set_context(alloc_site.get_frame().get_context())
        self._alloc_site = alloc_site
        self._type = type_obj

    def get_type(self) -> ClassObj:
        return self._type

    def get_allocation(self) -> 'PtAllocation':
        return self._alloc_site

    def __str__(self):
        return f'<InstanceObj :{str(self.get_type())}>'


class ModuleObj(TypeObj):
    _alloc_site: 'PtAllocation'
    _ir: IRModule

    def __init__(self, alloc_site: 'PtAllocation', ctx: Context):
        super().__init__()
        ir = alloc_site.get_ir()
        assert isinstance(ir, IRClass), "The ir of the allocation(PtAllocation) of ClassObj should be IRClass!"
        self._alloc_site = alloc_site
        self._ir = ir
        self._parents = parents
        self._init_method = None
        self.set_context(ctx)

    def get_parents(self) -> List['Var']:
        return self._parents

    def get_ir(self) -> IRClass:
        return self._ir

    def get_type(self) -> Type:
        return ClassTypeObject

    def get_allocation(self) -> 'PtAllocation':
        return self._alloc_site

    def is_callable(self) -> bool:
        return True

    def get_init_method(self) -> Optional['MethodObj']:
        return self._init_method

    def set_init_method(self, method: 'MethodObj'):
        self._init_method = method

    def __str__(self):
        return f"<class '{self._ir.get_qualname()}'>"

    def __eq__(self, other):
        return isinstance(other, ClassObj) and \
            (self.get_allocation(), self.get_context()) == (other.get_allocation(), other.get_context())

    def __hash__(self):
        return hash((self.get_allocation(), self.get_context(), self.get_parents()))

    def __le__(self, other) -> bool:
        raise NotImplementedError

class Callable(ABC):
    def is_callable(self) -> bool:
        return True


class CallableObj(Obj, Callable, ABC):
    def get_type(self) -> Type:
        return FunctionTypeObject


class BuiltinFunctionObj(CallableObj):
    def __init__(self):
        pass

    def get_allocation(self) -> Optional['PtAllocation']:
        pass

    def __str__(self):
        pass


class FunctionObj(CallableObj):
    _ir: IRFunc
    _alloc_site: 'PtAllocation'
    _params: List['Var']
    _ret: 'Var'

    def __init__(self, alloc_site: 'PtAllocation', params: List['Var'], ret: 'Var'):
        self.set_context(alloc_site.get_frame().get_context())
        ir = alloc_site.get_ir()
        assert isinstance(ir, IRFunc), "The ir of the allocation(PtAllocation) of FunctionObj should be IRFunc!"
        self._ir = ir
        self._params = params
        self._ret = ret

    def get_ir(self) -> IRFunc:
        return self._ir

    def get_ret(self) -> 'Var':
        return self._ret

    def get_params(self) -> List['Var']:
        return self._params

    def get_allocation(self) -> 'PtAllocation':
        return self._alloc_site

    def __str__(self):
        return f"<FunctionObj {self._ir.get_qualname()}>"


class MethodObj(CallableObj):
    _ir: IRFunc
    _alloc_site: 'PtAllocation'
    _obj: Obj
    _params: List['Var']
    _ret: 'Var'

    def __init__(self, alloc_site: 'PtAllocation', params: List['Var'], ret: 'Var'):
        self.set_context(alloc_site.get_frame().get_context())
        ir = alloc_site.get_ir()
        assert isinstance(ir, IRFunc), "The ir of the allocation(PtAllocation) of FunctionObj should be IRFunc!"
        self._ir = ir
        self._params = params
        self._ret = ret

    def get_ir(self) -> IRFunc:
        return self._ir

    def get_ret(self) -> 'Var':
        return self._ret

    def get_params(self) -> List['Var']:
        return self._params

    def get_obj(self) -> Obj:
        return self._obj

    def get_allocation(self) -> 'PtAllocation':
        return self._alloc_site

    def __str__(self):
        return f"<MethodObj {self._ir.get_qualname()}>"


class AwaitableObj(CallableObj):
    _scope: IRScope
    _value: IRCall
    _alloc: 'PtAllocation'

    def __init__(self, scope: IRScope, alloc: 'PtAllocation', value: IRCall):
        self._scope = scope
        self._alloc = alloc
        self._value = value

    def get_type(self) -> str:
        return f"<Awaitable {str(self._value)}>"

    def get_allocation(self) -> 'PtAllocation':
        return self._alloc

    def get_container_scope(self) -> Optional[IRScope]:
        return self._scope

    def get_value(self) -> IRCall:
        return self._value


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


class PtStmt(ABC):
    @abstractmethod
    def get_frame(self) -> PtFrame:
        ...


class AbstractPtStmt(PtStmt):
    _ir: IRStatement
    _frame: PtFrame

    @abstractmethod
    def __init__(self, ir: IRStatement, frame: PtFrame):
        self._ir = ir
        self._frame = frame

    def get_frame(self) -> PtFrame:
        return self._frame

    def get_ir(self) -> IRStatement:
        return self._ir

    def get_context(self) -> Context:
        return self.get_frame().get_context()


class PtAllocation(AbstractPtStmt):
    _type: Type

    def __init__(self, ir: IRStatement, frame: PtFrame, type_obj: Type):
        super().__init__(ir, frame)
        self._type = type_obj

    def get_type(self) -> Type:
        return self._type

    def __eq__(self, other):
        return isinstance(other, PtAllocation) and \
            (self.get_frame(), self.get_ir(), self.get_type()) == (other.get_frame(), other.get_ir(), other.get_type())

    def __hash__(self):
        return hash((self.get_frame(), self.get_ir(), self.get_type()))


class PtInvoke(AbstractPtStmt):
    _call_kind: CallKind
    _func: Var
    _args: List[Var]
    _target: Optional[Var]

    def __init__(self, ir: IRStatement, frame: PtFrame, call_kind: CallKind,
                 func: Var, args: List[Var], target: Optional[Var]):
        super().__init__(ir, frame)
        self._call_kind = call_kind
        self._func = func
        self._args = args
        self._target = target

    def get_call_kind(self) -> CallKind:
        return self._call_kind

    def get_func(self) -> Var:
        return self._func

    def get_args(self) -> List[Var]:
        return self._args

    def get_target(self) -> Optional[Var]:
        return self._target


class PtLoadSubscr(AbstractPtStmt):
    def __init__(self, ir: IRStatement, frame: PtFrame):
        super().__init__(ir, frame)


class PtStoreSubscr(AbstractPtStmt):
    def __init__(self, ir: IRStatement, frame: PtFrame):
        super().__init__(ir, frame)


class PtLoadAttr(AbstractPtStmt):
    def __init__(self, ir: IRStatement, frame: PtFrame, lval: Var, rval: Var, field: str):
        super().__init__(ir, frame)
        self.lval = lval
        self.rval = rval
        self.field = field

    def get_lval(self) -> Var:
        return self.lval

    def get_rval(self) -> Var:
        return self.rval

    def get_field(self) -> str:
        return self.field


class PtStoreAttr(AbstractPtStmt):
    def __init__(self, ir: IRStatement, frame: PtFrame, lval: Var, rval: Var, field: str):
        super().__init__(ir, frame)
        self.lval = lval
        self.rval = rval
        self.field = field

    def get_lval(self) -> Var:
        return self.lval

    def get_rval(self) -> Var:
        return self.rval

    def get_field(self) -> str:
        return self.field


class StmtCollector:
    store_attrs: Optional[List[PtStoreAttr]]
    load_attrs: Optional[List[PtLoadAttr]]
    store_subscrs: Optional[List[PtStoreSubscr]]
    load_subscrs: Optional[List[PtLoadSubscr]]
    invokes: Optional[List[PtInvoke]]

    def __init__(self):
        self.store_attrs = None
        self.load_attrs = None
        self.store_subscrs = None
        self.load_subscrs = None
        self.invokes = None

    def add_load_attr(self, stmt: PtLoadAttr):
        if self.load_attrs is not None:
            self.load_attrs.append(stmt)
        else:
            self.load_attrs = [stmt]

    def add_store_attr(self, stmt: PtStoreAttr):
        if self.store_attrs is not None:
            self.store_attrs.append(stmt)
        else:
            self.store_attrs = [stmt]

    def add_invoke(self, stmt: PtInvoke):
        if self.invokes is not None:
            self.invokes.append(stmt)
        else:
            self.invokes = [stmt]

    def get_store_attrs(self) -> List[PtStoreAttr]:
        if self.store_attrs is not None:
            return self.store_attrs
        else:
            return []

    def get_load_attrs(self) -> List[PtLoadAttr]:
        if self.load_attrs is not None:
            return self.load_attrs
        else:
            return []

    def get_store_subscrs(self) -> List[PtStoreSubscr]:
        if self.store_subscrs is not None:
            return self.store_subscrs
        else:
            return []

    def get_load_subscrs(self) -> List[PtLoadSubscr]:
        if self.load_subscrs is not None:
            return self.load_subscrs
        else:
            return []

    def get_invokes(self) -> List[PtInvoke]:
        if self.invokes is not None:
            return self.invokes
        else:
            return []
