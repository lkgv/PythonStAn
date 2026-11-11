"""Migration helpers using the production frontend, solver, and PTA/AI drivers.

These are test harnesses, not a compatibility implementation of the removed
``pythonstan.analysis.ai`` prototype. Each analysis owns its World and points-to
object table for the duration of the observation.
"""
import ast
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from pythonstan.analysis.pointer.ai import AIState, AbsVal, AnalysisBudget, AISolver, SolverConfig
from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
from pythonstan.analysis.transform.three_address import ThreeAddressTransformer
from pythonstan.analysis.transform.ir import IRTransformer
from pythonstan.analysis.transform.block_cfg import BlockCFGBuilder
from pythonstan.ir.ir_statements import IRModule
from pythonstan.world import World


@contextmanager
def isolated_world():
    import pythonstan.analysis.pointer.kcfa.points_to_set as points
    world = World()
    previous = dict(world.__dict__)
    previous_table = points._global_table
    points._global_table = None
    World.setup()
    try:
        yield world
    finally:
        world.__dict__.clear()
        world.__dict__.update(previous)
        points._global_table = previous_table


class EmptyPtaQuery:
    """No external facts for a closed scalar test; never claims uniqueness."""
    def points_to(self, *args, **kwargs):
        return PointsToSet.empty()

    def field_points_to(self, *args, **kwargs):
        return PointsToSet.empty()

    def get_class_mro(self, *args, **kwargs):
        return []

    def may_have_attr(self, *args, **kwargs):
        return True


class CFGBackedSolver(AISolver):
    def __init__(self, cfg, **kwargs):
        self._provided_cfg = cfg
        super().__init__(**kwargs)

    def _build_cfg(self):
        return self._provided_cfg


def build_source(source, name="subject"):
    """Lower source with the real TAC/IR transforms and build its block CFG."""
    tree = ast.parse(source)
    from pythonstan.analysis.contracts import SourceModule
    module = IRModule(name, tree, name=name, filename=f"{name}.py", source_snapshot=SourceModule(name,f"{name}.py",source))
    tac = ThreeAddressTransformer().visit(tree)
    transformer = IRTransformer(module)
    transformer.process_stmts(tac.body)
    statements = transformer.get_stmts()
    builder = BlockCFGBuilder(module)
    builder.build_graph(statements)
    return module, statements, builder.cfg


def terminal_state(solver):
    """Join only terminal block observations, not arbitrary transient states."""
    if getattr(solver,"exit_state",None) is not None:
        return solver.exit_state
    states = [state for bid, state in solver.block_out_states.items()
              if not solver.cfg_adapter.get_successors(bid)]
    if not states:
        raise AssertionError("analysis produced no terminal state")
    result = states[0]
    for state in states[1:]:
        result = result.join(state)
    return result


def solve_source(source, initial_state=None, config=None, budget=None):
    with isolated_world():
        module, statements, cfg = build_source(source)
        query = EmptyPtaQuery()
        from pythonstan.analysis.pointer.kcfa.object import BuiltinFunctionObject, AllocKind, AllocSite
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        state = initial_state or AIState()
        if "len" not in state.env:
            builtin = BuiltinFunctionObject(CallStringContext(), AllocSite("len", AllocKind.BUILTIN), "len")
            state = state.set_var("len", AbsVal.from_addrs(frozenset({builtin})))
        solver = CFGBackedSolver(
            cfg, scope_ir=module, initial_state=state,
            pta_query=query, budget=budget or AnalysisBudget(),
            config=config or SolverConfig(),
        )
        solver.solve()
        return solver


def int_contains(value, number):
    """Explicit interval/constant concretization for integer assertions."""
    lattice = value.get_int_lattice()
    if lattice.is_bottom:
        return False
    if lattice.is_top:
        return True
    if lattice.consts:
        return number in lattice.consts
    return (lattice.lo is None or lattice.lo <= number) and (lattice.hi is None or number <= lattice.hi)


def int_exact(value, number):
    return value.get_int_lattice().get_consts() == frozenset({number})


@dataclass
class ProgramAnalysis:
    pipeline: object
    query: object
    ai: object
    module: object
    adapter: object

    @property
    def edges(self):
        return {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
                for edge in self.query.call_graph().edges}

    def scope(self, name):
        if name == self.module.get_qualname():
            return self.module
        scope = World().scope_manager.get_module(name)
        assert scope is not None, name
        return scope

    def points(self, name, scope=None, context=None):
        return self.query.points_to_at(name, self.module if scope is None else self.scope(scope), context)

    def constants(self, name, scope=None, context=None):
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject
        return {obj.value for obj in self.points(name, scope, context) if isinstance(obj, ConstantObject)}


