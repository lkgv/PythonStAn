from typing import Iterable, Any, Optional, List, Tuple

from .stmts import PtInvoke
from .heap_model import Obj
from .elements import Var
from pythonstan.ir import IRScope


class Context:
    elements: List
    k: Optional[int]

    def __init__(self, elts: Optional[Iterable] = None, k: Optional[int] = None):
        if elts is None:
            self.elements = []
        else:
            self.elements = list(elts)
        self.k = k
        if self.k is not None and len(self.elements) > self.k:
            self.elements = self.elements[len(self.elements) - self.k:]

    def append(self, e: Any) -> 'Context':
        return Context(self.elements + [e], self.k)

    def __len__(self):
        return len(self.elements)

    def __getitem__(self, item: int):
        return self.elements[item]


CSScope = Tuple[Context, IRScope]
CSObj = Tuple[Context, Obj]
CSVar = Tuple[Context, Obj]


class CSCallSite:
    callsite: PtInvoke
    context: Context
    container: CSScope

    # callees: Set[CSScope]

    def __init__(self, callsite: PtInvoke, context: Context, container: CSScope):
        self.context = context
        self.callsite = callsite
        self.container = container

    def get_callsite(self) -> PtInvoke:
        return self.callsite

    def get_container(self) -> CSScope:
        return self.container

    def __hash__(self):
        return hash((self.callsite, self.context, self.context))
