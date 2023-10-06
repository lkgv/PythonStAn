from typing import TypeVar, Generic, List

T = TypeVar('T')


class WorkList(Generic[T]):
    arr = List[T]

    def __init__(self):
        self.arr = []

    def empty(self) -> bool:
        return len(self.arr) == 0

    def pop(self) -> T:
        return self.arr.pop()

    def add(self, e: T):
        self.arr.insert(0, e)
