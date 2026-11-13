"""Runtime frame attachments have a finite policy projection and retain ownership."""

from __future__ import annotations

from dataclasses import replace

from pythonstan.analysis.contracts.identity import CellId, ContextId, FrameId, ObjectId
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.policies.region import EffectiveRegionPolicy, RegionExecutionContext
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.metadata import GeneratorMeta, Metadata
from pythonstan.analysis.state.store import Frame, HeapObject, Store
from pythonstan.analysis.state.suspensions import suspension_states
from pythonstan.analysis.state.transform import (
    PayloadTransformer,
    detached_payload,
    payload_signature,
    walk_graph,
)


def test_frame_execution_projects_policy_and_preserves_arena_attachment() -> None:
    first = RegionExecutionContext(
        EffectiveRegionPolicy.from_abstract({"context_depth": 1}), ValueOperations()
    )
    other = RegionExecutionContext(
        EffectiveRegionPolicy.from_abstract({"context_depth": 3}), ValueOperations()
    )
    first.ops.arena.publish(
        first.ops.arena.prepare({ObjectId("host-only"): ObjectId("host-summary")})
    )
    frame = Frame("m", "f", ContextId(()), FrameId("frame"), execution=first)
    root = Store(cells={CellId("x"): Value.ref(ObjectId("A"))})
    root.heap[ObjectId("A")] = HeapObject(
        "generator",
        metadata=Metadata.from_record(
            "generator", GeneratorMeta(frame=frame, entry=StateHandle.capture(root))
        ),
    )
    copied = detached_payload(root)
    copied_frame = copied.heap[ObjectId("A")].metadata["frame"]
    assert copied_frame.execution is first
    assert copied_frame.execution.ops.arena is first.ops.arena
    assert copied_frame.execution.ops.arena is not other.ops.arena
    values: list[Value] = []
    walk_graph(copied, on_value=values.append)
    assert all(ObjectId("host-only") not in value.refs for value in values)
    quotient = PayloadTransformer({ObjectId("A"): ObjectId("S")}, other.ops).visit(root)
    mapped_frame = quotient.heap[ObjectId("S")].metadata["frame"]
    assert mapped_frame.execution is first
    assert first.ops.arena.aliases == {ObjectId("host-only"): ObjectId("host-summary")}
    assert other.ops.arena.aliases == {}
    assert payload_signature(frame) != payload_signature(replace(frame, execution=other))


def test_standalone_join_preanchors_cross_reachable_source_owners() -> None:
    shared = Store()
    owners = [
        HeapObject(
            "generator",
            metadata=Metadata.from_record(
                "generator",
                GeneratorMeta(
                    frame=Frame("m", "f", ContextId(()), FrameId(name)),
                    entry=StateHandle.capture(shared),
                    stage="created",
                ),
            ),
        )
        for name in ("left", "right")
    ]
    shared.heap = {ObjectId("left"): owners[0], ObjectId("right"): owners[1]}
    joined = owners[0].join(owners[1], ValueOperations())
    assert isinstance(joined.metadata.record, GeneratorMeta)
    states = suspension_states(joined.metadata.record)
    roots = [state.entry.read() for state in states if state.entry is not None]
    assert len(roots) == 2 and roots[0] is roots[1]
    assert roots[0] is not shared
    assert roots[0].heap[ObjectId("left")] is joined
    assert roots[0].heap[ObjectId("right")] is joined
    assert shared.heap[ObjectId("left")] is owners[0]
    assert shared.heap[ObjectId("right")] is owners[1]
