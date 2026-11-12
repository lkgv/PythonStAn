"""Explicit full-store boundaries between abstract domains and inclusion regions."""

from __future__ import annotations

from dataclasses import replace

from pythonstan.analysis.domains.ints import IntLattice
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.state.boundary import BoundaryObject, BoundaryState
from pythonstan.analysis.state.flow import Flow, FlowTail, flow_payload, make_completion
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.store import HeapObject, Store
from pythonstan.analysis.state.transform import PayloadTransformer, detached_payload
from pythonstan.analysis.state.transform import semantic_payload_equal as payload_equal


class FullStateAdapter:
    def encode(self, flow: Flow) -> BoundaryState:
        copied = detached_payload(flow)
        state = copied.store
        heap = {
            identifier: BoundaryObject(
                obj.kind,
                obj.fields,
                obj.default,
                obj.maybe_missing,
                obj.singleton,
                obj.class_ids,
                obj.metadata,
                obj.havoc,
                obj,
            )
            for identifier, obj in state.heap.items()
        }
        return BoundaryState(
            cells=state.cells,
            heap=heap,
            initialized=state.initialized,
            writes=state.writes,
            events=state.events,
            obligations=state.obligations,
            completion=copied.kind,
            value=flow_payload(copied),
            exception=copied.exception,
            continuation=copied.continuation,
            cleanups=copied.cleanups,
            resume_target=copied.resume_target,
            havoc_namespaces=state.havoc_namespaces,
            havoc_imports=state.havoc_imports,
            guards=state.guards,
            identity_epoch=state.identity_epoch,
            _store_root=state,
            state_revision=state.state_revision,
        )

    def decode(self, boundary: BoundaryState) -> Flow:
        copied = detached_payload(boundary)
        state = copied._store_root if copied._store_root is not None else Store()
        heap = {}
        for identifier, obj in copied.heap.items():
            runtime = obj._runtime_root if obj._runtime_root is not None else HeapObject(obj.kind)
            runtime.kind = obj.kind
            runtime.fields = obj.fields
            runtime.default = obj.default
            runtime.maybe_missing = obj.maybe_missing
            runtime.singleton = obj.singleton
            runtime.class_ids = obj.class_ids
            runtime.metadata = obj.metadata
            runtime.havoc = obj.havoc
            heap[identifier] = runtime
        state.cells = copied.cells
        state.heap.clear()
        state.heap.update(heap)
        state.initialized = copied.initialized
        state.writes = copied.writes
        state.events = copied.events
        state.obligations = copied.obligations
        state.havoc_namespaces = copied.havoc_namespaces
        state.havoc_imports = copied.havoc_imports
        state.guards = copied.guards
        state.identity_epoch = copied.identity_epoch
        state.state_revision = copied.state_revision
        return make_completion(
            StateHandle.capture(state),
            copied.completion,
            copied.value,
            copied.exception,
            FlowTail(copied.continuation, copied.cleanups, copied.resume_target),
        )

    def check_roundtrip(self, flow: Flow, ops: ValueOperations) -> bool:
        decoded = self.decode(self.encode(flow))
        return (
            type(flow) is type(decoded)
            and flow.exception == decoded.exception
            and payload_equal(flow.store, decoded.store)
            and payload_equal(flow.continuation, decoded.continuation)
            and payload_equal(flow.cleanups, decoded.cleanups)
            and payload_equal(flow.resume_target, decoded.resume_target)
            and flow_payload(flow) == flow_payload(decoded)
        )


def reabstract_value(value: Value, operations: ValueOperations) -> Value:
    integer = value.ints
    if integer.consts and len(integer.consts) > operations.policy.max_const_ints:
        integer = IntLattice.from_interval(min(integer.consts), max(integer.consts), integer.parity)
    string = operations.string.widen(value.strings, value.strings)
    return replace(value, ints=integer, strings=string)


def reabstract_state(flow: Flow, operations: ValueOperations) -> Flow:
    transform = PayloadTransformer(
        operations.arena.aliases,
        operations,
        scalar=lambda value: reabstract_value(value, operations),
        epoch=operations.arena.epoch,
    )
    return transform.visit(flow)


__all__ = [
    "BoundaryObject",
    "BoundaryState",
    "FullStateAdapter",
    "reabstract_state",
    "reabstract_value",
]
