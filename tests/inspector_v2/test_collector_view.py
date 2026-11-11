"""Independent calibration of actual native inputs and passive model capture."""
import hashlib

import pytest

from pythonstan_inspector.adapter import capture
from pythonstan_inspector.contract import CaptureRequest, canonical_bytes
from pythonstan_inspector.storage import load_bundle, write_bundle
from tests.inspector.test_adapter import _native_fingerprint, _solver


_VIEWS = ("node_points", "ctx_points", "pfg_edges", "call_edges", "kwargs_model")


def _capture(solver, **limits):
    return capture(solver, CaptureRequest(views=_VIEWS, **limits))


def _row(snapshot):
    view = snapshot.data["snapshot"]["views"]["kwargs_model"]
    rows = [row for row in view["rows"] if row["signature"].get("kwarg") == "kw"]
    assert len(rows) == 1
    return view, rows[0]


def _entities(snapshot):
    return {entity["id"]: entity for entity in snapshot.data["snapshot"]["entities"]}


def _facts(snapshot, owner):
    return {row["object"] for row in snapshot.data["snapshot"]["views"]["ctx_points"]["rows"]
            if row["owner"] == owner}


@pytest.mark.parametrize("expanded", [False, True])
@pytest.mark.parametrize("consumer", ["keys", "values"])
def test_real_invocation_input_collector_consumer_and_roundtrip(tmp_path, expanded, consumer):
    argument = "**{'x': 1}" if expanded else "x=1"
    source = f"def take(**kw):\n    return next(iter(kw.{consumer}()))\nresult = take({argument})\n"
    solver = _solver(tmp_path, source)
    snapshot = _capture(solver)
    view, row = _row(snapshot)
    assert view["coverage"]["registry"] == "complete"
    assert all(value == "complete" for value in view["coverage"]["fields"].values())
    entities = _entities(snapshot)
    assert entities[row["callsite"]]["origin"]["line"] == 3
    assert entities[row["callsite"]]["origin"]["column"] == 9
    call_edges = snapshot.data["snapshot"]["views"]["call_edges"]["rows"]
    assert {"callsite": row["owner"], "callee": row["callee_scope"],
            "call_kind": row["call_kind"]} in call_edges
    assert row["callee_object"] in _facts(snapshot, row["callee_input"]["ref"])
    assert len(row["collector"]["objects"]) == 1
    collector = row["collector"]["objects"][0]
    assert collector["object"] in _facts(snapshot, row["collector"]["parameter"]["ref"])
    key = next(channel for channel in collector["channels"]
               if channel["kind"] == "key" and channel["key"] == "x")
    assert {entities[ref]["attributes"]["value"] for ref in _facts(snapshot, key["owner"]["ref"])} == {1}
    assert row["consumers"][0]["model"] == "dict." + consumer
    assert row["consumers"][0]["scope"] == row["callee_scope"]
    assert row["consumers"][0]["receiver"] == collector["object"]
    assert row["consumers"][0]["channel"] == ("native_keys" if consumer == "keys" else "aggregate")
    assert row["consumers"][0]["stage"] == "registered_model_invocation"
    if expanded:
        assert row["keywords"][0]["name"] == "**"
        mapping = row["input_mappings"][0]
        assert mapping["input"] == row["keywords"][0]["value"]
        assert mapping["coverage"] == "complete"
        input_key = next(channel for channel in mapping["objects"][0]["channels"]
                         if channel["kind"] == "key" and channel["key"] == "x")
        assert _facts(snapshot, input_key["owner"]["ref"])
    else:
        value_owner = row["keywords"][0]["value"]["ref"]
        assert entities[value_owner]["attributes"]["variable_kind"] == "global"
        assert {entities[ref]["attributes"]["value"] for ref in _facts(snapshot, value_owner)} == {1}
    loaded = load_bundle(write_bundle(snapshot, tmp_path / "model-bundle"))
    assert loaded.data["snapshot"]["views"]["kwargs_model"] == view


