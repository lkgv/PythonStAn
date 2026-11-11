"""Independent finite protocol obligations frozen before static predictions."""
import hashlib
import json
from pathlib import Path

import pytest

from tests.round12_semantics.test_descriptor_native import _analyze, _edges


ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / "obligations.json").read_text())
AMENDMENT = json.loads((ROOT / "channel_amendment_v1.json").read_text())


def _source(case):
    data = (ROOT / case["source"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == case["source_sha256"]
    return data.decode()


def _values(bundle, scope, name):
    snapshot = bundle.data["snapshot"]
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    owners = {entity["id"] for entity in entities.values()
              if entity["kind"] == "ctx" and entity["attributes"].get("name") == name
              and entity["origin"].get("scope") == scope}
    return {entities[row["object"]]["attributes"]["value"]
            for row in snapshot["views"]["ctx_points"]["rows"]
            if row["owner"] in owners and entities[row["object"]]["attributes"].get("engine_class") == "ConstantObject"
            and "value" in entities[row["object"]]["attributes"]}


def _concrete(case, source):
    namespace = {}
    exec(compile(source, "<trusted-round13-protocol-witness>", "exec"), namespace)
    if case["id"] == "sync_bare_yield":
        assert namespace["item"] is None
    elif case["id"] == "async_step_intermediate":
        assert namespace["token"] is namespace["suspension_callback"]
        assert namespace["events"] == ["suspension"]
        # Cleanup drives the already-owned finite objects only. This is outside
        # the frozen prediction source and does not supply analyzer targets.
        try:
            namespace["step"].send(None)
        except StopIteration as done:
            assert done.value is namespace["item_callback"]
        try:
            namespace["generator"].aclose().send(None)
        except StopIteration:
            pass
    elif case["id"] == "custom_await_completion":
        assert namespace["events"] == ["completed"]
    elif case["id"] == "async_bare_yield":
        assert namespace["pending"].cr_frame is None
    elif case["id"] in {"coroutine_send_intermediate", "coroutine_wrapper_intermediate"}:
        assert namespace["token"] is namespace["suspension_callback"]
        assert namespace["events"] == ["suspension"]
        driver = namespace.get("iterator", namespace["pending"])
        try:
            driver.send(None)
        except StopIteration as done:
            assert done.value is namespace["completed_callback"]
        assert namespace["pending"].cr_frame is None


@pytest.mark.parametrize("case", MANIFEST["cases"] + AMENDMENT["cases"], ids=lambda case: case["id"])
def test_frozen_native_protocol_value_and_call_obligations(tmp_path, case):
    source = _source(case)
    _concrete(case, source)
    bundle = _analyze(tmp_path, source, context_policy="2-cfa")
    for expected in case["required_values"]:
        actual = _values(bundle, expected["scope"], expected["name"])
        assert expected["value"] in actual, {"case": case["id"], "required": expected, "actual": actual}
    calls = _edges(bundle)
    required = {tuple(pair) for pair in case["required_edges"]}
    forbidden = {tuple(pair) for pair in case["forbidden_edges"]}
    assert required <= calls, {"case": case["id"], "missing_required": sorted(required - calls)}
    assert not forbidden.intersection(calls), {"case": case["id"], "wrong_channel_calls": sorted(forbidden.intersection(calls))}
