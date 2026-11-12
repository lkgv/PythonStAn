"""Detached inspector views retain alternatives without repairing exported facts."""

from __future__ import annotations

import pytest

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_array,
    require_object,
)
from pythonstan_inspector.refactor_adapter import inspect_result


def snapshot() -> JsonObject:
    return {
        "values": {"subject": {"result": {"constants": ["exported"]}}},
        "events": {
            "events": [
                {
                    "event_id": "input",
                    "kind": "name_read",
                    "sequence": 0,
                    "parents": [],
                    "payload": {},
                },
                {
                    "event_id": "old",
                    "kind": "name_write",
                    "unit": "u",
                    "revision": 1,
                    "sequence": 1,
                    "parents": ["input"],
                    "payload": {"cell": "global:subject:result"},
                },
                {
                    "event_id": "left",
                    "kind": "name_write",
                    "unit": "u",
                    "revision": 2,
                    "sequence": 2,
                    "parents": ["input"],
                    "payload": {"cell": "global:subject:result", "value": "observed-left"},
                },
                {
                    "event_id": "right",
                    "kind": "name_write",
                    "unit": "u",
                    "revision": 2,
                    "sequence": 3,
                    "parents": ["input", "missing"],
                    "payload": {"cell": "global:subject:result", "value": "observed-right"},
                },
            ]
        },
    }


def test_latest_revision_keeps_all_branches_and_reports_missing_causality() -> None:
    inspector = inspect_result(snapshot())
    explanation = inspector.explain(module="subject", symbol="result")
    projection = require_object(explanation["projection"])
    assert projection["producer_events"] == ["left", "right"]
    assert require_object(projection["value"])["constants"] == ["exported"]
    assert explanation["claim"] == "diagnostic_observation_only"
    assert explanation["missing_parents"] == ["missing"] and explanation["truncated"]
    chain = require_array(explanation["chain"])
    assert [require_object(event)["event_id"] for event in chain] == ["input", "left", "right"]


def test_caller_and_returned_view_mutations_cannot_change_retained_snapshot() -> None:
    original = snapshot()
    inspector = inspect_result(original)
    require_object(original["values"]).clear()
    first = inspector.events(kind="name_write")
    first[0].clear()
    detached = inspector.snapshot()
    require_object(detached["events"]).clear()
    assert len(inspector.events(kind="name_write")) == 3
    assert inspector.explain(module="subject", symbol="result")["status"] == "observed_chain"
    assert inspector.explain(module="subject", symbol="result", max_events=0)["truncated"]


def test_legacy_array_event_shape_and_invalid_raw_payload_boundary() -> None:
    data = snapshot()
    data["events"] = require_object(data["events"])["events"]
    assert len(inspect_result(data).events(unit="u")) == 3
    with pytest.raises(TypeError):
        inspect_result({"events": [object()]})
    malformed: JsonObject = {"events": [{"event_id": [], "kind": "name_write"}]}
    with pytest.raises(TypeError, match="identity"):
        inspect_result(malformed).explain(module="subject", symbol="result")
