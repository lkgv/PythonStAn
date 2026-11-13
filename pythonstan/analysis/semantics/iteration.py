"""Explicit iteration completion information shared by constructor consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.state.store import Store

type HintMode = Literal["none", "value", "iterator"]


@dataclass(frozen=True)
class CollectedValues:
    store: Store
    values: tuple[Value, ...]
    complete: bool = True


NONITERABLE_SCALAR_SORTS = frozenset(
    {"int", "float", "bool", "none", "complex", "ellipsis", "NotImplementedType"}
)