def test_full_signature_hash_is_separate_from_display(tmp_path):
    solver = _solver(tmp_path, "def take(pos, /, ordinary=2, *, consumed=3, **kw):\n"
                     "    return kw.values()\nresult = take(0, ordinary=4, consumed=5, pos=6, x=1)\n")
    _, row = _row(_capture(solver))
    signature = row["signature"]
    expected = {"version": 1, "posonly": ["pos"], "positional": ["ordinary"],
                "kwonly": ["consumed"], "vararg": None, "kwarg": "kw",
                "positional_defaults": 1, "kwonly_defaults": [True]}
    assert {key: signature[key] for key in expected} == expected
    assert signature["coverage"] == "complete"
    assert signature["sha256"] == hashlib.sha256(canonical_bytes(expected)).hexdigest()
    # Inputs remain raw; M2 computes consumption independently from source.
    assert {entry["name"] for entry in row["keywords"]} == {"ordinary", "consumed", "pos", "x"}


def test_whole_producer_omission_does_not_erase_admitted_invocation(tmp_path, monkeypatch):
    from pythonstan.analysis.pointer.kcfa.processor.normal_call import NormalCallProcessor
    monkeypatch.setattr(NormalCallProcessor, "_match_parameters", lambda *args, **kwargs: None)
    solver = _solver(tmp_path, "def take(**kw):\n    return 0\nresult = take(x=1)\n")
    view, row = _row(_capture(solver))
    assert view["coverage"]["registry"] == "complete"
    assert len(view["rows"]) == 1
    assert row["collector"]["parameter"]["status"] == "missing_in_engine"
    assert row["collector"]["objects"] == []
    assert row["collector"]["coverage"] == "complete"
    assert row["keywords"][0]["value"]["status"] == "resolved"


def test_capture_is_passive_with_resolvers_and_native_constructors_forbidden(tmp_path, monkeypatch):
    from pythonstan.analysis.pointer.kcfa.context import CallSite, Ctx, Scope
    from pythonstan.analysis.pointer.kcfa.heap_model import Field
    from pythonstan.analysis.pointer.kcfa.object import AllocSite, DictObject
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    solver = _solver(tmp_path, "def take(**kw):\n    return kw.keys()\nresult = take(x=1)\n")
    before = _native_fingerprint(solver)

    def forbidden(*args, **kwargs):
        raise AssertionError("model capture created/resolved native state")

    for name in ("get_variable", "get_field", "_get_variable_direct"):
        monkeypatch.setattr(solver.state, name, forbidden)
    for cls in (CallSite, Ctx, Scope, Field, AllocSite, DictObject, NormalNode, Variable):
        monkeypatch.setattr(cls, "__init__", forbidden)
    snapshot = _capture(solver)
    assert _native_fingerprint(solver) == before
    assert _row(snapshot)[0]["coverage"]["registry"] == "complete"


def test_real_retained_empty_field_owner_has_exact_ctx_fact_coverage(tmp_path):
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import FieldAccess
    solver = _solver(tmp_path, "def take(**kw):\n    return kw.keys()\nresult = take(x=1)\n")
    # Stable controlled engine fault: remove Ctx storage while retaining the
    # real consumer-created field owner in the native PFG. Do not create one.
    owners = [node.var for node in solver.state._pointer_flow_graph.nodes
              if hasattr(node, "var") and isinstance(node.var, Ctx)
              and isinstance(node.var.content, FieldAccess)
              and node.var.content.field.name == "native_mapping_keys"]
    assert owners
    for owner in owners:
        solver.state._env.pop(owner, None)
    snapshot = _capture(solver)
    _, row = _row(snapshot)
    channel = next(item for item in row["collector"]["objects"][0]["channels"]
                   if item["kind"] == "native_keys")
    assert channel["field_exists"] is True
    assert channel["owner"]["status"] == "resolved"
    identity = channel["owner"]["ref"]
    points = snapshot.data["snapshot"]["views"]["ctx_points"]
    assert points["coverage"]["owners"][str(identity)] == {
        "fields": {"facts": "complete"}, "evidence": "audited_existing_owner_empty_native_env"}
    assert _facts(snapshot, identity) == set()


