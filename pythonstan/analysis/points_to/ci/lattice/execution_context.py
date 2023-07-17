from typing import List, Set, Optional

from .scope_chain import ScopeChain
from .obj_label import ObjLabel
from .value import Value


class ExecutionContext:
    scope_chain: Optional[ScopeChain]

    def __init__(self,
                 scope_chain: Optional[ScopeChain] = None,
                 self_val: Optional[Value] = None):
        self.scope_chain = scope_chain

    def clone(self) -> 'ExecutionContext':
        return ExecutionContext(self.scope_chain)

    def is_empty(self) -> bool:
        return self.scope_chain is None

    def get_var(self, var_name: str) -> Optional[Value]:
        return self.scope_chain.get_var(var_name)

    def set_var(self, var_name: str, val: Value) -> bool:
        assert var_name
        ...

    def add(self) -> bool:
        ...
