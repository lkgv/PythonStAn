"""Resolved lexical names carry their canonical cell namespace explicitly."""

from dataclasses import dataclass
from typing import Literal

from pythonstan.analysis.contracts.identity import CellId

type BindingScope = Literal["global", "local", "closure", "nonlocal", "unbound_nonlocal"]


@dataclass(frozen=True, slots=True)
class BindingRef:
    name: str
    cell: CellId
    scope: BindingScope
