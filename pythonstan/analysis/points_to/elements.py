from typing import Union, Optional, Set

from .stmts import PtInvoke
from .heap_model import Obj
from .context import Context
from pythonstan.ir import IRScope

__all__ = ['Var', 'Pointer', 'StaticField', 'CSScope', 'CSObj', 'CSVar', 'InstanceField', 'ArrayIndex', 'CSCallSite']


class Var:
    name: str
    is_global: bool

    def __init__(self, name: str, is_global: bool = False):
        self.name = name
        self.is_global = is_global

    @classmethod
    def copy_from(cls, var: 'Var') -> 'Var':
        return cls(var.name, var.is_global)


class Pointer:
    pts: Optional['PointsToSet']

    def get_points_to_set(self) -> Optional['PointsToSet']:
        return self.pts

    def set_points_to_set(self, pts: 'PointsToSet'):
        self.pts = pts


class StaticField(Pointer):
    ...


class CSScope(Pointer):
    _context: Context
    _scope: IRScope

    def __init__(self, context: Context, scope: IRScope):
        self._context = context
        self._scope = scope

    def get_context(self) -> Context:
        return self._context

    def get_scope(self) -> IRScope:
        return self._scope

    def __eq__(self, other):
        if not isinstance(other, CSScope):
            return False
        return self._context == other._context and self._scope == other._scope

    def __hash__(self):
        return hash((self._context, self._scope))


class CSObj(Pointer):
    _context: Context
    _obj: Obj

    def __init__(self, context: Context, obj: Obj):
        self._context = context
        self._obj = obj

    def get_context(self) -> Context:
        return self._context

    def get_obj(self) -> Obj:
        return self._obj

    def __eq__(self, other):
        if not isinstance(other, CSObj):
            return False
        return self._context == other._context and self._obj == other._obj

    def __hash__(self):
        return hash((self._context, self._obj))


class CSVar(Pointer):
    _context: Context
    _var: Var

    def __init__(self, context: Context, var: Var):
        self._context = context
        self._var = var

    def get_context(self) -> Context:
        return self._context

    def get_var(self) -> Var:
        return self._var

    def __eq__(self, other):
        if not isinstance(other, CSVar):
            return False
        return self._context == other._context and self._var == other._var

    def __hash__(self):
        return hash((self._context, self._var))


class InstanceField(Pointer):
    _base: CSObj

    def get_base(self) -> CSObj:
        return self._base

    ...


class ArrayIndex(Pointer):
    ...


class CSCallSite:
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
    pts: Set[CSObj]

    def __init__(self, obj: Optional[CSObj] = None):
        if obj is None:
            self.pts = set()
        else:
            self.pts = {obj}

    def add_obj(self, obj: CSObj) -> bool:
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
    def from_set(cls, pts: Set['CSObj']) -> 'PointsToSet':
        ret = cls()
        ret.pts = {x for x in pts}
        return ret

    @classmethod
    def from_obj(cls, obj: CSObj) -> 'PointsToSet':
        return cls(obj)

    def has(self, obj: CSObj) -> bool:
        return obj in self.pts

    def is_empty(self) -> bool:
        return len(self.pts) == 0

    def size(self) -> int:
        return len(self.pts)

    def get_objs(self) -> Set[CSObj]:
        return self.pts

    def __sub__(self, other: 'PointsToSet') -> 'PointsToSet':
        return self.from_set(self.pts - other.pts)

    def __str__(self):
        return str(self.pts)

    def __repr__(self):
        return str(self.pts)

    def __iter__(self):
        return iter(self.pts)
