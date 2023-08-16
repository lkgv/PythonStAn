from typing import Optional, Dict, List, Set

from pythonstan.ir import IRScope
from .value import Value
from .obj_label import ObjLabel
from .summarized import Summarized


class Scope:
    var_map: Dict[str, Value]
    ir: Optional[IRScope]
    global_vars: Set[str]
    nonlocal_vars: Set[str]

    def __init__(self, ir: Optional[IRScope] = None):
        self.ir = ir
        self.var_map = {}
        self.global_vars = {*()}
        self.nonlocal_vars = {*()}

    def has_var(self, var_name: str) -> bool:
        return var_name in self.var_map

    def get_var(self, var_name: str) -> Optional[Value]:
        return self.var_map.get(var_name)

    def update_var(self, var_name: str, val: Value) -> bool:
        if self.var_map[var_name] == val:
            return False
        else:
            self.var_map[var_name].join(val, True)
            return True

    def get_ir(self) -> Optional[IRScope]:
        return self.ir

    def is_local(self) -> bool:
        return self.ir is None

    def add_global(self, var_name: str):
        self.global_vars.add(var_name)

    def add_nonlocal(self, var_name: str):
        self.nonlocal_vars.add(var_name)

    def is_global(self, var_name: str) -> bool:
        return var_name in self.global_vars

    def is_nonlocal(self, var_name: str) -> bool:
        return var_name in self.nonlocal_vars


class ScopeChain:
    obj: Set[ObjLabel]
    next_sc: Optional['ScopeChain']

    def __init__(self, obj: Set[ObjLabel], next_sc: Optional['ScopeChain']):
        self.obj = obj
        self.next_sc = next_sc

    @classmethod
    def make(cls, obj: Set[ObjLabel], next_sc: 'ScopeChain') -> 'ScopeChain':
        return cls(obj, next_sc)

    def get_next(self) -> Optional['ScopeChain']:
        return self.next_sc

    def get_obj(self) -> Set[ObjLabel]:
        return self.obj

    @classmethod
    def summarize(cls,
                  sc: Optional['ScopeChain'],
                  s: Optional[Summarized],
                  summarize_cache: Optional[Dict['ScopeChain', 'ScopeChain']] = None
                  ) -> Optional['ScopeChain']:
        if sc is None:
            return None
        if s is None:
            return sc
        if summarize_cache is None:
            summarize_cache = {}
        cs = summarize_cache.get(sc)
        if cs is None:
            new_obj = {*()}
            for l in sc.get_obj():
                if l.is_singleton() and s.is_maybe_summarized(l):
                    new_obj.add(l.make_summary())
                    if not s.is_definitely_summarized(l):
                        new_obj.add(l)
                else:
                    new_obj.add(l)
            n = cls.summarize(sc.get_next(), s, summarize_cache)
            cs = cls.make(new_obj, n)
            summarize_cache[sc] = cs
        return cs

    @classmethod
    def add(cls, s1: Optional['ScopeChain'], s2: Optional['ScopeChain']
            ) -> Optional['ScopeChain']:
        if s1 is None:
            return s2
        if s2 is None:
            return s1
        n = cls.add(s1.get_next(), s2.get_next())
        new_obj = s1.get_obj().union(s2.get_obj())
        return cls.make(new_obj, n)

    @classmethod
    def remove(cls, s1: Optional['ScopeChain'], s2: Optional['ScopeChain']
               ) -> Optional['ScopeChain']:
        if s1 is None or s2 is None:
            return s1
        n = cls.remove(s1.get_next(), s2.get_next())
        new_obj = s1.get_obj().difference(s2.get_obj())
        return cls.make(new_obj, n)

    @classmethod
    def is_empty(cls, s: Optional['ScopeChain']) -> bool:
        if s is None:
            return True
        return len(s.get_obj()) == 0 and cls.is_empty(s.get_next())

    def __eq__(self, other) -> bool:
        if other == self:
            return True
        if not isinstance(other, ScopeChain):
            return False
        if ((self.get_next() is None) != (other.get_next() is None) or
                (self.get_next() is not None and not self.get_next() == other.get_next())):
            return False
        return self.get_obj() == other.get_obj()




class OldScopeChain:
    scopes: List[Scope]

    def __init__(self, scopes: Optional[List[Scope]] = None):
        self.scopes = [] if scopes is None else scopes

    def cur_scope(self) -> Scope:
        return self.scopes[-1]

    def add_scope(self, scope: Optional[Scope] = None):
        if scope is not None:
            self.scopes.append(scope)
        else:
            self.scopes.append(Scope())

    def get_var(self, var_name: str) -> Optional[Value]:
        for scope in self.scopes[:-1]:
            if scope.has_var(var_name):
                return scope.get_var(var_name)
        return None

    def is_global(self, var_name: str) -> bool:
        for scope in self.scopes[:-1]:
            if scope.is_global(var_name):
                return True
            ir = scope.get_ir()
            if ir is not None:
                return False
        return False

    def set_var(self, var_name: str, val: Value) -> bool:
        is_global = self.is_global(var_name)
        is_nonlocal = False
        for scope in self.scopes[:-1]:
            if scope.has_var(var_name):
                return scope.update_var(var_name, val)
            if not is_global and isinstance(scope, IRScope):
                break
        self.cur_scope().update_var(var_name, val)
        return False

    def clone(self) -> 'ScopeChain':
        return ScopeChain([x for x in self.scopes])

    def __add__(self, other):
        return ScopeChain(self.scopes + other.scopes)

    def __eq__(self, other):
        return self.scopes == other.scopes
