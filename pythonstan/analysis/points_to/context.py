from typing import Iterable, Any, Optional, List


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
