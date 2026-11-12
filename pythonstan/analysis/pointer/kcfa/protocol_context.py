"""An explicit, opt-in finite context dimension for keyword mapping calls.

The token contains source identities and a bounded key bucket, never a Scope,
receiver object, or another context. Base sensitivity remains independently
selected. Replacement is flat; summary tags remain the outermost wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeGuard, TypeVar, overload, override

from pythonstan.ir.ir_statements import IRStatement

from .context import (
    AbstractContext,
    CallSite,
    NativeContext,
    SummaryContext,
    is_native_context,
    is_summary_context,
)
from .object import AllocKind, AllocSite


@dataclass(frozen=True)
class KeywordPartition:
    operation: CallSite
    receiver_source: AllocSite | None
    key_kind: str
    key_value: str | None

    def __post_init__(self) -> None:
        if (
            type(self.operation) is not CallSite
            or not isinstance(self.operation.statement, IRStatement)
            or self.operation.index is not None
            and type(self.operation.index) is not int
            or self.operation.scope_name is not None
            and type(self.operation.scope_name) is not str
            or self.receiver_source is not None
            and (
                type(self.receiver_source) is not AllocSite
                or not isinstance(self.receiver_source.stmt, IRStatement)
                or not isinstance(self.receiver_source.kind, AllocKind)
            )
            or type(self.key_kind) is not str
            or self.key_kind not in {"exact", "summary"}
            or self.key_kind == "exact"
            and self.receiver_source is None
            or self.key_kind == "exact"
            and type(self.key_value) is not str
            or self.key_kind == "summary"
            and self.key_value is not None
        ):
            raise ValueError("Invalid keyword protocol partition")


ProtocolItemT = TypeVar("ProtocolItemT")


@dataclass(frozen=True)
class ProtocolKeyContext(AbstractContext[ProtocolItemT]):
    inner: AbstractContext[ProtocolItemT]
    partition: KeywordPartition

    @override
    def __hash__(self) -> int:
        return hash((self.inner, self.partition))

    @override
    def __post_init__(self) -> None:
        if (
            not isinstance(self.inner, AbstractContext)
            or isinstance(self.inner, (ProtocolKeyContext, SummaryContext))
            or type(self.partition) is not KeywordPartition
        ):
            raise ValueError(
                "ProtocolKeyContext requires one flat partition and an unwrapped base context"
            )
        super().__post_init__()

    @override
    def to_string(self) -> str:
        return f"K[{self.inner.to_string()};{self.partition.key_kind}]"

    @override
    def is_empty(self) -> bool:
        return False

    def append(self, *args, **kwargs):
        return apply_protocol_partition(self.inner.append(*args, **kwargs), self.partition)


def is_protocol_context[ItemT](
    context: AbstractContext[ItemT],
) -> TypeGuard[ProtocolKeyContext[ItemT]]:
    return isinstance(context, ProtocolKeyContext)


@overload
def split_protocol_context[ItemT](
    context: AbstractContext[ItemT],
) -> tuple[AbstractContext[ItemT], KeywordPartition | None]: ...


@overload
def split_protocol_context(context: None) -> tuple[None, None]: ...


def split_protocol_context[ItemT](
    context: AbstractContext[ItemT] | None,
) -> tuple[AbstractContext[ItemT] | None, KeywordPartition | None]:
    """Return the base context (with Summary outer intact) and flat token."""
    if context is None:
        return None, None
    if is_summary_context(context):
        inner, partition = split_protocol_context(context.inner)
        return (context if inner is context.inner else SummaryContext(inner)), partition
    if is_protocol_context(context):
        base, _ = split_protocol_context(context.inner)
        return base, context.partition
    return context, None


def apply_protocol_partition[ItemT](
    context: AbstractContext[ItemT], partition: KeywordPartition
) -> AbstractContext[ItemT]:
    """Replace, never nest, a local token while preserving an outer summary."""
    base, _ = split_protocol_context(context)
    if is_summary_context(base):
        if base.inner is None:
            raise ValueError(
                "ProtocolKeyContext requires one flat partition and an unwrapped base context"
            )
        return SummaryContext(apply_protocol_partition(base.inner, partition))
    return ProtocolKeyContext(base, partition)


@overload
def split_native_protocol_context(
    context: NativeContext,
) -> tuple[NativeContext, KeywordPartition | None]: ...


@overload
def split_native_protocol_context(context: None) -> tuple[None, None]: ...


def split_native_protocol_context(
    context: NativeContext | None,
) -> tuple[NativeContext | None, KeywordPartition | None]:
    """Validate a general split result before exporting the Native profile."""
    base, partition = split_protocol_context(context)
    if base is None:
        if context is not None:
            raise ValueError("Nonnull Native context produced an absent base")
        return None, None
    if is_native_context(base):
        return base, partition
    raise ValueError("Underlying context is outside the registered Native profile")


def apply_native_protocol_partition(
    context: NativeContext, partition: KeywordPartition
) -> NativeContext:
    """Apply the canonical generic operation and check the emitted profile."""
    result = apply_protocol_partition(context, partition)
    if is_native_context(result):
        return result
    raise ValueError("Result context is outside the registered Native profile")
