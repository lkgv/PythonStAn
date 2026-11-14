"""Duck-class transfer honors possible __bases__ in an admitted default slot."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.identity import FrameId, ObjectId, SourceModule
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.transfer import Transfer
from pythonstan.analysis.state.flow import require_payload
from pythonstan.analysis.state.store import Frame, HeapObject, Store


@pytest.mark.parametrize(
    "call,subject,target,expected",
    [
        ("issubclass", "proxy", "int", True),
        ("issubclass", "bool", "proxy", False),
        ("isinstance", "one", "proxy", False),
    ],
)
def test_default_slot_permits_a_normal_duck_bases_witness(
    call: str, subject: str, target: str, expected: bool
) -> None:
    driver = InterproceduralDriver([SourceModule("subject", "subject.py", "pass\n")])
    transfer = Transfer(driver)
    proxy, bases = ObjectId("review:function"), ObjectId("review:bases")
    store = Store(
        heap={
            proxy: HeapObject("function", default=Value.ref(bases)),
            bases: HeapObject(
                "tuple", fields={0: Value.ref("builtin:int")}, metadata={"length": 1}
            ),
        }
    )
    value, maybe_missing = store.read_slot(proxy, "__bases__", transfer.ops)
    assert maybe_missing and bases in value.refs
    values = {
        "proxy": Value.ref(proxy),
        "int": Value.ref("builtin:int"),
        "bool": Value.ref("builtin:bool"),
        "one": Value.constant(1),
    }
    frame = Frame("subject", "subject", (), FrameId("review-defaults"), is_module=True)
    flows = transfer.builtin(call, [values[subject], values[target]], (), store, frame, None)
    normals = [require_payload(flow) for flow in flows if flow.kind == "normal"]
    assert any(expected in result.bools or result.top for result in normals), [
        (
            flow.kind,
            flow.exception,
            None if flow.kind != "normal" else require_payload(flow).export(),
        )
        for flow in flows
    ]
