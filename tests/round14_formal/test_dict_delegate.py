"""Independent native-key identities and old/late delegation dependencies."""
import hashlib
from pathlib import Path

import pytest

from tests.round12_semantics.test_continuations import analyze, edges, event_values


SOURCE_SHA = "7a940b1e2913164088a1e1d2982ed7b7387ab642a049782e56ed5094a938f534"


def _source():
    raw = Path(__file__).with_name("dict_delegate_source.py.txt").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA
    return raw.decode()


def _predict(path, source, policy, *, retain=False):
    from pythonstan.world import World
    path.mkdir(exist_ok=True)
    World.setup()
    return analyze(path, source, policy=policy, retain=retain)


def _completion_values(data):
    entities = {row["id"]: row for row in data["entities"]}
    owners = {row["id"] for row in data["entities"]
              if row["kind"] == "ctx" and row["attributes"].get("name") == "completion"
              and row["origin"].get("scope") == "subject.delegated"}
    return {entities[row["object"]]["attributes"]["value"]
            for row in data["views"]["ctx_points"]["rows"]
            if row["owner"] in owners and "value" in entities[row["object"]]["attributes"]}


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_dictionary_yields_keys_not_values_and_has_none_completion(tmp_path, policy):
    source = _source()
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen", None]
    data = _predict(tmp_path, source, policy)
    assert ("<module>", "chosen") in edges(data)
    assert ("<module>", "poison") not in edges(data)
    assert ("<module>", "late") not in edges(data)
    assert {"chosen", None} <= event_values(data)
    assert None in _completion_values(data)


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_actual_late_insertion_producer_keeps_both_key_callbacks(tmp_path, policy):
    source = _source().replace("for callback in delegated():", "mapping[late] = poison\nfor callback in delegated():")
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["chosen", "late", None]
    data = _predict(tmp_path, source, policy)
    assert {("<module>", "chosen"), ("<module>", "late")} <= edges(data)
    assert ("<module>", "poison") not in edges(data)
    assert {"chosen", "late", None} <= event_values(data)
    assert None in _completion_values(data)


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_existing_delegation_subscription_replays_a_late_key(tmp_path, policy):
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.native_mapping import NATIVE_MAPPING_KEYS
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle

    before, solver = _predict(tmp_path, _source(), policy, retain=True)
    assert ("<module>", "late") not in edges(before)
    globals_ = {node.content.name: node for node in solver.state._env
                if isinstance(node, Ctx) and isinstance(node.content, Variable)
                and node.content.kind.value == "global"}
    mapping_location = globals_["mapping"]
    mapping, = solver.state.get_points_to(mapping_location)
    keys = solver.state.get_field(mapping_location.scope, mapping_location.context,
                                  mapping, NATIVE_MAPPING_KEYS)
    solver.handle_new_points_to(keys, keys.scope, solver.state.get_points_to(globals_["late"]))
    solver.solve_to_fixpoint()
    assert solver._stats["converged"] == 1
    after = load_bundle(write_bundle(capture(solver), tmp_path / "after")).data["snapshot"]
    assert {("<module>", "chosen"), ("<module>", "late")} <= edges(after)
    assert ("<module>", "poison") not in edges(after)
    assert {"chosen", "late", None} <= event_values(after)
