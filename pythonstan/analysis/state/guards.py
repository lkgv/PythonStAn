"""Finite dictionary-presence facts carried by the real branch state."""

from __future__ import annotations

from typing import NamedTuple

from pythonstan.analysis.contracts.identity import ObjectId

from .keys import FieldKey


class MembershipGuard(NamedTuple):
    owner: ObjectId
    key: FieldKey
    positive: bool


__all__ = ["MembershipGuard"]
