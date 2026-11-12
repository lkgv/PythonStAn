"""Invoke Python's iterator protocol at one explicitly erased host boundary.

Host metadata can arrive as an arbitrary Python object. ``iter`` validates its
special-method slots, including the legacy integer ``__getitem__`` fallback;
an ``Iterable`` ABC check would reject some objects that Python accepts.
"""

from builtins import iter as _builtin_iter
from collections.abc import Callable, Iterator
from operator import contains as _builtin_contains
from typing import Final, cast

# The typeshed overloads enumerate statically known protocol implementations.
# This fixed builtin also accepts an erased object, returning an iterator or
# raising the original protocol error. The cast changes only its input type;
# no user callback or analyzer implementation is assigned this capability.
_ITER_OBJECTS: Final = cast(Callable[[object], Iterator[object]], _builtin_iter)
# Like iter, membership accepts legacy sequence slots that a Container ABC
# check does not recognize. The primitive validates and invokes those slots.
_CONTAINS_OBJECT: Final = cast(Callable[[object, object], bool], _builtin_contains)


def iter_objects(value: object) -> Iterator[object]:
    """Return the actual iterator immediately, preserving identity and errors."""
    return _ITER_OBJECTS(value)


def contains_object(container: object, item: object) -> bool:
    """Run actual membership without enumerating or replacing a cached container."""
    return _CONTAINS_OBJECT(container, item)
