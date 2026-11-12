"""Invoke Python's iterator protocol at one explicitly erased host boundary.

Host metadata can arrive as an arbitrary Python object. ``iter`` validates its
special-method slots, including the legacy integer ``__getitem__`` fallback;
an ``Iterable`` ABC check would reject some objects that Python accepts.
"""

from builtins import iter as _builtin_iter
from collections.abc import Callable, Iterator
from typing import Final, cast

# The typeshed overloads enumerate statically known protocol implementations.
# This fixed builtin also accepts an erased object, returning an iterator or
# raising the original protocol error. The cast changes only its input type;
# no user callback or analyzer implementation is assigned this capability.
_ITER_OBJECTS: Final = cast(Callable[[object], Iterator[object]], _builtin_iter)


def iter_objects(value: object) -> Iterator[object]:
    """Return the actual iterator immediately, preserving identity and errors."""
    return _ITER_OBJECTS(value)
