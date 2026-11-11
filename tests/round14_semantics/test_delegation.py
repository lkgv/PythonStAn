"""Native dictionary-key delegation through a pinned Django method.

The RenderContext.__iter__ body below is identical to Django6.1.3's method;
the surrounding finite object/driver fixture supplies a complete witness,
without importing or executing the upstream package or injecting call edges.
"""
import ast
import hashlib
from pathlib import Path

import pytest

from tests.round12_semantics.test_descriptor_native import _analyze, _edges, _list_strings


DJANGO_METHOD = '''def __iter__(self):
    yield from self.dicts[-1]
'''

DJANGO_CONTEXT = '''events = []
def selected():
    events.append("selected")
    return "rendered"
def poison():
    events.append("value-poison")
    return "wrong"
class Key:
    def render(self):
        return selected()
class RenderContext:
    def __init__(self, mapping):
        self.dicts = [mapping]
    def __iter__(self):
        yield from self.dicts[-1]
mapping = {Key(): poison}
context = RenderContext(mapping)
iterator = context.__iter__()
node = iterator.send(None)
result = node.render()
'''


def _global_literals(bundle, name):
    snapshot = bundle.data["snapshot"]
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    owners = {entity["id"] for entity in entities.values()
        if entity["kind"] == "ctx" and entity["attributes"].get("name") == name
        and entity["attributes"].get("variable_kind") == "global"}
    points = snapshot["views"]["ctx_points"]
    assert points["coverage"]["registry"] == "complete"
    return {entities[row["object"]]["attributes"]["value"] for row in points["rows"]
        if row["owner"] in owners and "value" in entities[row["object"]]["attributes"]}


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("intrinsics", [False, True])
def test_pinned_render_context_dictionary_yields_key_object_and_callback_effect(tmp_path, policy, intrinsics):
    concrete = {}
    exec(DJANGO_CONTEXT, concrete)
    assert concrete["result"] == "rendered"
    assert concrete["events"] == ["selected"]
    bundle = _analyze(tmp_path, DJANGO_CONTEXT, context_policy=policy, enabled=intrinsics)
    edges = _edges(bundle)
    assert {("subject", "subject.Key.render"), ("subject.Key.render", "subject.selected")} <= edges
    assert "selected" in _list_strings(bundle, "events")
    assert not any(callee == "subject.poison" for _, callee in edges)
    assert "value-poison" not in _list_strings(bundle, "events")


def test_pinned_django_render_context_ast_and_source_binding():
    source = Path(__file__).resolve().parents[2].parent / "round8_corpus/django/django-6.1.3/django/template/context.py"
    if not source.exists():
        pytest.skip("rehydrate exact pinned Django source for its separate source-binding check")
    raw = source.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "aba72a7da853cfcdc2ea5bcbf5771c3d88def2a6f309cc584e8270132da96186"
    tree = ast.parse(raw)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RenderContext")
    actual = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "__iter__")
    assert hashlib.sha256(ast.dump(actual, include_attributes=False).encode()).hexdigest() == "29f3f3d99edf89351ecd42fab8fe358ded207762e3770c7b53d15f05cdafab45"
    expected = ast.parse(DJANGO_METHOD).body[0]
    assert ast.dump(actual, include_attributes=False) == ast.dump(expected, include_attributes=False)
    assert actual.lineno == 198
    assert isinstance(actual.body[0].value, ast.YieldFrom)
    assert actual.body[0].lineno == 199


@pytest.mark.parametrize("expression", ["mapping.copy()", "dict(mapping)", "{**mapping}"])
@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_native_copies_preserve_key_callable_without_value_callback_leak(tmp_path, expression, policy):
    source = '''events = []
def selected(): events.append("selected")
def poison(): events.append("value-poison")
mapping = {selected: poison}
copy = EXPRESSION
def delegate():
    yield from copy
pending = delegate()
callback = pending.send(None)
callback()
'''.replace("EXPRESSION", expression)
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["selected"]
    bundle = _analyze(tmp_path, source, context_policy=policy)
    assert ("subject", "subject.selected") in _edges(bundle)
    assert "selected" in _list_strings(bundle, "events")
    assert not any(callee == "subject.poison" for _, callee in _edges(bundle))


