"""An explicit, opt-in finite context dimension for keyword mapping calls.

The token contains source identities and a bounded key bucket, never a Scope,
receiver object, or another context. Base sensitivity remains independently
selected. Replacement is flat; summary tags remain the outermost wrapper.
"""
from dataclasses import dataclass
from typing import Optional
from pythonstan.ir.ir_statements import IRStatement

from .context import AbstractContext, CallSite, SummaryContext
from .object import AllocKind, AllocSite


@dataclass(frozen=True)
class KeywordPartition:
    operation: CallSite
    receiver_source: Optional[AllocSite]
    key_kind: str
    key_value: Optional[str]

    def __post_init__(self):
        if (type(self.operation) is not CallSite
                or not isinstance(self.operation.statement, IRStatement)
                or self.operation.index is not None and type(self.operation.index) is not int
                or self.operation.scope_name is not None and type(self.operation.scope_name) is not str
                or self.receiver_source is not None and (
                    type(self.receiver_source) is not AllocSite
                    or not isinstance(self.receiver_source.stmt, IRStatement)
                    or not isinstance(self.receiver_source.kind, AllocKind))
                or type(self.key_kind) is not str or self.key_kind not in {"exact", "summary"}
                or self.key_kind == "exact" and self.receiver_source is None
                or self.key_kind == "exact" and type(self.key_value) is not str
                or self.key_kind == "summary" and self.key_value is not None):
            raise ValueError("Invalid keyword protocol partition")


@dataclass(frozen=True)
class ProtocolKeyContext(AbstractContext):
    inner: AbstractContext
    partition: KeywordPartition

    def __post_init__(self):
        if (not isinstance(self.inner, AbstractContext)
                or isinstance(self.inner, (ProtocolKeyContext, SummaryContext))
                or type(self.partition) is not KeywordPartition):
            raise ValueError("ProtocolKeyContext requires one flat partition and an unwrapped base context")
        super().__post_init__()

    def to_string(self):
        return f"K[{self.inner.to_string()};{self.partition.key_kind}]"

    def is_empty(self):
        return False

    def append(self, *args, **kwargs):
        return apply_protocol_partition(self.inner.append(*args, **kwargs), self.partition)


def split_protocol_context(context):
    """Return the base context (with Summary outer intact) and flat token."""
    if isinstance(context, SummaryContext):
        inner, partition = split_protocol_context(context.inner)
        return (context if inner is context.inner else SummaryContext(inner)), partition
    if isinstance(context, ProtocolKeyContext):
        base, _ = split_protocol_context(context.inner)
        return base, context.partition
    return context, None


def apply_protocol_partition(context, partition):
    """Replace, never nest, a local token while preserving an outer summary."""
    base, _ = split_protocol_context(context)
    if isinstance(base, SummaryContext):
        return SummaryContext(apply_protocol_partition(base.inner, partition))
    return ProtocolKeyContext(base, partition)
