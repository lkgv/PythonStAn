"""Actual native-key descriptor calls, without synthetic source-body roots.

Desired positive obligations deliberately fail until the production native
special-method subscription supports descriptor binding. Missing callbacks
must never be frozen as the gold result of this regression.
"""
from contextlib import redirect_stdout
import hashlib
from io import StringIO

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline
from pythonstan_inspector.adapter import capture
from pythonstan_inspector.contract import QueryRequest
from pythonstan_inspector.core import query
from pythonstan_inspector.storage import load_bundle, write_bundle


HASH_DESCRIPTOR_SOURCE = '''observed = []
def descriptor_callback():
    observed.append("descriptor")
def hash_callback():
    observed.append("hash")
    return 1
class HashDescriptor:
    def __get__(self, instance, owner):
        descriptor_callback()
        return hash_callback
class Key:
    __hash__ = HashDescriptor()
mapping = {}
result = mapping.get(Key())
'''


def _analyze(tmp_path, source, *, context_policy="0-cfa", enabled=False):
    filename = tmp_path / "subject.py"
    filename.write_text(source)
    digest = hashlib.sha256(filename.read_bytes()).hexdigest()
    World.setup()
    config = Config(context_policy=context_policy, enable_ai_intrinsics=enabled,
        enable_synthetic_method_contexts=False, work_scheduling_policy="fair",
        max_iterations=50000, log_level="ERROR")
    with redirect_stdout(StringIO()):
        pipeline = Pipeline(config={"filename": str(filename), "project_path": str(tmp_path),
            "library_paths": [], "import_level": 0, "mock_libs": False, "time_count": False,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                          "options": config.to_dict()}]})
        driver = pipeline.analysis_manager.get_analyzer("pta")
        driver = getattr(driver, "driver", driver)
        driver.solver.enable_inspector_boundaries()
        pipeline.run()
    assert driver.solver._stats["converged"] == 1
    snapshot = capture(driver.solver, run_metadata={
        "run_id": "native-descriptor-regression", "configuration": config.to_dict(),
        "source_validation": "pre_post_verified", "inputs": [{"path": str(filename),
            "sha256_before": digest, "sha256_after": hashlib.sha256(filename.read_bytes()).hexdigest()}],
        "observed_residual_effect_families": sorted({effect
            for row in driver.solver.state._residual_effects.values()
            for effect in row.get("effects", ())})})
    return load_bundle(write_bundle(snapshot, tmp_path / "inspection"))


def _edges(bundle):
    snapshot = bundle.data["snapshot"]
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    result = query(bundle, QueryRequest("show", {"view": "call_edges"}))
    assert result["query_complete"] is True
    assert snapshot["views"]["call_edges"]["coverage"]["registry"] == "complete"
    rows = result["evidence"]
    edges = set()
    for row in rows:
        callsite = entities[row["callsite"]]
        caller = entities[callsite["links"]["scope"]["ref"]]
        callee = entities[row["callee"]]
        edges.add((caller["origin"].get("qualname"), callee["origin"].get("qualname")))
    return edges


def _list_strings(bundle, name):
    """Decode only the selected current frozen heap/points relations."""
    snapshot = bundle.data["snapshot"]
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    points = snapshot["views"]["ctx_points"]["rows"]
    variables = {entity["id"] for entity in entities.values()
                 if entity["kind"] == "ctx" and entity["attributes"].get("name") == name}
    lists = {row["object"] for row in points if row["owner"] in variables}
    fields = {entity["id"] for entity in entities.values()
              if entity["kind"] == "ctx" and entity["attributes"].get("field_kind") == "elem"
              and entity["links"].get("object", {}).get("ref") in lists}
    return {entities[row["object"]]["attributes"].get("value") for row in points
            if row["owner"] in fields and type(entities[row["object"]]["attributes"].get("value")) is str}


def _assert_empty_varargs(bundle, scope_name):
    snapshot = bundle.data["snapshot"]
    entities = {entity["id"]: entity for entity in snapshot["entities"]}
    points = snapshot["views"]["ctx_points"]
    assert points["coverage"]["registry"] == "complete"
    args = {entity["id"] for entity in entities.values()
            if entity["kind"] == "ctx" and entity["attributes"].get("name") == "args"
            and entity["origin"].get("qualname") == scope_name}
    tuples = {row["object"] for row in points["rows"] if row["owner"] in args}
    assert tuples, "the real called function must have a represented vararg tuple"
    fields = {entity["id"] for entity in entities.values()
              if entity["kind"] == "ctx"
              and entity["attributes"].get("field_kind") in ("elem", "position")
              and entity["links"].get("object", {}).get("ref") in tuples}
    assert not any(row["owner"] in fields for row in points["rows"]), "descriptor result was rebound to the key"


