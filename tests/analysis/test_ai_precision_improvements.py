"""Live PTA/AI precision obligations replacing hand-configured mock answers."""
from pythonstan.analysis.pointer.ai import AIState, AbsVal
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from legacy_ai_support import (
    pointer_program, function_names, callees, assert_weak_write_preserved,
    analyze_program, assert_exception_alias_flow,
)


class TestObjectFieldPrecision:
    def test_singleton_object_strong_updates(self, pointer_program):
        # A singleton address set is not a proof of concrete allocation uniqueness.
        assert len(pointer_program.points("x")) == 1
        assert_weak_write_preserved(pointer_program)

    def test_non_singleton_requires_weak_updates(self, pointer_program):
        assert len(pointer_program.points("x").union(pointer_program.points("y"))) == 2
        assert_weak_write_preserved(pointer_program)

    def test_container_element_precision(self, pointer_program):
        q = pointer_program.query
        first = q.subscript_of_objects(pointer_program.points("items"), 0)
        second = q.subscript_of_objects(pointer_program.points("items"), 1)
        assert function_names(first) == {"subject.func_a"}
        assert function_names(second) == {"subject.func_b"}
        assert first.intersection(second).is_empty()


class TestIndirectCallPrecision:
    def test_polymorphic_call_narrowing(self, pointer_program):
        assert callees(pointer_program, "polymorphic") == {"subject.func_a", "subject.func_b"}

    def test_monomorphic_call_resolution(self, pointer_program):
        assert callees(pointer_program, "monomorphic") == {"subject.specific_method"}

    def test_factory_method_precision(self, pointer_program):
        assert callees(pointer_program, "factory") == {"subject.create_instance"}
        assert pointer_program.points("factory_result")


class TestAliasAnalysisPrecision:
    def test_non_aliasing_enables_independent_updates(self, pointer_program):
        q = pointer_program.query
        assert not q.may_alias("x", "y", pointer_program.module)
        assert function_names(q.field_of_objects(pointer_program.points("x"), "field")) == {"subject.func_a"}
        assert function_names(q.field_of_objects(pointer_program.points("y"), "field")) == {"subject.func_b"}

    def test_known_aliasing_requires_coordination(self, pointer_program):
        q = pointer_program.query
        assert q.may_alias("a", "b", pointer_program.module)
        assert q.field_of_objects(pointer_program.points("a"), "field") == q.field_of_objects(pointer_program.points("b"), "field")

    def test_must_not_alias_optimization(self, pointer_program):
        assert not pointer_program.query.may_alias("x", "y", pointer_program.module)
        assert pointer_program.points("x").intersection(pointer_program.points("y")).is_empty()


class TestPathSensitivePrecision:
    def test_conditional_type_refinement(self, pointer_program):
        assert callees(pointer_program, "polymorphic") == {"subject.func_a", "subject.func_b"}
        assert "subject.specific_method" not in callees(pointer_program, "polymorphic")

    def test_exception_path_precision(self, tmp_path):
        assert_exception_alias_flow(tmp_path)


class TestBenchmarkBasedPrecision:
    def test_oop_method_dispatch_precision(self, pointer_program):
        assert ("subject.Service.method_x", "subject.func_a") in pointer_program.edges
        assert ("subject.Service.method_x", "subject.func_b") not in pointer_program.edges
        assert pointer_program.ai.get_summary("subject.Service.method_x").ret

    def test_closure_variable_precision(self, pointer_program):
        assert ("subject.closure_factory.closure", "subject.func_a") in pointer_program.edges
        assert pointer_program.ai.get_summary("subject.closure_factory.closure").ret

    def test_generator_state_precision(self, tmp_path):
        with analyze_program(tmp_path, '''
def callback():
    return "done"
def generate():
    yield callback
it = generate()
f = next(it)
result = f()
''') as program:
            assert ("subject", "subject.callback") in program.edges
            assert function_names(program.points("f")) == {"subject.callback"}
            assert program.ai.get_summary("subject.generate") is not None


class TestDataflowPrecision:
    def test_def_use_chain_precision(self, pointer_program):
        q = pointer_program.query
        assert function_names(q.field_of_objects(pointer_program.points("a"), "field")) == {"subject.func_a"}
        assert ("subject.Service.method_x", "subject.func_a") in pointer_program.edges

    def test_interprocedural_dataflow_precision(self, pointer_program):
        assert pointer_program.constants("mono_result") == {"specific"}
        summary = pointer_program.ai.get_summary("subject.monomorphic")
        assert {obj.value for obj in summary.ret if hasattr(obj, "value")} == {"specific"}


class TestPrecisionMetrics:
    def test_points_to_set_size_reduction(self, pointer_program):
        assert len(pointer_program.points("x")) == 1
        assert len(pointer_program.points("x").union(pointer_program.points("y"))) == 2

    def test_call_target_reduction(self, pointer_program):
        assert len(callees(pointer_program, "monomorphic")) == 1
        assert len(callees(pointer_program, "polymorphic")) == 2

    def test_field_sensitivity_improvement(self, pointer_program):
        q = pointer_program.query
        first = q.field_of_objects(pointer_program.points("x"), "field")
        second = q.field_of_objects(pointer_program.points("x"), "other")
        assert function_names(first) == {"subject.func_a"}
        assert function_names(second) == {"subject.func_b"}
        assert first.intersection(second).is_empty()
