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
        if self_val is not None:
            self.set_self_var(self_val)

    def set_self_var(self, self_val: Value):
        self.set_var('self', self_val)

    def clone(self) -> 'ExecutionContext':
        return ExecutionContext(self.scope_chain)

    def is_empty(self) -> bool:
        return self.scope_chain is None

    def get_var(self, var_name: str) -> Optional[Value]:
        assert self.scope_chain is not None, "the ExecutionContext is None!"
        return self.scope_chain.get_var(var_name)

    def new_scope_chain(self):
        self.scope_chain = ScopeChain()

    def set_var(self, var_name: str, val: Value) -> bool:
        assert self.scope_chain is not None, "the ExecutionContext is None!"
        self.scope_chain.set_var(var_name, val)

    def add(self) -> bool:
        ...
