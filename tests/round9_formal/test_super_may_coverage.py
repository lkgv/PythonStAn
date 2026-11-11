"""Independent necessary-call and delayed-fact tests for super may coverage.

Expected calls follow the concrete source receiver MRO, not the implementation's
candidate closure. Extra may targets are allowed; these tests do not certify C3.
"""
import ast
from contextlib import contextmanager

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.heap_model import Field, FieldKind, attr
from pythonstan.analysis.pointer.kcfa.object import (
    AllocKind, AllocSite, ClassObject, ConstantObject, InstanceObject, ModuleObject,
    ObjectFactory,
)
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet, reset_object_table
from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.ir.ir_statements import IRClass, IRModule
from pythonstan.world.pipeline import Pipeline
from tests.analysis.legacy_ai_support import isolated_world


@contextmanager
def analyze_source(tmp_path, source, policy):
    filename = tmp_path / "independent_super.py"
    filename.write_text(source)
    with isolated_world():
        pipeline = Pipeline(config={
            "filename": str(filename), "project_path": str(tmp_path),
            "library_paths": [], "import_level": 0, "mock_libs": False,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                          "options": Config(context_policy=policy, max_iterations=20000,
                                            enable_ai_intrinsics=False, log_level="ERROR").to_dict()}],
        })
        pipeline.run()
        query = pipeline.analysis_manager.get_results("pta").query()
        assert query.get_statistics()["converged"] == 1
        yield query


SOURCE_CASES = [
    ('''
class A:
    def value(self): return 1
class B(A):
    def value(self): return super().value()
class C(A):
    def value(self): return 2
class D(B, C): pass
result = D().value()
''', "B.value", "C.value", 2),
    ('''
class A:
    @classmethod
    def value(cls): return 1
class B(A):
    @classmethod
    def value(cls): return super().value()
class C(A):
    @classmethod
    def value(cls): return cls.marker
class D(B, C): marker = 17
result = D.value()
''', "B.value", "C.value", 17),
    ('''
class A(type):
    def value(cls): return 1
class B(A):
    def value(cls): return super().value()
class C(A):
    def value(cls): return cls.marker
class D(B, C): pass
class Container(metaclass=D): marker = 23
result = Container.value()
''', "B.value", "C.value", 23),
    ('''
class A:
    @staticmethod
    def selected(): return 1
class B(A):
    def value(self): return super().selected()
class C(A):
    @staticmethod
    def selected(): return 31
class D(B, C): pass
result = D().value()
''', "B.value", "C.selected", 31),
    ('''
class A:
    def value(self): return 37
def make(base):
    class Made(base):
        def value(self): return super().value()
    return Made
B = make(A)
C = make(A)
class D(B, C): pass
result = D().value()
''', "make.Made.value", "make.Made.value", 37),
    ('''
class A:
    def value(self): return 39
def make(base):
    class Made(base):
        def value(self): return super().value()
    return Made
B = make(A)
C = make(B)
result = C().value()
''', "make.Made.value", "make.Made.value", 39),
]


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("source, caller, callee, expected", SOURCE_CASES)
def test_concrete_receiver_mro_necessary_edges(tmp_path, policy, source, caller, callee, expected):
    # This isolated synthetic execution supplies an independent semantic witness;
    # its observations are never supplied to the production static analysis.
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == expected
    with analyze_source(tmp_path, source, policy) as query:
        edges = {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
                 for edge in query.call_graph().edges}
        assert ("independent_super." + caller, "independent_super." + callee) in edges
        assert not query.get_semantic_status()["certificate_eligible"]


@pytest.fixture
def model():
    reset_object_table()
    context = CallStringContext((), 1)
    ir = IRModule("late_super", ast.parse(""), filename="late_super.py")
    module = ModuleObject(context, AllocSite(ir, AllocKind.MODULE), ir)
    scope = Scope.new(module, None, context, ir, None)
    state = PointerAnalysisState()
    state.set_internal_scope(module, scope)
    solver = PointerSolver(state, Config(max_iterations=20000, log_level="ERROR"), Processor())
    yield solver, state, scope, context
    reset_object_table()


