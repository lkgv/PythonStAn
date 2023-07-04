from typing import Optional, Dict, List, Set

from pythonstan.ir import IRScope
from .value import Value


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
    scopes: List[Scope]

    def __init__(self, scopes: Optional[List[Scope]] = None):
        self.scopes = [] if scopes is None else scopes

    def cur_scope(self) -> Scope:
        return self.scopes[-1]

    def add_scope(self, scope: Scope):
        self.scopes.append(scope)

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
