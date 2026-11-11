"""Executable solver obligations migrated from the pre-implementation skeleton.

All former skeleton checks now require values, graph edges, or a fixed point.
The fixture only assembles public domain objects and the real solver.
"""
import ast
import pytest
from tests.pointer.kcfa.builders import make_scope, make_site
from pythonstan.analysis.pointer.kcfa import (
    PointerSolver, PointerAnalysisState, Config, AllocKind, AllocConstraint,
    CopyConstraint, LoadConstraint, StoreConstraint, PointsToSet, Variable,
    VariableKind, ContextSelector, CallStringContext, AbstractObject,
)
from pythonstan.analysis.pointer.kcfa.context import CallSite
from pythonstan.analysis.pointer.kcfa.heap_model import attr
from pythonstan.analysis.pointer.kcfa.processor import ComposeProcessor
from pythonstan.analysis.pointer.kcfa.solver import SolverQuery
from pythonstan.analysis.pointer.kcfa.context_selector import parse_policy
from pythonstan.ir.ir_statements import IRCall


class Model:
    def __init__(self, config=None):
        self.scope = make_scope("solver_test", "module")
        self.context = self.scope.context
        self.state = PointerAnalysisState()
        self.state.set_internal_scope(self.scope.obj, self.scope)
        self.solver = PointerSolver(self.state, config or Config(), ComposeProcessor([]),
                                    context_selector=ContextSelector(parse_policy((config or Config()).context_policy)))

    def var(self, name):
        return Variable(name, VariableKind.GLOBAL)

    def location(self, name, context=None):
        return self.state.get_variable(self.scope, context or self.context, self.var(name))

    def object(self):
        obj = AbstractObject(self.context, make_site())
        self.state.obj_scope[obj] = self.scope
        return obj

    def seed(self, name, *objects):
        self.solver.handle_new_points_to(self.location(name), self.scope, PointsToSet.from_objects(objects))

    def field(self, obj, name="f"):
        return self.state.get_field(self.scope, self.context, obj, attr(name))

    def seed_field(self, obj, *values):
        self.solver.handle_new_points_to(self.field(obj), self.scope, PointsToSet.from_objects(values))

    def add(self, constraint, context=None):
        self.solver.add_constraint(self.scope, context or self.context, constraint)

    def copy(self, source="src", target="tgt"):
        self.add(CopyConstraint(self.var(source), self.var(target)))

    def load(self, base="base", target="tgt"):
        self.add(LoadConstraint(self.var(base), attr("f"), self.var(target)))

    def store(self, base="base", source="src"):
        self.add(StoreConstraint(self.var(base), attr("f"), self.var(source)))

    def solve(self):
        self.solver.solve_to_fixpoint()

    def points(self, name):
        return self.state.get_points_to(self.location(name))


@pytest.fixture
def model():
    return Model()


def _analyzed_call():
    # Source-level lowering is required for parameter and return bindings.
    from tests.pointer.kcfa.test_call_semantics import _analyze
    analysis, module = _analyze("def identity(x):\n    return x\nresult = identity(42)\n")
    query = analysis.solver.query()
    from pythonstan.analysis.pointer.kcfa.object import ConstantObject
    values = query.points_to_at("result", module)
    assert {obj.value for obj in values if isinstance(obj, ConstantObject)} == {42}
    assert any(edge.callee.stmt.get_qualname().endswith("identity")
               for edge in query.call_graph().edges)
    return analysis, module


class TestPointerSolverInitialization:
    def test_basic_initialization(self, model):
        assert model.solver.state is model.state
        assert model.solver.config == Config()
        assert model.solver._iteration == 0

    def test_worklist_starts_empty(self, model):
        assert model.state._worklist.empty()

    def test_statistics_initialized(self, model):
        assert model.solver._stats["iterations"] == 0
        assert model.solver._stats["constraints_applied"] == 0


