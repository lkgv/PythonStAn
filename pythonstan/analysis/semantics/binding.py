"""Shared, non-executing Python argument-binding plan.

The binder receives already evaluated argument values. It does not evaluate
source, import modules, or manufacture a receiver; callers supply the receiver
as the first positional argument when descriptor resolution binds one.
"""
from __future__ import annotations
import ast
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class BindingResult:
    bindings: tuple[tuple[str,Any],...]
    varargs: tuple[Any,...] = ()
    kwargs: tuple[tuple[str,Any],...] = ()
    errors: tuple[str,...] = ()


def bind_arguments(signature: ast.arguments, positional, keywords, defaults=(), kw_defaults=()):
    positional=tuple(positional); keywords=tuple(keywords); defaults=tuple(defaults);kw_defaults=dict(kw_defaults)
    posonly=[x.arg for x in signature.posonlyargs]
    ordinary=[x.arg for x in signature.args]
    allpos=posonly+ordinary
    bound={};extras={};errors=[]
    for name,value in zip(allpos,positional): bound[name]=value
    if len(positional)>len(allpos) and signature.vararg is None: errors.append("too_many_positional")
    seen=set()
    kwonly={x.arg for x in signature.kwonlyargs}
    for name,value in keywords:
        if name in seen: errors.append("duplicate_keyword:"+name)
        seen.add(name)
        if name in posonly:
            if signature.kwarg is None: errors.append("positional_only_as_keyword:"+name)
            else: extras[name]=value
        elif name in ordinary or name in kwonly:
            if name in bound: errors.append("multiple_values:"+name)
            bound[name]=value
        elif signature.kwarg is not None: extras[name]=value
        else: errors.append("unexpected_keyword:"+name)
    default_start=len(allpos)-len(defaults)
    for index,name in enumerate(allpos):
        if name not in bound:
            if index>=default_start: bound[name]=defaults[index-default_start]
            else: errors.append("missing_positional:"+name)
    for name in kwonly:
        if name not in bound:
            if name in kw_defaults: bound[name]=kw_defaults[name]
            else: errors.append("missing_keyword_only:"+name)
    return BindingResult(tuple(bound.items()),positional[len(allpos):],tuple(extras.items()),tuple(errors))
