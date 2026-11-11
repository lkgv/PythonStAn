"""Finite source regressions for explicit keyword collector may-flows.

Every prediction uses production lowering, the solver, the real adapter and a
sealed bundle. CPython executes only the trusted finite source strings below.
The late-input test is separately labeled abstract input growth, not a claim
that one concrete execution changes its already-evaluated argument.
"""
import pytest

from tests.round12_semantics.test_continuations import edges, event_values, values
from tests.round14_formal.test_key_context_model import _predict, _result


def _concrete(source):
    namespace = {}
    exec(source, namespace)
    return namespace


def _analyze(path, source, *, policy="0-cfa", model="none"):
    return _predict(path, source, policy=policy, model=model)


def _field_objects(data, variable, field_kind, field_name=None):
    """Read only the real exported field relation for one named parameter."""
    entities = {entity["id"]: entity for entity in data["entities"]}
    parameter_owners = {entity["id"] for entity in entities.values()
                        if entity["kind"] == "ctx"
                        and entity["attributes"].get("name") == variable}
    objects = {row["object"] for row in data["views"]["ctx_points"]["rows"]
               if row["owner"] in parameter_owners}
    field_owners = {entity["id"] for entity in entities.values()
                    if entity["kind"] == "ctx"
                    and entity["attributes"].get("field_kind") == field_kind
                    and entity["attributes"].get("field_name") == field_name
                    and entity.get("links", {}).get("object", {}).get("ref") in objects}
    return [entities[row["object"]] for row in data["views"]["ctx_points"]["rows"]
            if row["owner"] in field_owners]


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("model", ["none", "receiver_key_v1"])
def test_explicit_collector_keys_survive_real_native_view(tmp_path, policy, model):
    source = '''def take(**kw):
    return next(iter(kw.keys()))
result = take(x=1)
'''
    assert _concrete(source)["result"] == "x"
    bundle, _ = _analyze(tmp_path, source, policy=policy, model=model)
    assert _result(bundle.data["snapshot"]) == {"x"}


def test_explicit_collector_values_survive_real_native_view(tmp_path):
    source = '''def take(**kw):
    return next(iter(kw.values()))
result = take(x=1)
'''
    assert _concrete(source)["result"] == 1
    bundle, _ = _analyze(tmp_path, source)
    assert _result(bundle.data["snapshot"]) == {1}


@pytest.mark.parametrize("view, expected", [("keys", "x"), ("values", 1)])
def test_expanded_literal_positive_control(tmp_path, view, expected):
    source = '''def take(**kw):
    return next(iter(kw.VIEW()))
result = take(**{"x": 1})
'''.replace("VIEW", view)
    assert _concrete(source)["result"] == expected
    bundle, _ = _analyze(tmp_path, source)
    assert _result(bundle.data["snapshot"]) == {expected}


@pytest.mark.parametrize("arguments", ["callback=chosen", '**{"callback": chosen}'])
def test_keyword_keys_preserve_callback_return_and_heap_effect(tmp_path, arguments):
    source = '''events = []
def chosen():
    events.append("chosen")
    return 7
def poison():
    events.append("poison")
    return 99
def forward(**kwargs):
    def names():
        yield from kwargs.keys()
    pending = names()
    name = next(pending)
    return kwargs[name]()
result = forward(ARGUMENTS)
'''.replace("ARGUMENTS", arguments)
    concrete = _concrete(source)
    assert concrete["result"] == 7 and concrete["events"] == ["chosen"]
    bundle, _ = _analyze(tmp_path, source)
    data = bundle.data["snapshot"]
    assert _result(data) == {7}
    assert ("forward", "chosen") in edges(data)
    assert ("forward", "poison") not in edges(data)
    assert event_values(data) == {"chosen"}


@pytest.mark.parametrize("signature", ["consumed, **kw", "*, consumed, **kw"])
@pytest.mark.parametrize("view, expected", [("keys", "x"), ("values", 1)])
def test_regular_and_keyword_only_formals_consume_their_keyword(tmp_path, signature, view, expected):
    source = '''def take(SIGNATURE):
    return next(iter(kw.VIEW()))
result = take(consumed="poison", x=1)
'''.replace("SIGNATURE", signature).replace("VIEW", view)
    assert _concrete(source)["result"] == expected
    bundle, _ = _analyze(tmp_path, source)
    data = bundle.data["snapshot"]
    assert _result(data) == {expected}
    # Observe exact string key identities, not dictionary values as keys.
    names = _field_objects(data, "kw", "class_metadata", "native_mapping_keys")
    assert names and all(row["attributes"].get("value") == "x" for row in names)


@pytest.mark.parametrize("view, expected", [("keys", "x"), ("values", 1)])
def test_positional_only_name_is_legal_in_keyword_collector(tmp_path, view, expected):
    source = '''def take(x, /, **kw):
    return next(iter(kw.VIEW()))
result = take(0, x=1)
'''.replace("VIEW", view)
    assert _concrete(source)["result"] == expected
    bundle, _ = _analyze(tmp_path, source)
    assert _result(bundle.data["snapshot"]) == {expected}


def test_empty_collector_has_no_invented_key_or_value(tmp_path):
    source = '''def take(**kw):
    return kw
result = take()
'''
    assert _concrete(source)["result"] == {}
    bundle, _ = _analyze(tmp_path, source)
    data = bundle.data["snapshot"]
    entities = {row["id"]: row for row in data["entities"]}
    returned = values(data, "result")
    assert returned and all(entities[obj]["attributes"].get("engine_class") == "DictObject"
                            for obj in returned)
    assert _field_objects(data, "kw", "class_metadata", "native_mapping_keys") == []
    assert _field_objects(data, "kw", "elem") == []


def test_collector_value_subscription_retains_old_and_late_input(tmp_path):
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle

    source = '''incoming = None
later = 7
def take(**kw):
    return next(iter(kw.values()))
result = take(x=incoming)
'''
    assert _concrete(source)["result"] is None
    assert _concrete(source.replace("incoming = None", "incoming = 7"))["result"] == 7
    bundle, solver = _analyze(tmp_path, source)
    assert _result(bundle.data["snapshot"]) == {None}
    globals_ = {node.content.name: node for node in solver.state._env
                if isinstance(node, Ctx) and isinstance(node.content, Variable)
                and node.content.kind.value == "global"}
    # Extend an actual source input, not the collector or expected output.
    solver.handle_new_points_to(globals_["incoming"], globals_["incoming"].scope,
                               solver.state.get_points_to(globals_["later"]))
    solver.solve_to_fixpoint()
    assert solver._stats["converged"] == 1 and solver._stats["pending_work"] == 0
    after = load_bundle(write_bundle(capture(solver, run_metadata={
        "audit_kind": "controlled_abstract_input_growth",
        "input_variable": "incoming", "added_source_variable": "later",
    }), tmp_path / "late_bundle"))
    assert _result(after.data["snapshot"]) == {None, 7}
