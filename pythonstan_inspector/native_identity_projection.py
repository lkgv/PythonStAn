"""Audited native identity fields exported without changing native identity.

Only the adapter imports this module. The offline recipe module has no engine
dependency. No formatter, source reconstruction, or arbitrary object traversal
is used here.
"""

from __future__ import annotations

import math

from pythonstan.analysis.pointer.kcfa.context import (
    AbstractContext,
    CallSite,
    Ctx,
    Scope,
    SummaryContext,
)
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocSite, BuiltinMethodObject
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
from pythonstan.analysis.pointer.kcfa.protocol_context import KeywordPartition, ProtocolKeyContext
from pythonstan.analysis.pointer.kcfa.variable import FieldAccess, Variable, VariableKind
from pythonstan.ir.ir_statements import IRScope, IRStatement

from .capture_dto import EntityDescription, EntityLinkProjection
from .identity import CONTEXT_FIELDS, MODEL, OBJECT_FIELDS, SOURCE_CLASSES


def project_identity(
    projection: EntityLinkProjection, record: EntityDescription, obj: object
) -> None:
    attrs = record["attributes"]
    attrs["identity_model"] = MODEL
    attrs["identity_coverage"] = "complete"
    cls = type(obj).__name__

    def unsupported() -> None:
        attrs["identity_coverage"] = "unsupported"

    def scalar(name: str, value: object) -> None:
        valid = (
            value is None
            or type(value) is bool
            or type(value) is int
            and -(2**63) <= value < 2**63
            or type(value) is float
            and math.isfinite(value)
            or type(value) is str
            and len(value) <= 512
        )
        if valid and type(value) is str:
            try:
                value.encode("utf-8")
            except UnicodeError:
                valid = False
        if valid and (value is None or isinstance(value, (bool, int, float, str))):
            attrs[name] = value
        else:
            unsupported()

    def link(name: str, value: object, optional: bool = False) -> None:
        if optional:
            attrs[name + "_is_none"] = value is None
            if value is None:
                return
        projection.link(record, name, value)

    if type(obj) is NormalNode:
        return
    if type(obj) is Ctx:
        if type(obj.content) is Variable:
            attrs["content_kind"] = "variable"
        elif type(obj.content) not in (FieldAccess, CallSite):
            unsupported()
        return
    if type(obj) is Scope:
        link("object", obj.obj)
        return
    if (
        isinstance(obj, AbstractObject)
        and cls in OBJECT_FIELDS
        and type(obj).__module__ == AbstractObject.__module__
    ):
        raw: dict[str, object] = vars(obj)
        scalars, links, optional_fields = OBJECT_FIELDS[cls]
        for name in scalars:
            scalar(name, raw[name])
        for name in links:
            link(name, raw[name])
        for name in optional_fields:
            link(name, raw[name], optional=True)
        if isinstance(obj, BuiltinMethodObject):
            variable = obj.receiver_var
            attrs["receiver_var_is_none"] = variable is None
            if variable is not None:
                if type(variable) is not Variable:
                    unsupported()
                else:
                    scalar("receiver_var_name", variable.name)
                    scalar("receiver_var_kind", variable.kind.value)
                    attrs["receiver_var_name_coverage"] = (
                        "exact"
                        if variable.kind != VariableKind.TEMPORARY
                        and variable.name.isidentifier()
                        and len(variable.name) <= 512
                        else "opaque"
                    )
        return
    if (
        isinstance(obj, AbstractContext)
        and cls in CONTEXT_FIELDS
        and type(obj).__module__ == AbstractContext.__module__
    ):
        raw_context: dict[str, object] = vars(obj)
        fields, sequences = CONTEXT_FIELDS[cls]
        for name in fields:
            scalar(name, raw_context[name])
        for sequence in sequences:
            values = raw_context[sequence]
            if type(values) is not tuple or len(values) > 64:
                unsupported()
                continue
            items: tuple[object, ...] = values
            for index, value in enumerate(items):
                name = f"{sequence}:{index}"
                attrs[name + "_is_tuple"] = type(value) is tuple
                if type(value) is tuple:
                    attrs[name + "_length"] = len(value)
                    if len(value) > 64:
                        unsupported()
        return
    if type(obj) in (SummaryContext, ProtocolKeyContext):
        return
    if type(obj) is KeywordPartition:
        link("receiver_source", obj.receiver_source, optional=True)
        return
    if type(obj) is CallSite:
        scalar("scope_name", obj.scope_name)
        scalar("index", obj.index)
        return
    if type(obj) is AllocSite:
        if not isinstance(obj.stmt, IRStatement):
            unsupported()
        return
    if (
        isinstance(obj, (IRScope, IRStatement))
        and cls in SOURCE_CLASSES
        and type(obj).__module__ == IRStatement.__module__
    ):
        return
    unsupported()
