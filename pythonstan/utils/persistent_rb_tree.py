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
def is_empty(x: Union[Empty, DoubleEmpty, G]) -> bool:
    if x == E or x == EE:
        return True
    else:
        return False


T = TypeVar('T')
_Node_tuple = Tuple[Color, 'Node_t', T, 'Node_t']
Node_t = Union[Empty, DoubleEmpty, _Node_tuple]
# a node is optionally a 4-tuple (color, element, left child, right child) 

def has_pattern(node: Node_t, pattern) -> bool:
    if pattern is None:
        return True
    if is_empty(pattern) or is_empty(node):
        return node == pattern

    color, l_child, _, r_child = node
    color_pattern, l_pattern, r_pattern = pattern
    if color_pattern is None or color == color_pattern:
        return has_pattern(l_child, l_pattern) and \
            has_pattern(r_child, r_pattern)
    else:
        return False


class Tree(Generic[T]):
    compare: Callable[[T, T], int]

    def __init__(self,
                 compare: Callable[[T, T], int],
                 root: Node_t = E):
        self.root = root
        self.compare = compare
    
    @staticmethod
    def min_tree(t) -> Optional[T]:
        if is_empty(t):
            return None
        c, elt, l, r = t
        if is_empty(l):
            return elt
        return Tree.min_tree(l)

    def min(self) -> Optional[T]:
        return self.min_tree(self.root)
    
    @staticmethod
    def balance(t: Node_t) -> Node_t:
        if has_pattern(t, (B, (R, (R, None, None), None), None)):
            (_, (_, (_, a, x, b), y, c), z, d) = t
            return (R, (B, a, x, b), y, (B, c, z, d))
        if has_pattern(t, (B, (R, None, (R, None, None)), None)):
            (_, (_, a, x, (_, b, y, c)), z, d) = t
            return (R, (B, a, x, b), y, (B, c, z, d))
        if has_pattern(t, (B, None, (R, (R, None, None), None))):
            (_, a, x, (_, (_, b, y, c), z, d)) = t
            return (R, (B, a, x, b), y, (B, c, z, d))
        if has_pattern(t, (B, None, (R, None, (R, None, None)))):
            (_, a, x, (_, b, y, (_, c, z, d))) = t
            return (R, (B, a, x, b), y, (B, c, z, d))
        if has_pattern(t, (BB, None, (R, (R, None, None), None))):
            (_, a, x, (_, (_, b, y, c), z, d)) = t
            return (B, (B, a, x, b), y, (B, c, z, d))
        if has_pattern(t, (BB, (R, None, (R, None, None)), None)):
            (_, (_, a, x, (_, b, y, c)), z, d) = t
            return (B, (B, a, x, b), y, (B, c, z, d))
        return t
    
    @staticmethod
    def rotate(t: Node_t) -> Node_t:
        if has_pattern(t, (R, (BB, None, None), (B, None, None))):
            (_, (_, a, x, b), y, (_, c, z, d)) = t
            return Tree.balance((B, (R, (B, a, x, b), y, c), z, d))
        if has_pattern(t, (R, EE, (B, None, None))):
            (_, _, y, (_, c, z, d)) = t
            return Tree.balance((B, a, x, (R, b, y, (B, c, z, d))))
        if has_pattern(t, (R, (B, None, None), (BB, None, None))):
            (_, (_, a, x, b), y, (_, c, z, d)) = t
            return Tree.balance((B, a, x, (R, b, y, (B, c, z, d))))
        if has_pattern(t, (R, (B, None, None), EE)):
            (_, (_, a, x, b), y, _) = t
            return Tree.balance((B, a, x, (R, b, y, E)))
        if has_pattern(t, (B, (BB, None, None), (B, None, None))):
            (_, (_, a, x, b), y, (_, c, z, d)) = t
            return Tree.balance((BB, (R, (B, a, x, b), y, c), z, d))
        if has_pattern(t, (B, EE, (B, None, None))):
            (_, _, y, (_, c, z, d)) = t
            return Tree.balance((BB, (R, E, y, c), z, d))
        if has_pattern(t, (B, (B, None, None), (BB, None, None))):
            (_, (_, a, x, b), y, (_, c, z, d)) = t
            return Tree.balance((BB, a, x, (R, b, y, (B, c, z, d))))
        if has_pattern(t, (B, (B, None, None), EE)):
            (_, (_, a, x, b), y, _) = t
            return Tree.balance((BB, a, x, (R, b, y, E)))
        if has_pattern(t, (B, (BB, None, None), (R, (B, None, None), None))):
            (_, (_, a, w, b), x, (_, (_, c, y, d), z, e)) = t
            return (B, Tree.balance((B, (R, (B, a, w, b), x, c), y, d)), z, e)
        if has_pattern(t, (B, EE, (R, (B, None, None), None))):
            (_, _, x, (_, (_, c, y, d), z, e)) = t
            return (B, Tree.balance((B, (R, E, x, c), y, d)), z, e)
        if has_pattern(t, (B, (R, None, (B, None, None)), (BB, None, None))):
            (_, (_, a, w, (_, b, x, c)), y, (_, d, z, e)) = t
            return (B, a, w, Tree.balance((B, b, x, (R, c, y, (B, d, z, e)))))
        if has_pattern(t, (B, (R, None, (B, None, None)), EE)):
            (_, (_, a, w, (_, b, x, c)), y, _) = t
            return (B, a, w, Tree.balance((B, b, x, (R, c, y, E))))
        return t

    
    @staticmethod
    def _blacken(t: Node_t) -> Node_t:
        if has_pattern(t, (R, (R, None, None), None)):
            (_, (_, a, x, b), y, c) = t
            return (B, (R, a, x, b), y, c)
        if has_pattern(t, (R, None, (R, None, None))):
            (_, a, x, (_, b, y, c)) = t
            return (B, a, x, (R, b, y, c))
        return t
    
    @staticmethod
    def _redden(t: Node_t) -> Node_t:
        if has_pattern(t, (B, (B, None, None), (B, None, None))):
            (_, (_, a, x, b), y, (_, c, z, d)) = t
            return (R, (B, a, x, b), y, (B, c, z, d))
        return t
    
    def _insert(self, t: Node_t, x: T) -> Node_t:
        if is_empty(t):
            return (R, E, x, E)
        
        (c, a, y, b) = t
        res = self.compare(x, y)
        if res > 0:
            new_t = (c, self._insert(a), y, b)
            return self.balance(new_t)
        elif res == 0:
            return t
        else:
            new_t = (c, a, y, self._insert(b))
            return self.balance(new_t)
    
    def add(self, t: Node_t, x: T) -> Node_t:
        return self._blacken(self._insert(t, x))
    
    @staticmethod
    def min_del(t: Node_t):
        if is_empty(t):
            return t

        if has_pattern(t, (R, E, E)):
            (_, _, x, _) = t
            return (x, E)
        if has_pattern(t, (B, E, E)):
            (_, _, x, _) = t
            return (x, EE)
        if has_pattern(t, (B, E, (R, E, E))):
            (_, _, x, (_, _, y, _)) = t
            return (x, (B, E, y, E))
        (c, a, x, b) = t
        x_, a_ = Tree.min_del(a)
        return (x_, Tree.rotate(c, a_, x, b))
        
    
    def _delete(self, t: Node_t, x: T) -> Node_t:
        if is_empty(t):
            return t
        
        if has_pattern(t, (R, E, E)):
            (_, _, y, _) = t
            if self.compare(x, y) == 0:
                return E
            else:
                return (R, E, y, E)
        if has_pattern(t, (B, E, E)):
            (_, _, y, _) = t
            if self.compare(x, y) == 0:
                return EE
            else:
                return (B, E, y, E)
        if has_pattern(t, (B, (R, E, E), E)):
            (_, (_, _, y, _), z, _) = t
            res = self.compare(x, z)
            if res < 0:
                return (B, self._delete((R, E, y, E), x), z, E)
            elif res == 0:
                return (B, E, y, E)
            else:
                return (B, (R, E, y, E), z, E)
        
        (c, a, y, b) = t
        res = self.compare(x, y)
        if res < 0:
            return self.rotate((c, self._delete(a, x), y, b))
        elif res == 0:
            y_, b_ = self.min_del(b)
            return self.rotate((c, a, y_, b_))
        else:
            return self.rotate((c, a, y, self._delete(b, x)))
        
    def delete(self, t: Node_t, x: T) -> Node_t:
        return self._delete(self._redden(t), x)
    
    def find(self, t: Node_t, x: T) -> Optional[T]:
        if is_empty(t):
            return None
        (_, l, y, r) = t
        res = self.compare(x, y)
        if res < 0:
            return self.find(l, x)
        elif res == 0:
            return y
        else:
            return self.find(r, x)

