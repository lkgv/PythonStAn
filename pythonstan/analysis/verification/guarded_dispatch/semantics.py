"""Denotations of the small relational DSL, independent of engine transfers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolicInput:
    parameter: str


@dataclass(frozen=True)
class SymbolicFreshBox:
    allocation: str
    slot: str
    payload: SymbolicInput


@dataclass(frozen=True)
class SymbolicTop:
    """Any modeled return; existing user heap still obeys the frame contract."""


def interpret_relation(relation, parameter="payload", allocation="fresh-template"):
    if relation == "input":
        return SymbolicInput(parameter)
    if relation == "fresh_box":
        return SymbolicFreshBox(allocation, "value", SymbolicInput(parameter))
    if relation == "top":
        return SymbolicTop()
    raise ValueError("not a guarded-dispatch relation")


def relation_covers(candidate, concrete_symbolic_relation):
    return isinstance(candidate, SymbolicTop) or candidate == concrete_symbolic_relation
