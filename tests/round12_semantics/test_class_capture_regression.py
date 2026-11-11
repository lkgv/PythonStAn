"""CPython-backed class-body lexical capture through the production inspector.

These obligations use real entry calls and disable synthetic method roots. A
converged worklist cannot replace the required callback edges with a residual.
"""
import pytest

from tests.round12_semantics.test_descriptor_native import _analyze, _edges


PREFIX = '''def first(): return 1
def second(): return 2
def poison(): return 99
class Descriptor:
    def __init__(self, callback): self.callback = callback
    def __set_name__(self, owner, name): owner.callback = self.callback
'''

REPEATED_PARAMETER = '''def factory(callback):
    class Owner:
        field = Descriptor(callback)
    class Noise:
        unused = None
    return Owner
a = factory(first)
b = factory(second)
a.callback()
result = b.callback()
'''

REPEATED_LOCAL = '''def factory(selected):
    callback = selected
    class Owner:
        field = Descriptor(callback)
    return Owner
a = factory(first)
b = factory(second)
a.callback()
result = b.callback()
'''

REPEATED_CLOSURE = '''def outer(callback):
    def factory():
        class Owner:
            field = Descriptor(callback)
        return Owner
    return factory
fa = outer(first)
fb = outer(second)
a = fa()
b = fb()
a.callback()
result = b.callback()
'''

NESTED_CLASS = '''def factory(callback):
    class Outer:
        class Owner:
            field = Descriptor(callback)
    return Outer.Owner
a = factory(first)
b = factory(second)
a.callback()
result = b.callback()
'''

NESTED_CLASS_SHADOW = NESTED_CLASS.replace(
    "    class Outer:\n", "    class Outer:\n        callback = poison\n")

LATE_CELL = '''def outer():
    def factory():
        class Owner:
            field = Descriptor(callback)
        return Owner
    callback = second
    return factory
factory = outer()
Owner = factory()
result = Owner.callback()
'''

JOINED_FACTORY_CALLSITE = '''def outer(callback):
    def factory():
        class Owner:
            field = Descriptor(callback)
        return Owner
    return factory
fa = outer(first)
fb = outer(second)
results = []
for selected in [fa, fb]:
    Owner = selected()
    results.append(Owner.callback())
'''

CACHED_FUNCTION = '''def outer(callback):
    def factory():
        def inner():
            return callback()
        return inner
    return factory
fa = outer(first)
fb = outer(second)
results = []
for selected in [fa, fb]:
    inner = selected()
    results.append(inner())
'''

CACHED_METHOD = '''def outer(callback):
    def factory():
        class Owner:
            def invoke(self):
                return callback()
        return Owner
    return factory
fa = outer(first)
fb = outer(second)
results = []
for selected in [fa, fb]:
    Owner = selected()
    results.append(Owner().invoke())
'''

NESTED_METHOD_SHADOW = '''def factory(callback):
    class Outer:
        callback = poison
        class Owner:
            def invoke(self):
                return callback()
    return Outer.Owner
a = factory(first)
b = factory(second)
results = [a().invoke(), b().invoke()]
'''

NONLOCAL_FUNCTIONS = '''def build():
    callback = first
    def set_callback(selected):
        nonlocal callback
        callback = selected
    def invoke():
        return callback()
    return set_callback, invoke
setter, invoke = build()
setter(second)
result = invoke()
'''

NONLOCAL_METHOD = '''def build():
    callback = first
    class Owner:
        def set_callback(self, selected):
            nonlocal callback
            callback = selected
        def invoke(self):
            return callback()
    return Owner()
owner = build()
owner.set_callback(second)
result = owner.invoke()
'''

NONLOCAL_NESTED_METHOD = '''def build():
    callback = first
    class Outer:
        callback = poison
        class Owner:
            def set_callback(self, selected):
                nonlocal callback
                callback = selected
            def invoke(self):
                return callback()
    return Outer.Owner()
owner = build()
owner.set_callback(second)
result = owner.invoke()
'''

