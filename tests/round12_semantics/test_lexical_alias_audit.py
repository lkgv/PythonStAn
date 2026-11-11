"""Independent captured-read/write obligations at module result locations.

Checking only the shared reader's qualified call pair could let a write to the
first captured environment hide a missing write to the second one. These tests
also inspect the second module result in the actual frozen points-to view.
"""
import pytest

from tests.round12_semantics.test_descriptor_native import _analyze, _edges


PREFIX = "def first(): return 1\ndef second(): return 2\ndef replacement(): return 3\ndef poison(): return 99\n"

JOINED_WRITE = '''def outer(initial):
    callback = initial
    def factory():
        def writer():
            nonlocal callback
            callback = replacement
        return writer
    def reader():
        return callback()
    return factory, reader
fa, ra = outer(first)
fb, rb = outer(second)
writers = []
for factory in [fa, fb]:
    writer = factory()
    writers.append(writer)
writers[1]()
first_result = ra()
second_result = rb()
'''

NESTED_NONLOCAL = '''def outer():
    callback = first
    def middle():
        def writer():
            nonlocal callback
            callback = second
        def reader():
            return callback()
        return writer, reader
    writer, reader = middle()
    writer()
    return reader()
second_result = outer()
'''

LATE_CELL = '''def outer():
    callback = first
    def middle():
        def reader():
            return callback()
        return reader
    reader = middle()
    callback = second
    return reader
selected = outer()
second_result = selected()
'''

EXPLICIT_GLOBAL = '''callback = first
def factory(callback):
    class Holder:
        callback = poison
        @staticmethod
        def reader():
            global callback
            return callback()
    return Holder.reader
selected = factory(poison)
second_result = selected()
'''


def _module_values(bundle, name):
    snapshot = bundle.data["snapshot"]
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    owners = {entity["id"] for entity in entities.values()
              if entity["kind"] == "ctx" and entity["attributes"].get("name") == name
              and entity["attributes"].get("variable_kind") == "global"
              and entity["origin"].get("scope") == "subject"}
    return {entities[row["object"]]["attributes"].get("value")
            for row in snapshot["views"]["ctx_points"]["rows"] if row["owner"] in owners
            and entities[row["object"]]["attributes"].get("engine_class") == "ConstantObject"}


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("body,expected", [(JOINED_WRITE, 3), (NESTED_NONLOCAL, 2),
                                          (LATE_CELL, 2), (EXPLICIT_GLOBAL, 1)],
                         ids=["cached-alias-write", "nested-nonlocal", "late-cell", "explicit-global"])
def test_lexical_aliases_retain_concrete_read_and_write_results(tmp_path, body, expected, context_policy):
    source = PREFIX + body
    concrete = {}
    exec(compile(source, "<trusted-lexical-alias-witness>", "exec"), concrete)
    assert concrete["second_result"] == expected
    bundle = _analyze(tmp_path, source, context_policy=context_policy)
    assert expected in _module_values(bundle, "second_result")
    if body == EXPLICIT_GLOBAL:
        assert 99 not in _module_values(bundle, "second_result")
        assert ("subject.factory.Holder.reader", "subject.poison") not in _edges(bundle)


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("declaration", ["global", "nonlocal", "local"])
def test_class_namespace_excludes_external_writes_but_retains_real_local_descriptor(
        tmp_path, context_policy, declaration):
    prefix = '''observed = []
def hook(): observed.append("set_name")
class Descriptor:
    def __set_name__(self, owner, name): hook()
'''
    if declaration == "nonlocal":
        body = '''def factory():
    field = None
    class Holder:
        nonlocal field
        field = Descriptor()
    return Holder, field
Holder, field_copy = factory()
'''
    elif declaration == "global":
        body = "class Holder:\n    global field\n    field = Descriptor()\nfield_copy = field\n"
    else:
        body = "class Holder:\n    field = Descriptor()\nfield_copy = Holder.field\n"
    source = prefix + body
    concrete = {}
    exec(compile(source, "<trusted-class-binding-namespace-witness>", "exec"), concrete)
    expected_hook = declaration == "local"
    assert ("field" in concrete["Holder"].__dict__) is expected_hook
    assert concrete["observed"] == (["set_name"] if expected_hook else [])
    assert isinstance(concrete["field_copy"], concrete["Descriptor"])
    bundle = _analyze(tmp_path, source, context_policy=context_policy)
    snapshot = bundle.data["snapshot"]
    assert snapshot["views"]["ctx_points"]["coverage"]["registry"] == "complete"
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    owners = {entity["id"] for entity in entities.values()
              if entity["kind"] == "ctx" and entity["attributes"].get("name") == "field_copy"
              and entity["origin"].get("scope") == "subject"}
    objects = {row["object"] for row in snapshot["views"]["ctx_points"]["rows"] if row["owner"] in owners}
    represented_classes = {entities[entities[obj]["links"]["class_obj"]["ref"]]["origin"].get("qualname")
                           for obj in objects if "class_obj" in entities[obj].get("links", {})}
    assert "subject.Descriptor" in represented_classes
    hook_edge = ("subject.Descriptor.__set_name__", "subject.hook")
    assert (hook_edge in _edges(bundle)) is expected_hook
