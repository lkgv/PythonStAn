"""Independent source-call keyword protocol review; no helper edits here."""
import pytest
from tests.round12_semantics.test_descriptor_native import _analyze, _edges, _list_strings


PREFIX = '''events = []
def chosen():
    events.append("chosen")
    return 7
def consume(*, callback):
    return callback()
'''


def _analyze_partitioned(tmp_path, source, policy, monkeypatch):
    from tests.round12_semantics import test_descriptor_native as driver
    native_config = driver.Config

    def configured(**options):
        return native_config(keyword_context_model="receiver_key_v1", **options)

    monkeypatch.setattr(driver, "Config", configured)
    bundle = driver._analyze(tmp_path, source, context_policy=policy)
    assert bundle.data["run"]["configuration"]["keyword_context_model"] == "receiver_key_v1"
    return bundle


def test_custom_keys_native_dictionary_view_retains_getitem_and_callback(tmp_path):
    source = PREFIX + '''class Payload:
    def __init__(self): self.data = {"callback": chosen}
    def keys(self):
        events.append("keys")
        return self.data.keys()
    def __getitem__(self, name):
        events.append("getitem")
        return self.data[name]
result = consume(**Payload())
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == 7
    assert concrete["events"] == ["keys", "getitem", "chosen"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.consume", "subject.chosen") in _edges(bundle)
    assert {"keys", "getitem", "chosen"} <= _list_strings(bundle, "events")


def test_definitely_non_string_tuple_key_keeps_getitem_effect_without_named_binding(tmp_path):
    source = PREFIX + '''class Payload:
    def keys(self): return [("callback",)]
    def __getitem__(self, name):
        events.append("getitem")
        return chosen
try:
    result = consume(**Payload())
except TypeError:
    result = "rejected"
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == "rejected"
    assert concrete["events"] == ["getitem"]
    bundle = _analyze(tmp_path, source)
    assert "getitem" in _list_strings(bundle, "events")
    assert ("subject.consume", "subject.chosen") not in _edges(bundle)
    assert "chosen" not in _list_strings(bundle, "events")


def test_native_keys_view_metadata_does_not_read_values_as_unknown_keyword_keys(tmp_path):
    source = PREFIX + '''def poison():
    events.append("value-poison")
class Payload:
    def __init__(self):
        self.data = {"callback": chosen, "unused": poison}
        self.names = {"callback": poison}
    def keys(self): return self.names.keys()
    def __getitem__(self, name): return self.data[name]
result = consume(**Payload())
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == 7
    assert concrete["events"] == ["chosen"]
    bundle = _analyze(tmp_path, source)
    assert ("subject.consume", "subject.chosen") in _edges(bundle)
    assert ("subject.consume", "subject.poison") not in _edges(bundle)
    assert "value-poison" not in _list_strings(bundle, "events")


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_native_keys_view_keeps_unused_value_separate_from_callback_parameter(tmp_path, monkeypatch, policy):
    source = PREFIX.replace("def consume(*, callback):", "def consume(*, callback, unused):") + '''def poison():
    events.append("value-poison")
class Payload:
    def __init__(self): self.data = {"callback": chosen, "unused": poison}
    def keys(self): return self.data.keys()
    def __getitem__(self, name): return self.data[name]
result = consume(**Payload())
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == 7
    assert concrete["events"] == ["chosen"]
    bundle = _analyze_partitioned(tmp_path, source, policy, monkeypatch)
    assert ("subject.consume", "subject.chosen") in _edges(bundle)
    assert ("subject.consume", "subject.poison") not in _edges(bundle)
    assert "value-poison" not in _list_strings(bundle, "events")


def test_copied_namespace_keys_view_supplies_actual_callback_name(tmp_path):
    source = PREFIX + '''class Config:
    def __init__(self): self.callback = chosen
config = Config()
mapping = config.__dict__.copy()
view = mapping.keys()
def delegate():
    yield from view
pending = delegate()
name = pending.send(None)
callback = mapping[name]
result = callback()
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["name"] == "callback"
    assert concrete["result"] == 7
    bundle = _analyze(tmp_path, source)
    assert ("subject", "subject.chosen") in _edges(bundle)
    assert "chosen" in _list_strings(bundle, "events")


def test_live_native_keys_view_retains_late_actual_insertion(tmp_path):
    from tests.round12_semantics.test_continuations import analyze, edges, event_values
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.variable import Variable
    from pythonstan_inspector.adapter import capture
    from pythonstan_inspector.storage import load_bundle, write_bundle
    source = '''events = []