@pytest.mark.parametrize("context_policy", ["0-cfa", "1-cfa", "2-cfa"])
@pytest.mark.parametrize("enabled", [False, True])
def test_native_hash_custom_descriptor_calls_and_effects(tmp_path, context_policy, enabled):
    concrete = {}
    exec(HASH_DESCRIPTOR_SOURCE, concrete)
    assert concrete["observed"] == ["descriptor", "hash"]
    bundle = _analyze(tmp_path, HASH_DESCRIPTOR_SOURCE, context_policy=context_policy, enabled=enabled)
    required = {("subject", "subject.HashDescriptor.__get__"),
                ("subject.HashDescriptor.__get__", "subject.descriptor_callback"),
                ("subject", "subject.hash_callback")}
    assert required <= _edges(bundle)
    assert {"descriptor", "hash"} <= _list_strings(bundle, "observed")


def test_native_descriptor_returned_function_does_not_receive_key_argument(tmp_path):
    # A user descriptor returns an ordinary zero-argument callable. Adding the
    # original key as if this were an unbound source function is wrong binding.
    source = HASH_DESCRIPTOR_SOURCE.replace(
        "def hash_callback():", "def hash_callback(*args):").replace(
        'observed.append("hash")', 'observed.append(len(args))')
    concrete = {}
    exec(source, concrete)
    assert concrete["observed"] == ["descriptor", 0]
    bundle = _analyze(tmp_path, source)
    assert ("subject", "subject.hash_callback") in _edges(bundle)
    _assert_empty_varargs(bundle, "subject.hash_callback")


def test_native_implicit_hash_still_bypasses_instance_shadow(tmp_path):
    source = HASH_DESCRIPTOR_SOURCE.replace(
        "mapping = {}\nresult = mapping.get(Key())",
        "def instance_shadow():\n    observed.append('shadow')\n    return 2\n"
        "key = Key()\nkey.__hash__ = instance_shadow\nmapping = {}\nresult = mapping.get(key)")
    concrete = {}
    exec(source, concrete)
    assert concrete["observed"] == ["descriptor", "hash"]
    bundle = _analyze(tmp_path, source)
    assert ("subject", "subject.HashDescriptor.__get__") in _edges(bundle)
    assert ("subject", "subject.instance_shadow") not in _edges(bundle)


@pytest.mark.parametrize("wrapper,signature", [("staticmethod", ""), ("classmethod", "cls")])
def test_native_builtin_descriptor_hash_calls_use_declared_binding(tmp_path, wrapper, signature):
    source = f'''observed = []
def hash_callback():
    observed.append("hash")
    return 1
class Key:
    @{wrapper}
    def __hash__({signature}):
        return hash_callback()
mapping = {{}}
result = mapping.get(Key())
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["observed"] == ["hash"]
    bundle = _analyze(tmp_path, source)
    assert ("subject", "subject.Key.__hash__") in _edges(bundle)
    assert ("subject.Key.__hash__", "subject.hash_callback") in _edges(bundle)
    assert "hash" in _list_strings(bundle, "observed")


def test_late_descriptor_class_getter_and_returned_callable(tmp_path):
    source = '''observed = []
def descriptor_callback():
    observed.append("descriptor")
def hash_callback():
    observed.append("hash")
    return 1
class HashDescriptor:
    pass
class Key:
    __hash__ = HashDescriptor()
def getter(self, instance, owner):
    descriptor_callback()
    callback = hash_callback
    return callback
HashDescriptor.__get__ = getter
mapping = {}
result = mapping.get(Key())
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["observed"] == ["descriptor", "hash"]
    bundle = _analyze(tmp_path, source)
    assert {("subject", "subject.getter"),
            ("subject.getter", "subject.descriptor_callback"),
            ("subject", "subject.hash_callback")} <= _edges(bundle)
    assert {"descriptor", "hash"} <= _list_strings(bundle, "observed")