class TestSolverImplementedMethods:
    """One positive semantic obligation per formerly unimplemented operation."""
    def test_add_constraint(self, model):
        obj = model.object()
        model.seed("src", obj)
        model.copy()
        model.solve()
        assert model.points("tgt") == PointsToSet.singleton(obj)

    def test_solve_to_fixpoint(self, model):
        model.solve()
        assert model.solver._stats["converged"] == 1
        assert model.solver._pending_work_count() == 0

    def test_process_variable(self, model):
        obj = model.object()
        model.seed("src", obj)
        model.solve()
        assert model.points("src") == PointsToSet.singleton(obj)

    def test_apply_constraint(self, model):
        owner, value = model.object(), model.object()
        model.load()
        model.seed("base", owner)
        model.seed_field(owner, value)
        model.solve()
        assert value in model.points("tgt")

    def test_apply_copy(self, model):
        obj = model.object()
        model.copy()
        model.seed("src", obj)
        model.solve()
        assert obj in model.points("tgt")

    def test_apply_load(self, model):
        owner, value = model.object(), model.object()
        model.seed("base", owner)
        model.seed_field(owner, value)
        model.solve()
        model.load()
        model.solve()
        assert value in model.points("tgt")

    def test_apply_store(self, model):
        owner, value = model.object(), model.object()
        model.seed("base", owner)
        model.seed("src", value)
        model.store()
        model.solve()
        assert value in model.state.get_points_to(model.field(owner))

    def test_apply_alloc(self, model):
        site = make_site()
        model.add(AllocConstraint(model.var("tgt"), site))
        model.solve()
        assert {obj.alloc_site for obj in model.points("tgt")} == {site}

    def test_apply_call(self):
        _analyzed_call()

    def test_apply_return(self):
        _analyzed_call()


class TestSolverQuery:
    def test_query_creation(self, model):
        assert isinstance(model.solver.query(), SolverQuery)

    def test_query_points_to_empty(self, model):
        assert model.solver.query().points_to_at("absent", model.scope.stmt).is_empty()

    def test_query_points_to_with_data(self, model):
        obj = model.object()
        model.seed("x", obj)
        model.solve()
        assert obj in model.solver.query().points_to_at("x", model.scope.stmt)

    def test_query_get_field_empty(self, model):
        obj = model.object()
        assert model.solver.query().field_of_objects(PointsToSet.singleton(obj), "f").is_empty()

    def test_query_get_field_with_data(self, model):
        owner, value = model.object(), model.object()
        model.seed_field(owner, value)
        model.solve()
        assert value in model.solver.query().field_of_objects(PointsToSet.singleton(owner), "f")

    @pytest.mark.parametrize("overlap", [False, True])
    def test_query_may_alias_no_overlap(self, model, overlap):
        first, second = model.object(), model.object()
        model.seed("x", first)
        model.seed("y", first if overlap else second)
        model.solve()
        assert model.solver.query().may_alias("x", "y", model.scope.stmt) is overlap

    def test_query_may_alias_with_overlap(self, model):
        shared = model.object()
        model.seed("x", shared)
        model.seed("y", shared)
        model.solve()
        assert model.solver.query().may_alias("x", "y", model.scope.stmt)

    def test_query_may_alias_partial_overlap(self, model):
        first, shared, third = model.object(), model.object(), model.object()
        model.seed("x", first, shared)
        model.seed("y", shared, third)
        model.solve()
        assert model.solver.query().may_alias("x", "y", model.scope.stmt)
        assert model.points("x") != model.points("y")

    def test_query_get_statistics(self, model):
        stats = model.solver.query().get_statistics()
        for name in ("num_variables", "num_objects", "num_heap_locations", "iterations", "constraints_applied"):
            assert stats[name] == 0


class TestSolverCopyConstraint:
    def test_copy_propagates_points_to_set(self, model):
        obj = model.object()
        model.seed("src", obj)
        model.solve()
        model.copy()
        model.solve()
        assert model.points("tgt") == PointsToSet.singleton(obj)

    def test_copy_is_monotonic(self, model):
        first, second = model.object(), model.object()
        model.seed("src", first)
        model.copy()
        model.solve()
        before = model.points("tgt")
        model.seed("src", second)
        model.solve()
        assert model.points("tgt") == before.union(PointsToSet.singleton(second))

    def test_copy_empty_source(self, model):
        model.copy()
        model.solve()
        assert model.points("tgt").is_empty()


class TestSolverLoadConstraint:
    def test_load_propagates_field_values(self, model):
        owner, value = model.object(), model.object()
        model.seed("base", owner)
        model.seed_field(owner, value)
        model.load()
        model.solve()
        assert model.points("tgt") == PointsToSet.singleton(value)

    def test_load_from_multiple_objects(self, model):
        first, second, v1, v2 = [model.object() for _ in range(4)]
        model.seed("base", first, second)
        model.seed_field(first, v1)
        model.seed_field(second, v2)
        model.load()
        model.solve()
        assert model.points("tgt") == PointsToSet.from_objects([v1, v2])

    def test_load_unknown_field(self, model):
        model.seed("base", model.object())
        model.load()
        model.solve()
        assert model.points("tgt").is_empty()


