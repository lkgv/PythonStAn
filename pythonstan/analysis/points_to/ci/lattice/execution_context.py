from typing import List, Set, Optional

from .summarized import Summarized
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
        if self_val is not None:
            self.self_val = Value.make_none()

    @classmethod
    def from_execution_context(cls, ec: 'ExecutionContext') -> 'ExecutionContext':
        return cls(ec.scope_chain, ec.self_val)

    def is_empty(self) -> bool:
        return self.scope_chain is None and self.self_val.is_none()

    def add(self, other: 'ExecutionContext') -> bool:
        new_sc = ScopeChain.add(self.scope_chain, other.scope_chain)
        sc_changed = new_sc is not None and new_sc != self.scope_chain
        self.scope_chain = new_sc
        return sc_changed

    def get_scope_chain(self) -> Optional[ScopeChain]:
        return self.scope_chain

    def get_self_val(self) -> Value:
        return self.self_val

    def push_scope_chain(self, obj_labels: Set[ObjLabel]):
        self.scope_chain = ScopeChain.make(obj_labels, self.scope_chain)

    def pop_scope_chain(self):
        assert self.scope_chain is not None, "pop_scope_chain while chain is empty"
        self.scope_chain = self.scope_chain.get_next()

    def summarize(self, s: Summarized):
        self.scope_chain = ScopeChain.summarize(self.scope_chain, s)
        self.self_val = self.self_val.summarize(s)

    def remove(self, other: 'ExecutionContext'):
        self.scope_chain = ScopeChain.remove(self.scope_chain, other.scope_chain)
        self.self_val = self.self_val.remove_objs(other.self_val.get_obj_labels())

    def __eq__(self, other) -> bool:
        if other == self:
            return True
        if not isinstance(other, ExecutionContext):
            return False
        if ((self.scope_chain is None) != (other.scope_chain is None) or
                (self.scope_chain is not None and self.scope_chain != other.scope_chain)):
            return False
        return self.self_val == other.self_val

    def __hash__(self):
        return (0 if self.scope_chain is None else hash(self.scope_chain) * 7 +
                hash(self.self_val) * 11)



    '''


    def set_self_var(self, self_val: Value):
        self.set_var('self', self_val)

    def clone(self) -> 'ExecutionContext':
        return ExecutionContext(self.scope_chain)

    def is_empty(self) -> bool:
        return self.scope_chain is None

    def get_var(self, var_name: str) -> Optional[Value]:
        assert self.scope_chain is not None, "the ExecutionContext is None!"
        return self.scope_chain.get_var(var_name)

    def push_scope_chain(self, obj_labels: Set[ObjLabel]):
        self.scope_chain = ScopeChain.make(obj_labels, self.scope_chain)

    def new_scope_chain(self):
        self.scope_chain = ScopeChain()

    def set_var(self, var_name: str, val: Value) -> bool:
        assert self.scope_chain is not None, "the ExecutionContext is None!"
        self.scope_chain.set_var(var_name, val)

    def add(self) -> bool:
        ...

    '''