def chosen(): events.append("chosen")
def other(): events.append("other")
def poison(): events.append("value-poison")
mapping = {chosen: poison}
view = mapping.keys()
incoming = None
mapping[incoming] = poison
def delegate():
    yield from view
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
    assert concrete["events"] == ["chosen"]
    variant = {}
    exec(source.replace("incoming = None", "incoming = other"), variant)
    assert variant["events"] == ["chosen", "other"]
    before, solver = analyze(tmp_path, source, retain=True)
    assert "other" not in event_values(before)
    locations = {node.content.name: node for node in solver.state._env
        if isinstance(node, Ctx) and isinstance(node.content, Variable)
        and node.content.kind.value == "global"}
    solver.handle_new_points_to(locations["incoming"], locations["incoming"].scope,
                               solver.state.get_points_to(locations["other"]))
    solver.solve_to_fixpoint()
    after = load_bundle(write_bundle(capture(solver), tmp_path / "after-view")).data["snapshot"]
    assert ("<module>", "other") in edges(after)
    assert "other" in event_values(after)
    assert "value-poison" not in event_values(after)


def test_opaque_dictionary_constructor_provenance_remains_explicitly_unproved(tmp_path):
    from tests.round12_semantics.test_continuations import analyze
    source = PREFIX + '''class Opaque:
    def keys(self): return ["callback"]
    def __getitem__(self, name): return chosen
mapping = dict(Opaque())
view = mapping.keys()
def delegate():
    yield from view
pending = delegate()
try:
    name = pending.send(None)
except StopIteration:
    name = None
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["name"] == "callback"
    _, solver = analyze(tmp_path, source, retain=True)
    # A residual guard, not a functional-coverage claim for dict(custom).
    assert any("native_mapping_key_provenance" in row.get("effects", ())
               and "unknown_yield" in row.get("effects", ())
               for row in solver.state._residual_effects.values())


def test_default_context_model_is_explicit_and_red_source_remains_retained(tmp_path):
    from pathlib import Path
    import hashlib
    evidence = Path(__file__).resolve().parents[2] / "docs/certified_analysis/evidence/round14_continuation"
    raw = (evidence / "keyword_correlation_source.py.txt").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "ba75f271373d07ae8f91309ded40298077c67d9f4a3e8fa4c979a4fde646e5c4"
    bundle = _analyze(tmp_path, raw.decode())
    assert bundle.data["run"]["configuration"]["keyword_context_model"] == "none"
    assert ("subject.consume", "subject.chosen") in _edges(bundle)
    # Original default-mode precision predictions remain measured evidence,
    # rather than turning their forbidden-edge observation into new gold.
    assert (evidence / "keyword_correlation_red.log").is_file()


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("kind", ["generator", "coroutine"])
def test_protocol_partition_keeps_native_returned_continuations_distinct_and_unused_cold(tmp_path, monkeypatch, policy, kind):
    source = '''events = []
unused_values = []
def chosen():
    events.append("chosen")
    return 7
def poison():
    events.append("value-poison")
class Payload:
    def __init__(self): self.data = {"callback": chosen, "unused": poison}
    def keys(self): return ["callback", "unused"]
    GETITEM(self, name):
        PRODUCE self.data[name]
CONSUME(*, callback, unused):
    unused_values.append(unused)
    READ
    return selected()
DRIVER
'''
    if kind == "generator":
        source = source.replace("GETITEM", "def __getitem__").replace("PRODUCE", "yield")
        source = source.replace("CONSUME", "def consume").replace("READ", "selected = callback.send(None)")
        source = source.replace("DRIVER", "result = consume(**Payload())")
    else:
        source = source.replace("GETITEM", "async def __getitem__").replace("PRODUCE", "return")
        source = source.replace("CONSUME", "async def consume").replace("READ", "selected = await callback")
        source = source.replace("DRIVER", '''pending = consume(**Payload())
try:
    pending.send(None)
except StopIteration as completed:
    result = completed.value''')
    concrete = {}
    exec(source, concrete)
    assert concrete["result"] == 7
    assert concrete["events"] == ["chosen"]
    for value in concrete["unused_values"]:
        value.close()  # Host cleanup; no extra operation in analyzed source.
    bundle = _analyze_partitioned(tmp_path, source, policy, monkeypatch)
    assert ("subject.consume", "subject.chosen") in _edges(bundle)
    assert ("subject.consume", "subject.poison") not in _edges(bundle)
    assert "value-poison" not in _list_strings(bundle, "events")