class TestSolverStoreConstraint:
    def test_store_updates_field(self, model):
        owner, value = model.object(), model.object()
        model.seed("base", owner)
        model.seed("src", value)
        model.store()
        model.solve()
        assert model.state.get_points_to(model.field(owner)) == PointsToSet.singleton(value)

    def test_store_to_multiple_objects(self, model):
        first, second, value = [model.object() for _ in range(3)]
        model.seed("base", first, second)
        model.seed("src", value)
        model.store()
        model.solve()
        assert value in model.state.get_points_to(model.field(first))
        assert value in model.state.get_points_to(model.field(second))

    def test_store_is_monotonic(self, model):
        owner, first, second = [model.object() for _ in range(3)]
        model.seed("base", owner)
        model.seed("src", first)
        model.store()
        model.solve()
        before = model.state.get_points_to(model.field(owner))
        model.seed("src", second)
        model.solve()
        assert model.state.get_points_to(model.field(owner)) == before.union(PointsToSet.singleton(second))


class TestSolverAllocConstraint:
    def test_alloc_creates_object(self, model):
        site = make_site()
        model.add(AllocConstraint(model.var("tgt"), site))
        model.solve()
        assert {obj.alloc_site for obj in model.points("tgt")} == {site}

    def test_alloc_uses_context(self, model):
        site = make_site()
        callsite = CallSite(IRCall(ast.parse("caller()").body[0].value))
        context = CallStringContext((callsite,), 2)
        model.add(AllocConstraint(model.var("tgt"), site), context)
        model.solve()
        assert {obj.context for obj in model.points("tgt")} == {context}

    def test_multiple_allocs_same_site(self, model):
        site = make_site()
        contexts = [CallStringContext((CallSite(IRCall(ast.parse(f"call{i}()").body[0].value)),), 2) for i in range(2)]
        for context in contexts:
            model.add(AllocConstraint(model.var("tgt"), site), context)
        model.solve()
        assert len(model.points("tgt")) == 2
        assert {obj.context for obj in model.points("tgt")} == set(contexts)


class TestSolverCallConstraint:
    def test_call_resolves_callees(self):
        _analyzed_call()

    def test_call_generates_parameter_constraints(self):
        analysis, module = _analyzed_call()
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject
        function = next(edge.callee.stmt for edge in analysis.solver.query().call_graph().edges
                        if edge.callee.stmt.get_qualname().endswith("identity"))
        values = analysis.solver.query().points_to_at("x", function)
        assert any(isinstance(obj, ConstantObject) and obj.value == 42 for obj in values)

    def test_call_generates_return_constraints(self):
        _analyzed_call()


class TestSolverReturnConstraint:
    def test_return_propagates_value(self):
        # Interprocedural returns now use contextual PFG edges, not the obsolete
        # single-scope ReturnConstraint path.
        _analyzed_call()


class TestSolverFixpoint:
    def test_fixpoint_simple_copy_chain(self, model):
        model.add(AllocConstraint(model.var("z"), make_site()))
        model.copy("z", "y")
        model.copy("y", "x")
        model.solve()
        assert len(model.points("x")) == 1
        assert model.points("x") == model.points("y") == model.points("z")

    def test_fixpoint_field_load_store(self, model):
        owner, value = model.object(), model.object()
        model.seed("base", owner)
        model.seed("src", value)
        model.store()
        model.load()
        model.solve()
        assert value in model.points("tgt")

    def test_fixpoint_max_iterations(self):
        model = Model(Config(max_iterations=1))
        model.add(AllocConstraint(model.var("x"), make_site()))
        model.copy("x", "y")
        model.solve()
        assert model.solver._iteration == 1
        assert model.solver._stats["converged"] == 0
        assert model.solver._stats["iteration_limit_reached"] == 1
        assert model.solver._pending_work_count() > 0

    def test_fixpoint_worklist_exhaustion(self, model):
        model.add(AllocConstraint(model.var("x"), make_site()))
        model.solve()
        assert model.solver._pending_work_count() == 0
        assert model.solver._stats["converged"] == 1
        assert len(model.points("x")) == 1


class TestSolverIntegration:
    def test_solve_simple_program(self, model):
        value_site = make_site()
        model.add(AllocConstraint(model.var("x"), make_site()))
        model.add(AllocConstraint(model.var("tmp"), value_site))
        model.store("x", "tmp")
        model.copy("x", "y")
        model.load("y", "z")
        model.solve()
        assert {obj.alloc_site for obj in model.points("z")} == {value_site}

    @pytest.mark.parametrize("policy", ["0-cfa", "1-cfa", "2-cfa"])
    def test_solve_with_different_policies(self, policy):
        model = Model(Config(context_policy=policy))
        model.add(AllocConstraint(model.var("x"), make_site()))
        model.copy("x", "y")
        model.solve()
        assert len(model.points("x")) == 1
        assert model.points("x") == model.points("y")
        assert model.solver._stats["converged"] == 1
