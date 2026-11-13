"""Nominal class identity remains authoritative alongside structural heap kinds."""

from __future__ import annotations

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.identity import FrameId, ObjectId
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.transfer import Transfer
from pythonstan.analysis.state.flow import require_payload
from pythonstan.analysis.state.store import Frame, HeapObject, Store

from .domain_oracles import require_members
from .test_sem05_resume2 import concrete


@pytest.mark.parametrize("kind", ["list", "dict"])
def test_structural_container_kind_keeps_nominal_source_subclass_identity(kind: str) -> None:
    assert concrete(f"class C({kind}): pass\nresult=isinstance(C(),C)\n") == [True, None]
    driver = InterproceduralDriver([])
    transfer = Transfer(driver)
    clsid, instance = ObjectId("class:C"), ObjectId("instance:C")
    store = Store(
        heap={
            clsid: HeapObject(
                "class",
                metadata={
                    "bases": (ObjectId("builtin:" + kind),),
                    "mro": (ObjectId("builtin:" + kind),),
                },
            ),
            instance: HeapObject(kind, class_ids=frozenset({clsid}), metadata={"length": 0}),
        }
    )
    frame = Frame("subject", "subject", (), FrameId("direct-classinfo"), is_module=True)
    flows = transfer.builtin(
        "isinstance", [Value.ref(instance), Value.ref(clsid)], (), store, frame, None
    )
    assert flows and all(out.kind == "normal" for out in flows)
    assert all(require_payload(out).bools == frozenset({True}) for out in flows)


def test_context_zero_distinct_classes_are_not_proved_identical() -> None:
    source = (
        "def make():\n class C: pass\n return C\nA=make()\nB=make()\nresult=isinstance(A(),B)\n"
    )
    assert concrete(source) == [False, None]
    query = ValueQuery("subject", "result")
    result = analyze(
        Source(source, module="subject"),
        config={
            "engine": "ai_primary",
            "abstract": {"context_depth": 0},
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=[query],
    )
    answer = result.query(query)
    assert answer.status == "answered" and answer.value is not None
    require_members(answer.value, [False])
