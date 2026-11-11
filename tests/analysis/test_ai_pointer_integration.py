"""Historical pointer/AI scenarios exercised through the maintained live API.

Original test IDs are retained. The original mocks only returned configured
sets; these tests now assert real points-to, field, call, and summary facts.
"""
import ast
from pythonstan.analysis.pointer.ai import AIState, AbsVal, AISummaryEngine, AnalysisBudget
from pythonstan.analysis.pointer.kcfa.heap_model import attr, elem, key, value, unknown
from pythonstan.analysis.pointer.kcfa.object import AllocKind
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from pythonstan.ir.ir_statements import IRCall
from legacy_ai_support import pointer_program, function_names, callees, assert_weak_write_preserved, assert_exception_alias_flow


class TestStrongVsWeakUpdates:
    def test_strong_update_on_singleton(self, pointer_program):
        assert len(pointer_program.points("x")) == 1
        assert_weak_write_preserved(pointer_program)

    def test_weak_update_on_non_singleton(self, pointer_program):
        selected = pointer_program.points("x").union(pointer_program.points("y"))
        assert len(selected) == 2
        assert_weak_write_preserved(pointer_program)
        assert_weak_write_preserved(pointer_program, "y")

    def test_imprecise_results_force_weak_updates(self, pointer_program):
        assert any(obj.kind == AllocKind.UNKNOWN for obj in pointer_program.points("unknown_obj"))
        assert_weak_write_preserved(pointer_program, "unknown_obj")


class TestIndirectCallResolution:
    def test_callee_narrowing_with_precise_results(self, pointer_program):
        assert callees(pointer_program, "polymorphic") == {"subject.func_a", "subject.func_b"}

    def test_callee_over_approximation_with_imprecise_results(self, pointer_program):
        assert pointer_program.points("unknown_obj")
        assert any(obj.kind == AllocKind.UNKNOWN for obj in pointer_program.points("unknown_obj"))

    def test_context_sensitive_callee_resolution(self, pointer_program):
        assert ("subject", "subject.Service.method_x") in pointer_program.edges
        assert ("subject.Service.method_x", "subject.func_a") in pointer_program.edges


class TestAttributeResolution:
    def test_attribute_load_with_known_pointsto(self, pointer_program):
        fields = pointer_program.query.field_of_objects(pointer_program.points("x"), "field")
        assert function_names(fields) == {"subject.func_a"}

    def test_attribute_store_with_field_sensitivity(self, pointer_program):
        q = pointer_program.query
        assert function_names(q.field_of_objects(pointer_program.points("x"), "field")) == {"subject.func_a"}
        assert function_names(q.field_of_objects(pointer_program.points("x"), "other")) == {"subject.func_b"}
        assert function_names(q.subscript_of_objects(pointer_program.points("items"), 0)) == {"subject.func_a"}
        assert function_names(q.subscript_of_objects(pointer_program.points("routes"), "b")) == {"subject.func_b"}

    def test_dynamic_attribute_access_fallback(self, pointer_program):
        obj = next(iter(pointer_program.points("x")))
        state = AIState().write_field_value(obj, unknown(), AbsVal.from_int(42))
        result, _ = state.read_field_value(obj, attr("unseen_name"), None, None)
        assert result.get_int_lattice().get_consts() == frozenset({42})


class TestPathPruning:
    def test_may_alias_information(self, pointer_program):
        q, scope = pointer_program.query, pointer_program.module
        assert q.may_alias("a", "b", scope)
        assert not q.may_alias("a", "c", scope)
        assert not q.may_alias("b", "c", scope)

    def test_alias_symmetry(self, pointer_program):
        q, scope = pointer_program.query, pointer_program.module
        for a, b in [("a", "b"), ("a", "c"), ("b", "c")]:
            assert q.may_alias(a, b, scope) == q.may_alias(b, a, scope)

    def test_imprecise_alias_over_approximation(self, pointer_program):
        q, scope = pointer_program.query, pointer_program.module
        assert q.may_alias("unknown_obj", "x", scope)
        assert q.may_alias("x", "unknown_obj", scope)

    def test_context_sensitive_aliasing(self, pointer_program):
        assert pointer_program.query.may_alias("service", "self_alias", pointer_program.module)
        receiver = pointer_program.points("self", "subject.Service.method_x")
        assert receiver.intersection(pointer_program.points("service"))


class TestFallbackBehaviors:
    def test_unknown_call_site_fallback(self, pointer_program):
        call = IRCall(ast.parse("missing()").body[0].value)
        assert pointer_program.query.callees_of(call, pointer_program.module) == set()

    def test_unknown_variable_fallback(self, pointer_program):
        assert pointer_program.points("never_assigned").is_empty()
        assert pointer_program.points("unknown_obj")

    def test_digest_version_availability(self, pointer_program):
        # The live adapter has no immutable dependency token. Caching must be
        # disabled rather than inventing a version string as the old mock did.
        engine = AISummaryEngine(pointer_program.adapter, AnalysisBudget())
        assert engine._cache_dependency_token() == (False, None)
        assert pointer_program.ai.get_summary("subject.monomorphic").ret


class TestCallGraphIntegration:
    def test_call_graph_successors(self, pointer_program):
        assert callees(pointer_program, "monomorphic") == {"subject.specific_method"}

    def test_indirect_call_with_call_graph(self, pointer_program):
        assert callees(pointer_program, "polymorphic") == {"subject.func_a", "subject.func_b"}
        assert pointer_program.ai.get_summary("subject.polymorphic").ret


class TestRealisticScenarios:
    def test_object_method_call_scenario(self, pointer_program):
        assert ("subject.Service.method_x", "subject.func_a") in pointer_program.edges
        assert pointer_program.ai.get_summary("subject.Service.method_x").ret

    def test_container_element_access_scenario(self, pointer_program):
        q = pointer_program.query
        assert function_names(q.subscript_of_objects(pointer_program.points("items"), 0)) == {"subject.func_a"}
        assert function_names(q.subscript_of_objects(pointer_program.points("routes"), "b")) == {"subject.func_b"}

    def test_exception_handling_with_aliases(self, tmp_path):
        assert_exception_alias_flow(tmp_path)