@contextmanager
def analyze_program(tmp_path, source, *, pointer_options=None, ai_options=None):
    from pythonstan.analysis.pointer.kcfa.config import Config
    from pythonstan.analysis.pointer.kcfa.pta_query_adapter import PtaQueryAdapter
    from pythonstan.world.pipeline import Pipeline
    filename = Path(tmp_path) / "subject.py"
    filename.write_text(source)
    with isolated_world():
        config = Config(max_iterations=50000, log_level="ERROR", **(pointer_options or {}))
        options = {"type": "ai analysis", "pta_analysis_name": "pta", "log_level": "ERROR", **(ai_options or {})}
        pipeline = Pipeline(config={
            "filename": str(filename), "project_path": str(tmp_path),
            "library_paths": [], "import_level": 0, "mock_libs": False, "time_count": False,
            "analysis": [
                {"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"], "options": config.to_dict()},
                {"name": "ai", "id": "AIAnalysisDriver", "prev_analysis": ["pta"], "options": options},
            ],
        })
        if "plan_scopes" not in (ai_options or {}) and "entry_scope" not in (ai_options or {}):
            from dataclasses import replace
            driver = pipeline.analysis_manager.get_analyzer("ai")
            driver.ai_config = replace(driver.ai_config, plan_scopes=sorted(
                scope.get_qualname() for scope in World().scope_manager.get_scopes()))
        pipeline.run()
        query = pipeline.analysis_manager.get_results("pta").query()
        analyzer = pipeline.analysis_manager.get_analyzer("pta")
        adapter = PtaQueryAdapter(query._state, analyzer.class_hierarchy, analyzer.context_selector)
        yield ProgramAnalysis(pipeline, query, pipeline.analysis_manager.get_results("ai"),
                              World().get_entry_module(), adapter)


# Real source replaces fabricated MockPointerResults lookup tables. Both the
# PTA query and independent AI summary pass must be usable on each fixture.
POINTER_SOURCE = '''
def func_a():
    return "a"
def func_b():
    return "b"
def specific_method():
    return "specific"
class Box:
    pass
class Service:
    def method_x(self):
        return self.callback()
def create_instance():
    return Box()
def polymorphic(flag):
    if flag:
        target = func_a
    else:
        target = func_b
    return target()
def monomorphic():
    return specific_method()
def factory():
    return create_instance()
def closure_factory(callback):
    def closure():
        return callback()
    return closure
x = Box()
y = Box()
a = x
b = x
c = y
x.field = func_a
x.other = func_b
y.field = func_b
items = [func_a, func_b]
routes = {"a": func_a, "b": func_b}
service = Service()
service.callback = func_a
self_alias = service
method_result = service.method_x()
poly_result = polymorphic(True)
mono_result = monomorphic()
factory_result = factory()
closed = closure_factory(func_a)
closure_result = closed()
unknown_obj = external()
'''


def function_names(objects):
    return {obj.ir.get_qualname() for obj in objects if hasattr(obj, "ir")}


def callees(program, caller):
    return {callee for source, callee in program.edges if source == "subject." + caller}


import pytest

@pytest.fixture
def pointer_program(tmp_path):
    with analyze_program(tmp_path, POINTER_SOURCE, pointer_options={"index_sensitive": True}) as program:
        assert program.query.get_statistics()["converged"] == 1
        assert program.ai.get_summary("subject.monomorphic") is not None
        yield program


def assert_weak_write_preserved(program, name="x"):
    from pythonstan.analysis.pointer.kcfa.heap_model import attr
    objects = tuple(program.points(name))
    assert objects
    state = AIState()
    for obj in objects:
        state = state.write_field_value(obj, attr("field"), AbsVal.from_int(1))
        state = state.write_field_value(obj, attr("field"), AbsVal.from_int(2))
        value, _ = state.read_field_value(obj, attr("field"), None, None)
        assert value.get_int_lattice().get_consts() == frozenset({1, 2})
    # One abstract allocation does not prove one concrete runtime object.
    assert not hasattr(program.adapter, "is_singleton_object")


def assert_exception_alias_flow(tmp_path):
    """Actual exception/finally CFG retains alias writes and callable flow."""
    source = '''
def callback():
    return "ok"
class Box:
    pass
a = Box()
b = a
try:
    raise ValueError()
except ValueError:
    a.cleanup = callback
finally:
    b.done = callback
result = b.cleanup()
'''
    with analyze_program(tmp_path, source) as program:
        assert program.query.may_alias("a", "b", program.module)
        assert function_names(program.query.field_of_objects(program.points("b"), "cleanup")) == {"subject.callback"}
        assert function_names(program.query.field_of_objects(program.points("a"), "done")) == {"subject.callback"}
        assert ("subject", "subject.callback") in program.edges
        assert program.constants("result") == {"ok"}
        # Exception entry is not fully modeled by the independent AI engine;
        # retain its residual alongside the actual upstream value/effect witness.
        summary = program.ai.get_summary("subject")
        assert not summary.is_complete and "unknown_call" in summary.incomplete_reasons
