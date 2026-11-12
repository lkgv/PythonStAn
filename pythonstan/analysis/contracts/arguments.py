"""Evaluated call inputs shared by binding consumers, independent of engines."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CallArguments[T]:
    """Evaluated values, retaining ordered and possibly repeated keyword names."""

    positional: tuple[T, ...] = ()
    keywords: tuple[tuple[str, T], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "positional", tuple(self.positional))
        object.__setattr__(self, "keywords", tuple((name, value) for name, value in self.keywords))
