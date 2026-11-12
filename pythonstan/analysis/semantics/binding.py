"""Shared binding of already evaluated call values; never executes source."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pythonstan.analysis.contracts.arguments import CallArguments


@dataclass(frozen=True)
class BindingResult[T]:
    bindings: tuple[tuple[str, T], ...]
    varargs: tuple[T, ...] = ()
    kwargs: tuple[tuple[str, T], ...] = ()
    errors: tuple[str, ...] = ()


def bind_arguments[T](
    signature: ast.arguments,
    arguments: CallArguments[T],
    defaults: Iterable[T] = (),
    kw_defaults: Mapping[str, T] | Iterable[tuple[str, T]] = (),
) -> BindingResult[T]:
    positional = arguments.positional
    keywords = arguments.keywords
    positional_defaults = tuple(defaults)
    keyword_defaults = dict(kw_defaults)
    posonly = [argument.arg for argument in signature.posonlyargs]
    ordinary = [argument.arg for argument in signature.args]
    allpos = posonly + ordinary
    bound: dict[str, T] = {}
    extras: dict[str, T] = {}
    errors: list[str] = []
    for name, value in zip(allpos, positional, strict=False):
        bound[name] = value
    if len(positional) > len(allpos) and signature.vararg is None:
        errors.append("too_many_positional")
    seen: set[str] = set()
    kwonly = {argument.arg for argument in signature.kwonlyargs}
    for name, value in keywords:
        if name in seen:
            errors.append("duplicate_keyword:" + name)
        seen.add(name)
        if name in posonly:
            if signature.kwarg is None:
                errors.append("positional_only_as_keyword:" + name)
            else:
                extras[name] = value
        elif name in ordinary or name in kwonly:
            if name in bound:
                errors.append("multiple_values:" + name)
            bound[name] = value
        elif signature.kwarg is not None:
            extras[name] = value
        else:
            errors.append("unexpected_keyword:" + name)
    default_start = len(allpos) - len(positional_defaults)
    for index, name in enumerate(allpos):
        if name not in bound:
            if index >= default_start:
                bound[name] = positional_defaults[index - default_start]
            else:
                errors.append("missing_positional:" + name)
    # Retain existing set iteration; call ordering is a separate semantic task.
    for name in kwonly:
        if name not in bound:
            if name in keyword_defaults:
                bound[name] = keyword_defaults[name]
            else:
                errors.append("missing_keyword_only:" + name)
    return BindingResult(
        tuple(bound.items()), positional[len(allpos) :], tuple(extras.items()), tuple(errors)
    )