def make_class(model, name, base_slots=0):
    _, state, scope, context = model
    args = ", ".join("base_" + name + "_" + str(i) for i in range(base_slots))
    node = ast.parse("class " + name + ("(" + args + ")" if args else "") + ": pass").body[0]
    ir = IRClass(name, node)
    variables = tuple(Variable("base_" + name + "_" + str(i), VariableKind.GLOBAL)
                      for i in range(base_slots))
    ir._kcfa_base_variables = variables
    cls = ClassObject(context, AllocSite(ir, AllocKind.CLASS), scope, ir)
    state.set_internal_scope(cls, Scope.new(cls, scope, context, ir, scope))
    return cls, variables


def add(model, variable, value):
    solver, _, scope, _ = model
    solver.handle_new_points_to(variable, scope, PointsToSet.singleton(value))
    solver.solve_to_fixpoint()


def base_location(model, variable):
    _, state, scope, context = model
    return state.get_variable(scope, context, variable)


def own_location(model, cls):
    _, state, scope, context = model
    return state.get_write_field(scope, context, cls, attr("selected"))


@pytest.mark.parametrize("order", ["lookup_first", "base_first", "field_first"])
def test_transitive_late_sibling_and_late_slot_alternative_are_never_lost(model, order):
    solver, state, scope, context = model
    a, _ = make_class(model, "A")
    b, bslots = make_class(model, "B", 1)
    c, cslots = make_class(model, "C", 1)
    d, dslots = make_class(model, "D", 2)
    e, eslots = make_class(model, "E", 1)
    receiver = InstanceObject(context, AllocSite("receiver", AllocKind.INSTANCE), d)
    proxy = ObjectFactory.create_super(context, "late proxy", b, receiver)
    payload = ConstantObject(context, AllocSite("c payload", AllocKind.CONSTANT), 41)
    later = ConstantObject(context, AllocSite("e payload", AllocKind.CONSTANT), 43)
    result = None
    if order == "lookup_first":
        result = state.get_field(scope, context, proxy, attr("selected"))
        solver.solve_to_fixpoint()
    if order == "field_first":
        add(model, own_location(model, c), payload)
    for variable, base in [(dslots[0], b), (bslots[0], a), (cslots[0], a), (eslots[0], a)]:
        add(model, base_location(model, variable), base)
    if result is None:
        result = state.get_field(scope, context, proxy, attr("selected"))
        solver.solve_to_fixpoint()
    add(model, base_location(model, dslots[1]), c)
    if order != "field_first":
        add(model, own_location(model, c), payload)
    assert payload in state.get_points_to(result)
    prior = set(state.get_points_to(result))
    add(model, own_location(model, e), later)
    add(model, base_location(model, dslots[1]), e)
    assert {payload, later} <= set(state.get_points_to(result))
    assert prior <= set(state.get_points_to(result))


def test_late_actual_metaclass_hierarchy_reaches_existing_class_receiver_super(model):
    solver, state, scope, context = model
    a, _ = make_class(model, "MetaA")
    b, bs = make_class(model, "MetaB", 1)
    c, cs = make_class(model, "MetaC", 1)
    d, ds = make_class(model, "MetaD", 2)
    container, _ = make_class(model, "Container")
    proxy = ObjectFactory.create_super(context, "meta proxy", b, container)
    result = state.get_field(scope, context, proxy, attr("selected"))
    solver.solve_to_fixpoint()
    payload = ConstantObject(context, AllocSite("meta payload", AllocKind.CONSTANT), 47)
    add(model, own_location(model, c), payload)
    for variable, base in [(bs[0], a), (cs[0], a), (ds[0], b), (ds[1], c)]:
        add(model, base_location(model, variable), base)
    meta_field = state.get_field(scope, context, container, Field(FieldKind.CLASS_METADATA, "metaclass"))
    add(model, meta_field, d)
    assert payload in state.get_points_to(result)
