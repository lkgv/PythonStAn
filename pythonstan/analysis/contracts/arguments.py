"""Evaluated call inputs shared by binding consumers, independent of engines."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CallArguments[T]:
    """Evaluated values, retaining ordered and possibly repeated keyword names."""

    positional: tuple[T, ...] = ()
    keywords: tuple[tuple[str, T], ...] = ()

    def __post_init__(self) -> None:
        positional: tuple[T, ...] = tuple(self.positional)
        keywords: tuple[tuple[str, T], ...] = tuple((name, value) for name, value in self.keywords)
        object.__setattr__(self, "positional", positional)
        object.__setattr__(self, "keywords", keywords)
