"""CPython 3.12 call phases, independent of source token positions.

A sole starred positional expression remains an iterable until the final call.
With any additional positional expression, each star is expanded while building
positional arguments. Keyword groups are evaluated before their mapping merge.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PositionalArgument:
    expression: ast.expr
    starred: bool
    deferred: bool = False


@dataclass(frozen=True, slots=True)
class KeywordGroup:
    keywords: tuple[ast.keyword, ...]


@dataclass(frozen=True, slots=True)
class KeywordMapping:
    expression: ast.expr


type CallStep = PositionalArgument | KeywordGroup | KeywordMapping


def call_plan(node: ast.Call) -> tuple[CallStep, ...]:
    steps: list[CallStep] = []
    for argument in node.args:
        steps.append(
            PositionalArgument(argument.value, True, len(node.args) == 1)
            if isinstance(argument, ast.Starred)
            else PositionalArgument(argument, False)
        )
    group: list[ast.keyword] = []
    for keyword in node.keywords:
        if keyword.arg is not None:
            group.append(keyword)
        else:
            if group:
                steps.append(KeywordGroup(tuple(group)))
                group = []
            steps.append(KeywordMapping(keyword.value))
    if group:
        steps.append(KeywordGroup(tuple(group)))
    return tuple(steps)
