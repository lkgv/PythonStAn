"""Typed heap keys preserve target literals and opaque analyzer slots."""

from __future__ import annotations

from enum import Enum

from pythonstan.analysis.domains.reabstract import ReferenceKey

type LiteralKey = None | bool | int | float | str | bytes | tuple[LiteralKey, ...]
# Enum members occur only as analyzer-private tuple tags. Source enum objects
# remain reference-valued, so this does not confuse them with target literals.
type FieldKey = None | bool | int | float | str | bytes | Enum | ReferenceKey | tuple[FieldKey, ...]

__all__ = ["FieldKey", "LiteralKey"]
