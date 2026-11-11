"""Finite real-source construction hooks; no upstream package execution.

Concrete source golds are independent CPython executions. Abstract late-input
growth/replay below are explicitly controlled native experiments, not claims
that concrete type() keeps watching its input dictionary after creation.
"""
from contextlib import redirect_stdout
from io import StringIO

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.context import Ctx
from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
from pythonstan.analysis.pointer.kcfa.heap_model import key
from pythonstan.analysis.pointer.kcfa.object import DictObject, InstanceObject
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from pythonstan.analysis.pointer.kcfa.processor.class_protocol import ClassProtocolProcessor, is_type_builtin
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline
from tests.round12_semantics.test_descriptor_native import _analyze, _edges, _list_strings


PREFIX = '''events = []
def chosen():
    events.append("chosen")
    return 7
def poison():
    events.append("poison")
    return 99
class Descriptor:
    def __init__(self, callback): self.callback = callback
    def __set_name__(self, owner, name):
        events.append(name)
        owner.callback = self.callback
'''

CONSTRUCTORS = {
    "type": 'Owner = type("Owner", (), {"field": Descriptor(chosen)})\n',
    "type_new": 'Owner = type.__new__(type, "Owner", (), {"field": Descriptor(chosen)})\n',
    "class_statement": 'class Owner:\n    field = Descriptor(chosen)\n',
}


def _module_values(bundle, name):
    snap = bundle.data["snapshot"]
    entities = {e["id"]: e for e in snap["entities"]}
    owners = {e["id"] for e in entities.values() if e["kind"] == "ctx"
              and e.get("attributes", {}).get("name") == name
              and e.get("origin", {}).get("qualname") == "subject"}
    return [entities[r["object"]].get("attributes", {}).get("value")
            for r in snap["views"]["ctx_points"]["rows"] if r["owner"] in owners]


def _assert_no_poison(bundle):
    assert not any(callee == "subject.poison" for _, callee in _edges(bundle))
    assert "poison" not in _list_strings(bundle, "events")
    assert 99 not in _module_values(bundle, "result")


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("constructor", tuple(CONSTRUCTORS))
def test_actual_construction_source_preserves_hook_result_call_and_effect(tmp_path, context_policy, constructor):
    source = PREFIX + CONSTRUCTORS[constructor] + "result = Owner.callback()\n"
    concrete = {}
    exec(compile(source, "<trusted-class-construction>", "exec"), concrete)
    assert concrete["result"] == 7 and concrete["events"] == ["field", "chosen"]
    bundle = _analyze(tmp_path, source, context_policy=context_policy, enabled=False)
    assert 7 in _module_values(bundle, "result")
    assert any(c == "subject.Descriptor.__set_name__" for _, c in _edges(bundle))
    assert any(c == "subject.chosen" for _, c in _edges(bundle))
    assert {"field", "chosen"} <= _list_strings(bundle, "events")
    _assert_no_poison(bundle)


@pytest.mark.parametrize("constructor", ["type", "type_new"])
def test_ignored_class_result_still_has_construction_effects(tmp_path, constructor):
    declaration = CONSTRUCTORS[constructor].removeprefix("Owner = ")
    source = PREFIX.replace("owner.callback = self.callback", "self.callback()") + declaration
    concrete = {}
    exec(compile(source, "<trusted-unused-class-result>", "exec"), concrete)
    assert concrete["events"] == ["field", "chosen"]
    bundle = _analyze(tmp_path, source, enabled=False)
    assert any(c == "subject.Descriptor.__set_name__" for _, c in _edges(bundle))
    assert any(c == "subject.chosen" for _, c in _edges(bundle))
    assert {"field", "chosen"} <= _list_strings(bundle, "events")
    _assert_no_poison(bundle)


