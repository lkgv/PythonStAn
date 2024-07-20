from typing import List, Optional, Iterable, Any, TypeVar, Generic, Dict

T = TypeVar('T')


class Context(Generic[T]):
    elements: List[T]
    k: Optional[int]

    def __init__(self, elts: Optional[Iterable] = None, k: Optional[int] = None):
        if elts is None:
            self.elements = []
        else:
            self.elements = list(elts)
        self.k = k
        if self.k is not None and len(self.elements) > self.k:
            self.elements = self.elements[len(self.elements) - self.k:]

    def append(self, e: T) -> 'Context[T]':
        return Context(self.elements + [e], self.k)

    def __len__(self):
        return len(self.elements)

    def __getitem__(self, item: int):
        return self.elements[item]


class ContextSensitive:
    _ctx: Context

    def get_context(self) -> Context:
        return self._ctx

    def set_context(self, ctx: Context):
        self._ctx = ctx


class TrieContext(Generic[T], Context[T]):
    _parent: Optional['TrieContext']
    _elem: Any
    _len: int
    _children: Dict[Any, 'TrieContext']

    def __init__(self, parent: Optional['TrieContext[T]'] = None, elem: Any = None):
        self._parent = parent
        self._elem = elem
        self._len = 0
        self._children = {}

    def __len__(self):
        return self._len

    def __getitem__(self, i: int):
        assert 0 <= i < self._len
        if i == self._len - 1:
            return self._elem
        else:
            return self._parent[i]

    def get_parent(self) -> Optional['TrieContext[T]']:
        return self._parent

    def get_child(self, elem: Any) -> Optional['TrieContext[T]']:
        if elem not in self._children:
            self._children[elem] = TrieContext(self, elem)
        return self._children[elem]

    def get_elem(self) -> Any:
        return self._elem


class TrieContextHelper(Generic[T]):
    _root: TrieContext[T]

    def __init__(self):
        self._root = TrieContext()

    def get_empty_context(self) -> TrieContext[T]:
        return self._root

    def make(self, elems: Iterable[T]) -> TrieContext[T]:
        ret = self._root
        for elem in elems:
            ret = ret.get_child(elem)
        return ret

    def make_last_k(self, context: Context[T], k: int) -> TrieContext[T]:
        if k == 0:
            return self._root
        c = context
        if len(c) <= k:
            return c
        elems = [None] * k
        for i in range(k, 0, -1):
            elems[i - 1] = c.get_elem()
            c = c.get_parent()
        return self.make(elems)

    def append(self, parent: Context[T], elem: T, limit: int) -> TrieContext[T]:
        p = parent
        if len(parent) < limit:
            return p.get_child(elem)
        else:
            return self.make_last_k(p, limit - 1).get_child(elem)
