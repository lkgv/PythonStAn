from tokenize import Double
from typing import *
from enum import Enum

from .common import Singleton


class Color(Enum):
    BLACK = 0
    DoubleBlack = 1
    RED = 2


B = Color.BLACK
BB = Color.DoubleBlack
R = Color.RED


class Empty(Singleton):
    pass


class DoubleEmpty(Singleton):
    pass


E = Empty()
EE = DoubleEmpty()


G = TypeVar('G')
def is_empty(x: Union[G, Empty, DoubleEmpty]) -> bool:
    if x == E or x == EE:
        return True
    else:
        return False


T = TypeVar('T')

class Node(Generic[T]):
    c: Color
    l: Union['Node[T]', Empty, DoubleEmpty]
    r: Union['Node[T]', Empty, DoubleEmpty]
    elt: T

    def __init__(self, elt, c=B, l=E, r=E):
        self.elt = elt
        self.c = c
        self.l = l
        self.r = r
    
    @staticmethod
    def has_pattern(t: Union['Node[T]', Empty, DoubleEmpty], p) -> bool:
        if p is None:
            return True

        if is_empty(p) or is_empty(t):
            return t == p

        color, l, r = p
        if color is None or t.c == color:
            return Node.has_pattern(t.l, l) and Node.has_pattern(t.r, r)
        else:
            return False
    
    def has_pattern(self, pattern) -> bool:
        return self.has_pattern(self, pattern)


Node_T = Union[Node[T], Empty, DoubleEmpty]


class Tree(Generic[T]):
    root: Node_T
    compare: Callable[[T, T], int]

    def __init__(self,
                 compare: Callable[[T, T], int],
                 root: Node_T = E):
        self.root = root
        self.compare = compare
    
    @staticmethod
    def min_tree(t: Node_T) -> Optional[T]:
        if is_empty(t):
            return None
        if is_empty(t.l):
            return t.elt
        return Tree.min_tree(t.l)

    def min(self) -> Optional[T]:
        return self.min_tree(self.root)
    
    @staticmethod
    def balance(t: Node_T) -> Node_T:
        if t.has_pattern((B, (R, (R, None, None), None), None)) or
           t.has_pattern((B, (R, None, (R, None, None)), None)) or
           t.has_pattern()