@pytest.mark.parametrize("copy", [False, True])
def test_namespace_dictionary_key_producer_reaches_delegated_lookup_callback(tmp_path, copy):
    source = '''events = []
def selected(): events.append("selected")
class Config:
    def __init__(self): self.callback = selected
config = Config()
namespace = config.__dict__COPY
def delegate():
    yield from namespace
pending = delegate()
name = pending.send(None)
callback = namespace[name]
callback()
'''.replace("COPY", ".copy()" if copy else "")
    concrete = {}
    exec(source, concrete)
    assert concrete["name"] == "callback"
    assert concrete["events"] == ["selected"]
    bundle = _analyze(tmp_path, source)
    assert ("subject", "subject.selected") in _edges(bundle)
    assert "selected" in _list_strings(bundle, "events")


@pytest.mark.parametrize("setup,missing_name", [
    ('class Config:\n    pass\nconfig = Config()\nprobe = getattr(config, "missing", None)', "missing"),
    ('class Config:\n    inherited = 17\nconfig = Config()\nprobe = config.inherited', "inherited")])
def test_empty_namespace_slots_and_inherited_reads_do_not_invent_stored_keys(tmp_path, setup, missing_name):
    source = setup + '''
namespace = config.__dict__
def delegate():
    yield from namespace
pending = delegate()
try:
    value = pending.send(None)
except StopIteration:
    value = "empty"
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["value"] == "empty"
    assert concrete["namespace"] == {}
    bundle = _analyze(tmp_path, source)
    assert missing_name not in _global_literals(bundle, "value")


def test_none_valued_namespace_slot_still_produces_its_real_key(tmp_path):
    source = '''class Config:
    def __init__(self): self.value = None
config = Config()
namespace = config.__dict__.copy()
def delegate():
    yield from namespace
pending = delegate()
name = pending.send(None)
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["name"] == "value"
    bundle = _analyze(tmp_path, source)
    assert "value" in _global_literals(bundle, "name")


def test_native_dictionary_iteration_does_not_rehash_keys_or_yield_values(tmp_path):
    source = '''events = []
def selected(): events.append("selected")
def poison(): events.append("value-poison")
class Key:
    def __hash__(self):
        events.append("hashed")
        return 1
    def __call__(self): selected()
key = Key()
mapping = {key: poison}
def delegate():
    yield from mapping
pending = delegate()
callback = pending.send(None)
callback()
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["hashed", "selected"]
    bundle = _analyze(tmp_path, source)
    edges = _edges(bundle)
    assert ("subject.Key.__call__", "subject.selected") in edges
    assert ("subject.delegate", "subject.Key.__hash__") not in edges
    assert ("subject.delegate", "subject.poison") not in edges
    assert "value-poison" not in _list_strings(bundle, "events")


def test_mixed_scalar_and_callable_keys_keep_key_value_roles(tmp_path):
    source = '''events = []
def selected(): events.append("selected")
def poison(): events.append("value-poison")
mapping = {selected: poison, "marker": "not-a-key"}
def delegate():
    yield from mapping
pending = delegate()
callback = pending.send(None)
callback()
name = pending.send(None)
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["name"] == "marker"
    assert concrete["events"] == ["selected"]
    bundle = _analyze(tmp_path, source)
    assert "marker" in _global_literals(bundle, "name")
    assert "not-a-key" not in _global_literals(bundle, "name")
    assert ("subject", "subject.selected") in _edges(bundle)
    assert not any(callee == "subject.poison" for _, callee in _edges(bundle))


def test_native_dict_subclass_uses_its_actual_iteration_override(tmp_path):
    source = '''events = []
def selected(): events.append("selected")
def poison(): events.append("value-poison")
class Custom(dict):
    def __iter__(self):
        events.append("custom-iter")
        yield selected
mapping = Custom({poison: selected})
def delegate():
    yield from mapping
pending = delegate()
callback = pending.send(None)
callback()
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["custom-iter", "selected"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.delegate", "subject.Custom.__iter__") in _edges(bundle)
    assert {"custom-iter", "selected"} <= _list_strings(bundle, "events")
    assert "value-poison" not in _list_strings(bundle, "events")


def test_empty_native_dictionary_has_none_delegation_completion(tmp_path):
    source = '''def delegate():
    completed = yield from {}
    yield completed
pending = delegate()
result = pending.send(None)
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] is None
    bundle = _analyze(tmp_path, source)
    assert None in _global_literals(bundle, "result")