@pytest.mark.parametrize("wrapper,receiver,signature", [
    ("", "Handler()", "self, "),
    ("@classmethod", "Handler", "cls, "),
    ("@staticmethod", "Handler", ""),
])
def test_user_descriptor_returning_bound_or_static_callback(tmp_path, wrapper, receiver, signature):
    source = f'''observed = []
class Handler:
    {wrapper}
    def callback({signature}*args):
        observed.append("hash")
        return 1
class HashDescriptor:
    def __get__(self, instance, owner):
        observed.append("descriptor")
        return {receiver}.callback
class Key:
    __hash__ = HashDescriptor()
mapping = {{}}
result = mapping.get(Key())
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["observed"] == ["descriptor", "hash"]
    bundle = _analyze(tmp_path, source)
    assert ("subject", "subject.Handler.callback") in _edges(bundle)
    assert {"descriptor", "hash"} <= _list_strings(bundle, "observed")
    _assert_empty_varargs(bundle, "subject.Handler.callback")


def test_inherited_descriptor_receives_actual_owner_and_returned_callable_instance(tmp_path):
    source = '''observed = []
class Callback:
    def __call__(self):
        observed.append("hash")
        return 1
class HashDescriptor:
    def __get__(self, instance, owner):
        observed.append("descriptor")
        owner.marker()
        return Callback()
class Base:
    __hash__ = HashDescriptor()
class Key(Base):
    @staticmethod
    def marker():
        observed.append("owner")
mapping = {}
result = mapping.get(Key())
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["observed"] == ["descriptor", "owner", "hash"]
    bundle = _analyze(tmp_path, source)
    assert {("subject", "subject.HashDescriptor.__get__"),
            ("subject.HashDescriptor.__get__", "subject.Key.marker"),
            ("subject", "subject.Callback.__call__")} <= _edges(bundle)
    assert {"descriptor", "owner", "hash"} <= _list_strings(bundle, "observed")


def test_native_equality_descriptor_preserves_other_argument(tmp_path):
    source = '''observed = []
def equal_callback(other):
    observed.append("equal")
    other.marker()
    return True
class EqDescriptor:
    def __get__(self, instance, owner):
        observed.append("descriptor")
        return equal_callback
class Stored:
    __eq__ = EqDescriptor()
    def __hash__(self):
        return 1
class Query:
    def __hash__(self):
        return 1
    def marker(self):
        observed.append("other")
mapping = {Stored(): "stored"}
result = mapping.get(Query())
'''
    concrete = {}
    exec(source, concrete)
    assert concrete["observed"] == ["descriptor", "equal", "other"]
    bundle = _analyze(tmp_path, source)
    assert {("subject", "subject.EqDescriptor.__get__"),
            ("subject", "subject.equal_callback"),
            ("subject.equal_callback", "subject.Query.marker")} <= _edges(bundle)
    assert {"descriptor", "equal", "other"} <= _list_strings(bundle, "observed")


def test_noncallable_descriptor_result_keeps_exception_obligation(tmp_path):
    source = HASH_DESCRIPTOR_SOURCE.replace("return hash_callback", "return None").replace(
        "result = mapping.get(Key())", "try:\n    result = mapping.get(Key())\nexcept TypeError:\n    result = None")
    concrete = {}
    exec(source, concrete)
    assert concrete["observed"] == ["descriptor"]
    bundle = _analyze(tmp_path, source)
    assert ("subject", "subject.HashDescriptor.__get__") in _edges(bundle)
    assert ("subject", "subject.hash_callback") not in _edges(bundle)
    assert "native_mapping_key_protocol" in bundle.data["run"]["observed_residual_effect_families"]
    assert "may_raise" in bundle.data["run"]["observed_residual_effect_families"]


@pytest.mark.parametrize("nested", [False, True])
def test_staticmethod_skips_class_namespace_and_captures_enclosing_function(tmp_path, nested):
    class_body = '''class Holder:
    callback = class_shadow
    @staticmethod
    def invoke():
        return callback()
'''
    if nested:
        class_body = "class Outer:\n" + "".join("    " + line + "\n" for line in class_body.splitlines())
    factory_body = "".join("    " + line + "\n" for line in class_body.splitlines())
    returned = "Outer.Holder.invoke" if nested else "Holder.invoke"
    source = '''observed = []
def actual():
    observed.append("actual")
    return 1
def class_shadow():
    observed.append("shadow")
    return 2
def factory(callback):
''' + factory_body + f"    return {returned}\nselected = factory(actual)\nresult = selected()\n"
    concrete = {}
    exec(source, concrete)
    assert concrete["observed"] == ["actual"]
    bundle = _analyze(tmp_path, source, context_policy="2-cfa")
    scope = "subject.factory.Outer.Holder.invoke" if nested else "subject.factory.Holder.invoke"
    assert (scope, "subject.actual") in _edges(bundle)
    assert (scope, "subject.class_shadow") not in _edges(bundle)
    assert "actual" in _list_strings(bundle, "observed")