CACHED_PROPERTY = '''events = []
def chosen(instance):
    events.append("chosen")
    return 7
def poison(instance):
    events.append("poison")
    return 99
class CachedProperty:
    def __init__(self, callback): self.real_func = callback
    def __set_name__(self, owner, name):
        self.name = name
        self.func = self.real_func
    def __get__(self, instance, owner):
        result = self.func(instance)
        instance.__dict__[self.name] = result
        return result
Owner = type("Owner", (), {"cached": CachedProperty(chosen)})
result = Owner().cached
'''


@pytest.mark.parametrize("context_policy", ["0-cfa", "2-cfa"])
def test_django_cached_property_shape_initializes_callable_and_own_cache(tmp_path, context_policy):
    concrete = {}
    exec(compile(CACHED_PROPERTY, "<trusted-cached-property-shape>", "exec"), concrete)
    assert concrete["result"] == 7 and concrete["events"] == ["chosen"]
    bundle = _analyze(tmp_path, CACHED_PROPERTY, context_policy=context_policy, enabled=False)
    assert 7 in _module_values(bundle, "result")
    assert any(c == "subject.CachedProperty.__set_name__" for _, c in _edges(bundle))
    assert any(c == "subject.chosen" for _, c in _edges(bundle))
    assert "chosen" in _list_strings(bundle, "events")
    _assert_no_poison(bundle)


def test_same_descriptor_under_two_own_names_retains_both_name_inputs(tmp_path):
    source = PREFIX + '''descriptor = Descriptor(chosen)
Owner = type("Owner", (), {"first": descriptor, "second": descriptor})
result = Owner.callback()
'''
    concrete = {}
    exec(compile(source, "<trusted-two-own-names>", "exec"), concrete)
    assert concrete["events"] == ["first", "second", "chosen"]
    bundle = _analyze(tmp_path, source, enabled=False)
    assert {"first", "second", "chosen"} <= _list_strings(bundle, "events")
    assert 7 in _module_values(bundle, "result")
    _assert_no_poison(bundle)


@pytest.mark.parametrize("body", [
    'Owner = type("Owner", (), {})\nOwner.field = Descriptor(poison)\n',
    'class Base:\n    pass\nBase.field = Descriptor(poison)\nOwner = type("Owner", (Base,), {})\n',
])
def test_later_class_attributes_and_inherited_values_do_not_invent_set_name(tmp_path, body):
    source = PREFIX + body
    concrete = {}
    exec(compile(source, "<trusted-no-construction-namespace-member>", "exec"), concrete)
    assert concrete["events"] == []
    bundle = _analyze(tmp_path, source, enabled=False)
    assert not any(c == "subject.Descriptor.__set_name__" for _, c in _edges(bundle))
    assert _list_strings(bundle, "events") == set()
    _assert_no_poison(bundle)


def test_unrelated_namespace_descriptor_does_not_become_selected_own_member(tmp_path):
    source = PREFIX + '''class NoiseDescriptor:
    def __set_name__(self, owner, name): poison()
unrelated = {"field": NoiseDescriptor()}
Owner = type("Owner", (), {"field": Descriptor(chosen)})
result = Owner.callback()
'''
    concrete = {}
    exec(compile(source, "<trusted-unrelated-namespace>", "exec"), concrete)
    assert concrete["result"] == 7 and concrete["events"] == ["field", "chosen"]
    bundle = _analyze(tmp_path, source, enabled=False)
    assert 7 in _module_values(bundle, "result")
    assert not any(c == "subject.NoiseDescriptor.__set_name__" for _, c in _edges(bundle))
    _assert_no_poison(bundle)


