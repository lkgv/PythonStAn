"""Finite admitted default projections retain valid classlike input worlds."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.identity import FrameId, ObjectId, SourceModule
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.transfer import Transfer
from pythonstan.analysis.state.flow import require_payload
from pythonstan.analysis.state.store import Frame, HeapObject, Store


def fixture_state(
    kind: str, *, explicit: bool, tuple_default: bool
) -> tuple[Transfer, Store, Frame, ObjectId]:
    driver = InterproceduralDriver([SourceModule("subject", "subject.py", "pass\n")])
    transfer = Transfer(driver)
    subject, bases, cls = (
        ObjectId("default:subject"),
        ObjectId("default:bases"),
        ObjectId("class:Proxy"),
    )
    fields = {"__bases__": Value.ref(bases)} if explicit else {}
    store = Store(
        heap={
            cls: HeapObject("class"),
            subject: HeapObject(
                kind,
                fields=fields,
                default=Value.ref(bases) if tuple_default else Value.constant(42),
                class_ids=frozenset({cls}) if kind == "instance" else frozenset(),
            ),
            bases: HeapObject(
                "tuple", fields={0: Value.ref(ObjectId("builtin:int"))}, metadata={"length": 1}
            ),
        }
    )
    frame = Frame("subject", "subject", (), FrameId("default-bases"), is_module=True)
    return transfer, store, frame, subject


@pytest.mark.parametrize("kind", ["function", "instance"])
def test_unspecified_default_key_covers_normal_and_missing_bases(kind: str) -> None:
    transfer, store, frame, subject = fixture_state(kind, explicit=False, tuple_default=True)
    slot, missing = store.read_slot(subject, "__bases__", transfer.ops)
    assert missing and ObjectId("default:bases") in slot.refs
    flows = transfer.builtin(
        "issubclass",
        [Value.ref(subject), Value.ref(ObjectId("builtin:int"))],
        (),
        store,
        frame,
        None,
    )
    assert any(out.kind == "normal" and True in require_payload(out).bools for out in flows)
    assert any(
        out.kind == "raise" and out.exception in {"AnyException", "TypeError"} for out in flows
    )
    assert not store.heap[subject].havoc


@pytest.mark.parametrize("kind", ["function", "instance"])
def test_explicit_valid_bases_remain_precise_with_an_unrelated_default(kind: str) -> None:
    transfer, store, frame, subject = fixture_state(kind, explicit=True, tuple_default=False)
    flows = transfer.builtin(
        "issubclass",
        [Value.ref(subject), Value.ref(ObjectId("builtin:int"))],
        (),
        store,
        frame,
        None,
    )
    assert flows and all(out.kind == "normal" for out in flows)
    assert all(require_payload(out).bools == frozenset({True}) for out in flows)


@pytest.mark.parametrize("kind", ["function", "instance"])
def test_non_tuple_default_retains_possible_type_error(kind: str) -> None:
    transfer, store, frame, subject = fixture_state(kind, explicit=False, tuple_default=False)
    flows = transfer.builtin(
        "issubclass",
        [Value.ref(subject), Value.ref(ObjectId("builtin:int"))],
        (),
        store,
        frame,
        None,
    )
    assert any(
        out.kind == "raise" and out.exception in {"AnyException", "TypeError"} for out in flows
    )
