"""Independent fixpoint checks: late rules, alias joins and lexical captures."""
import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
from pythonstan.analysis.pointer.kcfa.object import FunctionObject
from pythonstan.analysis.pointer.kcfa.state import Worklist
from pythonstan.analysis.pointer.kcfa.variable import VariableKind
from pythonstan.world.pipeline import Pipeline


@pytest.fixture(params=["fifo", "lifo"])
def schedule(request, monkeypatch):
    if request.param == "lifo":
        def pop(self):
            scope, node, points = self.items_list.pop()
            del self.items_dict[node]
            return scope, node, points.val
        monkeypatch.setattr(Worklist, "pop", pop)
    return request.param


def analyze(tmp_path, source, policy):
    path = tmp_path / "incremental.py"
    path.write_text(source)
    pipeline = Pipeline(config={
        "filename": str(path), "project_path": str(tmp_path), "library_paths": [],
        "import_level": 0, "mock_libs": False, "time_count": False,
        "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                      "options": Config(context_policy=policy, max_iterations=20000,
                                        log_level="ERROR", enable_ai_intrinsics=False).to_dict()}],
    })
    pipeline.run()
    analysis = pipeline.analysis_manager.get_analyzer("pta")
    assert analysis.solver.query().get_statistics()["converged"] == 1
    return analysis


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
@pytest.mark.parametrize("bound", [False, True])
def test_existing_call_edge_keeps_late_arguments_and_new_return_slot(tmp_path, schedule, policy, bound, monkeypatch):
    declaration = ("class Box:\n    def identity(self, value):\n        return value\n"
                   "box = Box()\nfirst_result = box.identity(first)" if bound else
                   "def identity(value):\n    return value\nfirst_result = identity(first)")
    analysis = analyze(tmp_path, "def first():\n    return 1\ndef second():\n    return 2\n" + declaration, policy)
    solver, state = analysis.solver, analysis.state
    edge = next(edge for edge in state.call_graph.edges
                if edge.callee.stmt.get_qualname().endswith(".identity"))
    original = next(call for call in state.constraints.get_by_type(CallConstraint)
                    if call.call_site == edge.callsite.content)
    scope = edge.callsite.scope
    second = solver.variable_factory.make_variable("second", VariableKind.GLOBAL)
    target = solver.variable_factory.make_variable("$late_return", VariableKind.TEMPORARY)
    replay = CallConstraint(original.callee, (second,), frozenset(), target, original.call_site)
    translated = []
    previous = solver.ir_translator.translate_function
    def record(function):
        translated.append(function)
        return previous(function)
    monkeypatch.setattr(solver.ir_translator, "translate_function", record)
    solver.add_constraint(scope, scope.context, replay)
    solver.solve_to_fixpoint()
    assert solver.query().get_statistics()["converged"] == 1
    assert edge.callee.stmt not in translated
    assert sum(candidate == edge for candidate in state.call_graph.edges) == 1
    for destination in (target, original.target):
        names = {obj.ir.name for obj in state.get_points_to(state.get_variable(scope, scope.context, destination))
                 if isinstance(obj, FunctionObject)}
        assert {"first", "second"} <= names


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_dynamic_namespace_factory_keeps_merged_lexical_captures(tmp_path, schedule, policy):
    source = '''
def alpha():
    return 1
def beta():
    return 2
def make(callback):
    def invoke(self):
        return callback()
    namespace = {}
    namespace["invoke"] = invoke
    return type("Product", (), namespace)
Left = make(alpha)
Right = make(beta)
Left().invoke()
Right().invoke()
'''
    namespace = {}
    exec(source, namespace)
    analysis = analyze(tmp_path, source, policy)
    edges = {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
             for edge in analysis.solver.query().call_graph().edges}
    assert {("incremental.make.invoke", "incremental.alpha"),
            ("incremental.make.invoke", "incremental.beta")} <= edges


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_dynamic_base_and_namespace_dependencies_survive_worklist_order(tmp_path, schedule, policy):
    source = '''
def callback():
    return 42
class Parent:
    def inherited(self):
        return callback()
def construct(base):
    def dispatch(self):
        return self.inherited()
    namespace = {}
    namespace.update({"dispatch": dispatch})
    return type("Product", (base,), namespace)
Product = construct(Parent)
result = Product().dispatch()
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["result"] == 42
    analysis = analyze(tmp_path, source, policy)
    edges = {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
             for edge in analysis.solver.query().call_graph().edges}
    assert {("incremental.construct.dispatch", "incremental.Parent.inherited"),
            ("incremental.Parent.inherited", "incremental.callback")} <= edges


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_coarsened_base_alternatives_keep_fallback_mro_target(tmp_path, schedule, policy):
    source = '''
def alpha():
    return 1
def beta():
    return 2
class Has:
    selected = staticmethod(alpha)
class Empty:
    pass
class Fallback:
    selected = staticmethod(beta)
def factory(base):
    class Product(base, Fallback):
        pass
    return Product
Left = factory(Has)
Right = factory(Empty)
first_result = Left.selected()
second_result = Right.selected()
'''
    namespace = {}
    exec(source, namespace)
    assert (namespace["first_result"], namespace["second_result"]) == (1, 2)
    analysis = analyze(tmp_path, source, policy)
    edges = {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
             for edge in analysis.solver.query().call_graph().edges}
    assert {("incremental", "incremental.alpha"),
            ("incremental", "incremental.beta")} <= edges


@pytest.mark.parametrize("policy", ["0-cfa", "2-cfa"])
def test_global_base_may_alternatives_do_not_prune_required_fallback(tmp_path, schedule, policy):
    # Both subclasses see a may-set for global ``base`` in flow-insensitive
    # analysis. Existence of Has.selected does not prove that Empty has it.
    # Before the repair FIFO retained both calls, while LIFO dropped beta.
    source = '''
def alpha():
    return 1
def beta():
    return 2
class Has:
    selected = staticmethod(alpha)
class Empty:
    pass
class Fallback:
    selected = staticmethod(beta)
base = Has
class Left(base, Fallback):
    pass
base = Empty
class Right(base, Fallback):
    pass
first_result = Left.selected()
second_result = Right.selected()
'''
    namespace = {}
    exec(source, namespace)
    assert (namespace["first_result"], namespace["second_result"]) == (1, 2)
    analysis = analyze(tmp_path, source, policy)
    edges = {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
             for edge in analysis.solver.query().call_graph().edges}
    assert {("incremental", "incremental.alpha"),
            ("incremental", "incremental.beta")} <= edges
