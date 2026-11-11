from graphlib import CycleError
import pytest
from pythonstan.analysis.pointer.kcfa.dependency_graph import ModuleDependencyGraph
from pythonstan.analysis.pointer.kcfa.module_summary import ModuleSummary
from tests.analysis.legacy_ai_support import analyze_program
from tests.pointer.kcfa.builders import make_scope
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode


def test_dependency_graph_scc_order_and_self_cycle():
    graph=ModuleDependencyGraph()
    graph.add_import('entry','a'); graph.add_import('a','b'); graph.add_import('b','a')
    graph.add_import('b','leaf'); graph.add_import('self','self')
    components=graph.strongly_connected_components()
    assert components.index(('leaf',)) < components.index(('a','b')) < components.index(('entry',))
    assert set(graph.detect_cycles()) == {('a','b'),('self',)}
    with pytest.raises(CycleError): graph.topological_sort()
    view=graph.get_imports('entry')
    assert view == frozenset({'a'})


def test_dependency_graph_deep_chain_is_not_recursion_limited():
    graph=ModuleDependencyGraph()
    for n in range(2000): graph.add_import(str(n),str(n+1))
    assert graph.topological_sort() == [str(n) for n in reversed(range(2001))]


def test_relative_packages_and_invalid_levels():
    graph=ModuleDependencyGraph()
    assert graph.resolve_relative_import('pkg.sub','x',1,is_package=True) == 'pkg.sub.x'
    assert graph.resolve_relative_import('pkg.sub.module','x',0) == 'x'
    with pytest.raises(ImportError): graph.resolve_relative_import('standalone','x',1)
    with pytest.raises(ValueError): graph.resolve_relative_import('pkg.mod','x',-1)


def test_projection_from_real_solver_scopes_and_binding_application(tmp_path):
    with analyze_program(tmp_path,'x=[]\ndef f():\n x={}\n return x\nf()') as run:
        scope=run.query._state.get_scope_for_ir(run.module)
        projection=ModuleSummary.from_state(run.query._state,scope,{'x'})
        assert projection.exports['x'] == run.points('x')
        assert not projection.is_complete
        assert projection.incomplete_reasons
        other=make_scope('imported','module')
        destination=PointerAnalysisState()
        projection.apply_bindings(destination,other)
        binding=destination.get_variable(other,other.context,Variable('x',VariableKind.GLOBAL))
        assert destination.get_points_to(NormalNode(binding)) == run.points('x')
        assert projection.apply_bindings(destination,other) == ()


def test_conflicting_context_summary_maps_are_rejected():
    left=ModuleSummary('m',context_map={'external':'first'})
    right=ModuleSummary('m',context_map={'external':'second'})
    with pytest.raises(ValueError,match='Conflicting context'): left.merge(right)