def _native(tmp_path, source):
    path = tmp_path / "subject.py"
    path.write_text(source)
    World.setup()
    config = Config(context_policy="0-cfa", enable_ai_intrinsics=False,
        enable_synthetic_method_contexts=False, work_scheduling_policy="fair",
        max_iterations=50000, log_level="ERROR")
    with redirect_stdout(StringIO()):
        pipeline = Pipeline(config={"filename": str(path), "project_path": str(tmp_path),
            "library_paths": [], "import_level": 0, "mock_libs": False, "time_count": False,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                          "options": config.to_dict()}]})
        driver = pipeline.analysis_manager.get_analyzer("pta")
        driver = getattr(driver, "driver", driver)
        driver.solver.enable_inspector_boundaries()
        pipeline.run()
    assert driver.solver._stats["converged"] == 1
    return driver


def _variable(driver, name):
    owners = [owner for owner in driver.state._env if isinstance(owner, Ctx)
              and isinstance(owner.content, Variable) and owner.content.name == name
              and owner.scope.stmt.get_qualname() == "subject"]
    assert len(owners) == 1
    return owners[0]


@pytest.mark.parametrize("new_field", [False, True], ids=["existing-field-late-value", "new-field-late-value"])
def test_controlled_abstract_namespace_growth_activates_persistent_hooks(tmp_path, new_field):
    # The concrete initial source is legal and has no hook. Abstract growth
    # describes a later-discovered possible *construction input*, not a claim
    # about mutation timing in that single concrete execution.
    source = PREFIX.replace("owner.callback = self.callback", "self.callback()")
    source += 'later_descriptor = Descriptor(chosen)\nnamespace = ' + ('{}' if new_field else '{"field": None}')
    source += '\nOwner = type("Owner", (), namespace)\n'
    concrete = {}
    exec(compile(source, "<trusted-initial-construction-input>", "exec"), concrete)
    assert concrete["events"] == []
    driver = _native(tmp_path, source)
    solver, state = driver.solver, driver.state
    assert not any(e.callee.stmt.get_qualname() == "subject.Descriptor.__set_name__"
                   for e in state.call_graph.edges)
    namespace_ctx, descriptor_ctx = _variable(driver, "namespace"), _variable(driver, "later_descriptor")
    mapping, = [obj for obj in state.get_points_to(namespace_ctx) if isinstance(obj, DictObject)]
    descriptor, = [obj for obj in state.get_points_to(descriptor_ctx) if isinstance(obj, InstanceObject)]
    target = state.get_field(namespace_ctx.scope, namespace_ctx.context, mapping, key("field"))
    solver.handle_new_points_to(target, namespace_ctx.scope, PointsToSet.singleton(descriptor))
    solver.solve_to_fixpoint()
    assert solver._stats["converged"] == 1 and solver._pending_work_count() == 0
    assert any(e.callee.stmt.get_qualname() == "subject.Descriptor.__set_name__"
               for e in state.call_graph.edges)
    assert any(e.callee.stmt.get_qualname() == "subject.chosen" for e in state.call_graph.edges)


def test_replayed_construction_reuses_owner_and_hook_subscriptions(tmp_path):
    driver = _native(tmp_path, PREFIX + CONSTRUCTORS["type"] + "result = Owner.callback()\n")
    solver, state = driver.solver, driver.state
    processor, = [p for p in solver.processor.processors if isinstance(p, ClassProtocolProcessor)]
    (scope, site), = processor._dynamic_classes
    call, = [call for registered_scope, call in state.constraints._constraints
             if registered_scope == scope and isinstance(call, CallConstraint)
             and call.call_site == site]
    callee = state.get_variable(scope, scope.context, call.callee)
    builtin, = [obj for obj in state.get_points_to(callee) if is_type_builtin(obj)]
    before = (len(processor._dynamic_classes), len(processor._namespace_edges),
              len(solver._construction_set_name_sources), len(state.pointer_flow_graph.edges))
    for _ in range(3):
        processor._construct(solver, scope, call, builtin, 0)
        solver.solve_to_fixpoint()
    after = (len(processor._dynamic_classes), len(processor._namespace_edges),
             len(solver._construction_set_name_sources), len(state.pointer_flow_graph.edges))
    assert before == after
