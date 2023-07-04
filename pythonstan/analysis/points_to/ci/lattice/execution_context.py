from typing import List, Set, Optional

from .scope_chain import ScopeChain
from .obj_label import ObjLabel
from .value import Value

class ExecutionContext:
    scope_chain: Optional[ScopeChain]
    self_val: Value

    def __init__(self,
                 scope_chain: Optional[ScopeChain] = None,
                 self_val: Optional[Value] = None):
        self.scope_chain = scope_chain
        self.self_val = Value.make_none() if self_val is None else self_val

    def clone(self) -> 'ExecutionContext':
        return ExecutionContext(self.scope_chain, self.self_val)

    def is_empty(self) -> bool:
        return self.scope_chain is None and len(self.var_obj) == 0 and self.self_val.is_none()

    def get_var(self, var_name: str) -> Optional[Value]:
        if var_name == 'self':
            return self.self_val
        else:
            return self.scope_chain.get_var(var_name)

    def set_var(self, var_name: str, val: Value) -> bool:
        ...

    def add(self) -> bool:
        ...