def test_callback_function_input_is_linked_by_real_owner(tmp_path):
    solver = _solver(tmp_path, "events = []\ndef chosen():\n    events.append('chosen')\n    return 7\n"
                     "def take(**kw):\n    return kw.values()\nresult = take(callback=chosen)\n")
    snapshot = _capture(solver)
    _, row = _row(snapshot)
    entities = _entities(snapshot)
    value = row["keywords"][0]["value"]["ref"]
    objects = [entities[ref] for ref in _facts(snapshot, value)]
    assert any(item["attributes"]["engine_class"] == "FunctionObject"
               and item["origin"]["qualname"].endswith("chosen") for item in objects)


def test_full_signature_budget_never_hashes_only_a_display_prefix(tmp_path):
    parameters = ", ".join(f"p{index}=0" for index in range(65))
    solver = _solver(tmp_path, f"def take({parameters}, **kw):\n    return kw.keys()\nresult = take(x=1)\n")
    snapshot = _capture(solver)
    row = snapshot.data["snapshot"]["views"]["kwargs_model"]["rows"][0]
    assert row["signature"]["coverage"] == "partial"
    assert row["signature"]["sha256"] is None
    assert row["coverage"]["signature"] == "partial"


def test_retained_field_owner_is_found_in_raw_node_storage_without_pfg_registration(tmp_path):
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
    from pythonstan.analysis.pointer.kcfa.variable import FieldAccess
    solver = _solver(tmp_path, "def take(**kw):\n    return kw.values()\nresult = take(x=1)\n")
    nodes = [node for node in solver.state._env if isinstance(node, NormalNode)
             and isinstance(node.var, Ctx) and isinstance(node.var.content, FieldAccess)
             and node.var.content.field.kind.value == "key"]
    assert nodes
    for node in nodes:
        solver.state._pointer_flow_graph.nodes.discard(node)
        solver.state._env.pop(node.var, None)
    snapshot = _capture(solver)
    _, row = _row(snapshot)
    channel = next(item for item in row["collector"]["objects"][0]["channels"] if item["kind"] == "key")
    assert channel["owner"]["status"] == "resolved"
    assert channel["field_exists"] is True


def test_default_capture_has_no_model_view_and_model_only_capture_does_not_invent_points(tmp_path):
    solver = _solver(tmp_path, "def take(**kw):\n    return kw.keys()\nresult = take(x=1)\n")
    assert "kwargs_model" not in capture(solver).data["snapshot"]["views"]
    snapshot = capture(solver, CaptureRequest(views=("kwargs_model",)))
    assert set(snapshot.data["snapshot"]["views"]) == {"kwargs_model"}
    assert _row(snapshot)[0]["coverage"]["registry"] == "complete"


def test_order_independence_empty_call_and_multiple_admissions(tmp_path):
    solver = _solver(tmp_path, "def take(**kw):\n    return kw.keys()\na = take()\nb = take(x=1)\n")
    snapshot = capture(solver, CaptureRequest(views=("kwargs_model", *_VIEWS[:-1])))
    view = snapshot.data["snapshot"]["views"]["kwargs_model"]
    assert view["coverage"]["registry"] == "complete"
    assert len(view["rows"]) == 2
    assert sorted(len(row["keywords"]) for row in view["rows"]) == [0, 1]
    assert all(row["collector"]["parameter"]["status"] == "resolved" for row in view["rows"])


@pytest.mark.parametrize("limits", [{"max_entities": 8}, {"max_rows": 1}])
def test_capture_budget_never_promotes_partial_inventory_to_complete(tmp_path, limits):
    solver = _solver(tmp_path, "def take(**kw):\n    return kw.keys()\na = take(x=1)\nb = take(y=2)\n")
    snapshot = _capture(solver, **limits)
    view = snapshot.data["snapshot"]["views"]["kwargs_model"]
    assert view["coverage"]["registry"] == "partial"
    assert all(owner["fields"]["invocations"] == "partial"
               for owner in view["coverage"]["owners"].values())
    assert any(item["view"] == "kwargs_model" for item in snapshot.data["snapshot"]["omissions"])