def test_late_actual_dictionary_insertion_producer_replays_key_callback(tmp_path):
    from tests.round12_semantics.test_continuations import analyze, edges, event_values
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle
    source = '''events = []
def selected(): events.append("selected")
def other(): events.append("other")
def poison(): events.append("value-poison")
mapping = {selected: poison}
incoming = None
mapping[incoming] = poison
def delegate():
    yield from mapping
pending = delegate()
first = pending.send(None)
first()
try:
    second = pending.send(None)
    second()
except TypeError:
    pass
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["events"] == ["selected"]
    variant = {}
    exec(source.replace("incoming = None", "incoming = other"), variant)
    assert variant["events"] == ["selected", "other"]
    before, solver = analyze(tmp_path, source, retain=True)
    assert "other" not in event_values(before)
    locations = {node.content.name: node for node in solver.state._env
        if isinstance(node, Ctx) and isinstance(node.content, Variable)
        and node.content.kind.value == "global"}
    solver.handle_new_points_to(locations["incoming"], locations["incoming"].scope,
                               solver.state.get_points_to(locations["other"]))
    solver.solve_to_fixpoint()
    after = load_bundle(write_bundle(capture(solver), tmp_path / "after")).data["snapshot"]
    assert ("<module>", "other") in edges(after)
    assert "other" in event_values(after)
    assert "value-poison" not in event_values(after)


def test_late_namespace_field_name_replays_existing_copy_and_delegate(tmp_path):
    from tests.round12_semantics.test_continuations import analyze
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan.analysis.pointer.kcfa.heap_model import elem
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle
    source = '''class Config:
    pass
config = Config()
names = ["first"]
late_name = "second"
for name in names:
    setattr(config, name, None)
namespace = config.__dict__.copy()
def delegate():
    yield from namespace
pending = delegate()
observed = pending.send(None)
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["observed"] == "first"
    variant = {}
    exec(source.replace('names = ["first"]', 'names = ["first", "second"]'), variant)
    assert set(variant["namespace"]) == {"first", "second"}
    before, solver = analyze(tmp_path, source, retain=True)
    before_bundle = load_bundle(write_bundle(capture(solver), tmp_path / "before-keys"))
    assert "second" not in _global_literals(before_bundle, "observed")
    locations = {node.content.name: node for node in solver.state._env
        if isinstance(node, Ctx) and isinstance(node.content, Variable)
        and node.content.kind.value == "global"}
    owner = next(iter(solver.state.get_points_to(locations["names"])))
    target = solver.state.get_field(locations["names"].scope,
        locations["names"].context, owner, elem())
    solver.handle_new_points_to(target, target.scope, solver.state.get_points_to(locations["late_name"]))
    solver.solve_to_fixpoint()
    after = load_bundle(write_bundle(capture(solver), tmp_path / "after-keys"))
    assert "second" in _global_literals(after, "observed")


def test_removal_presence_stays_an_explicit_unproved_obligation(tmp_path):
    from tests.round12_semantics.test_continuations import analyze
    source = '''class Config:
    def __init__(self): self.removed = None
config = Config()
del config.removed
namespace = config.__dict__.copy()
def delegate():
    yield from namespace
pending = delegate()
try:
    result = pending.send(None)
except StopIteration:
    result = "empty"
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == "empty"
    _, solver = analyze(tmp_path, source, retain=True)
    assert any("key_presence" in row.get("effects", ())
               for row in solver.state._residual_effects.values())


@pytest.mark.parametrize("index_sensitive", [False, True])
def test_dictionary_key_delegation_is_available_with_both_index_profiles(tmp_path, monkeypatch, index_sensitive):
    from tests.round12_semantics import test_descriptor_native as driver
    native_config = driver.Config

    def configured(**options):
        return native_config(index_sensitive=index_sensitive, **options)

    monkeypatch.setattr(driver, "Config", configured)
    concrete = {}
    exec(DJANGO_CONTEXT, concrete)
    assert concrete["events"] == ["selected"]
    bundle = driver._analyze(tmp_path, DJANGO_CONTEXT, context_policy="2-cfa")
    assert ("subject.Key.render", "subject.selected") in _edges(bundle)
    assert "selected" in _list_strings(bundle, "events")
    assert "value-poison" not in _list_strings(bundle, "events")