CACHED_NONLOCAL_WRITE = '''def outer():
    callback = first
    def factory():
        def setter(selected):
            nonlocal callback
            callback = selected
        return setter
    def invoke():
        return callback()
    return factory, invoke
fa, ia = outer()
fb, ib = outer()
for selected in [fa, fb]:
    setter = selected()
    setter(second)
results = [ia(), ib()]
'''


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("body", [REPEATED_PARAMETER, REPEATED_LOCAL,
    REPEATED_CLOSURE, NESTED_CLASS, NESTED_CLASS_SHADOW],
    ids=["parameter", "local", "inherited-closure", "nested-class", "class-shadow"])
def test_class_capture_preserves_repeated_lexical_callbacks(tmp_path, body, context_policy):
    source = PREFIX + body
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 2
    bundle = _analyze(tmp_path, source, context_policy=context_policy)
    edges = _edges(bundle)
    assert {("subject", "subject.first"), ("subject", "subject.second")} <= edges
    assert ("subject", "subject.poison") not in edges


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("enabled", [False, True])
def test_class_capture_retains_late_enclosing_cell_value(tmp_path, context_policy, enabled):
    source = PREFIX + LATE_CELL
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 2
    bundle = _analyze(tmp_path, source, context_policy=context_policy, enabled=enabled)
    assert ("subject", "subject.second") in _edges(bundle)


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
def test_class_capture_joins_factory_closures_at_one_real_callsite(tmp_path, context_policy):
    # Even a precise lexical classifier must allow two concrete closures to
    # reach this same selected() site. Allocation memoization must retain both
    # environments when their abstract allocation context is the same.
    source = PREFIX + JOINED_FACTORY_CALLSITE
    namespace = {}
    exec(source, namespace)
    assert namespace["results"] == [1, 2]
    bundle = _analyze(tmp_path, source, context_policy=context_policy)
    assert {("subject", "subject.first"), ("subject", "subject.second")} <= _edges(bundle)


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("body,caller", [
    (CACHED_FUNCTION, "subject.outer.factory.inner"),
    (CACHED_METHOD, "subject.outer.factory.Owner.invoke"),
    (NESTED_METHOD_SHADOW, "subject.factory.Outer.Owner.invoke")],
    ids=["cached-function", "cached-method", "nested-method-shadow"])
def test_callable_capture_joins_contexts_and_skips_class_shadow(tmp_path, body, caller, context_policy):
    source = PREFIX + body
    namespace = {}
    exec(source, namespace)
    assert namespace["results"] == [1, 2]
    bundle = _analyze(tmp_path, source, context_policy=context_policy)
    edges = _edges(bundle)
    assert {(caller, "subject.first"), (caller, "subject.second")} <= edges
    assert (caller, "subject.poison") not in edges


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("body,caller", [
    (NONLOCAL_FUNCTIONS, "subject.build.invoke"),
    (NONLOCAL_METHOD, "subject.build.Owner.invoke"),
    (NONLOCAL_NESTED_METHOD, "subject.build.Outer.Owner.invoke")],
    ids=["nonlocal-functions", "nonlocal-method", "nonlocal-nested-method"])
def test_nonlocal_write_reaches_sibling_captured_callback(tmp_path, body, caller, context_policy):
    source = PREFIX + body
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 2
    bundle = _analyze(tmp_path, source, context_policy=context_policy)
    edges = _edges(bundle)
    assert (caller, "subject.second") in edges
    assert (caller, "subject.poison") not in edges


def test_cached_capture_write_reaches_each_original_lexical_cell(tmp_path):
    source = PREFIX + CACHED_NONLOCAL_WRITE
    namespace = {}
    exec(source, namespace)
    assert namespace["results"] == [2, 2]
    bundle = _analyze(tmp_path, source, context_policy="2-cfa")
    snapshot = bundle.data["snapshot"]
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    # A qualname-only may-call assertion would hide loss of the second frame:
    # both readers have the same qualname. Check the two original cells.
    callbacks = {entity["id"] for entity in entities.values()
        if entity["kind"] == "ctx" and entity["attributes"].get("name") == "callback"
        and entity["origin"].get("qualname") == "subject.outer"}
    second = {entity["id"] for entity in entities.values()
        if entity["kind"] == "object" and entity["origin"].get("qualname") == "subject.second"}
    points = snapshot["views"]["ctx_points"]
    assert points["coverage"]["registry"] == "complete"
    joined = {row["owner"] for row in points["rows"]
        if row["owner"] in callbacks and row["object"] in second}
    assert len(callbacks) == 2
    assert callbacks <= joined
