"""Audited native identity fields exported without changing native identity.

Only the adapter imports this module. The offline recipe module has no engine
dependency. No formatter, source reconstruction, or arbitrary object traversal
is used here.
"""
import math

from .identity import MODEL, OBJECT_FIELDS, CONTEXT_FIELDS, SOURCE_CLASSES
from pythonstan.analysis.pointer.kcfa.context import Ctx, Scope, CallSite
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocSite
from pythonstan.analysis.pointer.kcfa.variable import Variable, FieldAccess, VariableKind
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
from pythonstan.analysis.pointer.kcfa.protocol_context import KeywordPartition, ProtocolKeyContext
from pythonstan.analysis.pointer.kcfa.context import AbstractContext, SummaryContext
from pythonstan.ir.ir_statements import IRScope, IRStatement


def project_identity(projection, record, obj):
    attrs = record["attributes"]
    attrs["identity_model"] = MODEL
    attrs["identity_coverage"] = "complete"
    cls = type(obj).__name__

    def unsupported():
        attrs["identity_coverage"] = "unsupported"

    def scalar(name, value):
        valid = (value is None or type(value) is bool
                 or type(value) is int and -(2**63) <= value < 2**63
                 or type(value) is float and math.isfinite(value)
                 or type(value) is str and len(value) <= 512)
        if valid and type(value) is str:
            try:
                value.encode("utf-8")
            except UnicodeError:
                valid = False
        if valid:
            attrs[name] = value
        else:
            unsupported()

    def link(name, value, optional=False):
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
    if isinstance(obj, AbstractObject) and cls in OBJECT_FIELDS and type(obj).__module__ == AbstractObject.__module__:
        scalars, links, optional = OBJECT_FIELDS[cls]
        for name in scalars:
            scalar(name, vars(obj)[name])
        for name in links:
            link(name, vars(obj)[name])
        for name in optional:
            link(name, vars(obj)[name], optional=True)
        if cls == "BuiltinMethodObject":
            variable = obj.receiver_var
            attrs["receiver_var_is_none"] = variable is None
            if variable is not None:
                if type(variable) is not Variable:
                    unsupported()
                else:
                    scalar("receiver_var_name", variable.name)
                    scalar("receiver_var_kind", variable.kind.value)
                    attrs["receiver_var_name_coverage"] = (
                        "exact" if variable.kind != VariableKind.TEMPORARY and variable.name.isidentifier()
                        and len(variable.name) <= 512 else "opaque")
        return
    if isinstance(obj, AbstractContext) and cls in CONTEXT_FIELDS and type(obj).__module__ == AbstractContext.__module__:
        fields, sequences = CONTEXT_FIELDS[cls]
        for name in fields:
            scalar(name, vars(obj)[name])
        for sequence in sequences:
            values = vars(obj)[sequence]
            if type(values) is not tuple or len(values) > 64:
                unsupported()
                continue
            for index, value in enumerate(values):
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
    if isinstance(obj, (IRScope, IRStatement)) and cls in SOURCE_CLASSES and type(obj).__module__ == IRStatement.__module__:
        return
    unsupported()
