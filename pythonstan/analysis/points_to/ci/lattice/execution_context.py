from typing import List, Set, Optional

from .scope_chain import ScopeChain
from .obj_label import ObjLabel
from .value import Value

class ExecutionContext:
    scope_chain: Optional[ScopeChain]
    var_obj: Set[ObjLabel]
    vself: Value

    def __init__(self,
                 scope_chain: Optional[ScopeChain] = None,
                 var_obj: Optional[Set[ObjLabel]] = None,
                 vself: Optional[Value] = None):
        self.scope_chain = scope_chain
        self.var_obj = {*()} if var_obj is None else var_obj
        self.vself = Value.make_none() if vself is None else vself
