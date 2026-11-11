"""Regression checks for module ownership at the analysis entry point."""

import ast

import pytest

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.object import ConstantObject
from pythonstan.analysis.pointer.kcfa.variable import VariableKind
from pythonstan.ir.ir_statements import IRAssign, IRImport, IRModule
from pythonstan.analysis.transform.ir import IRTransformer
from pythonstan.analysis.transform.three_address import ThreeAddressTransformer
from pythonstan.world import World
from pythonstan.world.namespace import NamespaceManager
from pythonstan.world.scope_manager import ModuleGraph, ScopeManager


@pytest.fixture
def world(monkeypatch):
    world = World()
    manager = ScopeManager()
    manager.build()
    monkeypatch.setattr(world, "scope_manager", manager, raising=False)
    monkeypatch.setattr(world, "namespace_manager", NamespaceManager(), raising=False)
    monkeypatch.setattr(world, "entry_module", None, raising=False)
    return world


def _module(world, name, value):
    tree = ast.parse(f"x = {value}")
    module = IRModule(name, tree, name, f"{name}.py")
    world.scope_manager.set_ir(module, "ir", [IRAssign(tree.body[0])])
    return module


def _analysis():
    return PointerAnalysis(AnalysisConfig(
        name="module_scopes", id="PointerAnalysis", description="module regression",
        prev_analysis=["ir"], options=Config(index_sensitive=True).to_dict(),
    ))


def _global_values(analysis, module, name):
    scope = next(scope for scope in analysis.state._internal_scope.values() if scope.stmt is module)
    variable = analysis.solver.variable_factory.make_variable(name, VariableKind.GLOBAL)
    contextual = analysis.state.get_variable(scope, scope.context, variable)
    return {obj.value for obj in analysis.state.get_points_to(contextual) if isinstance(obj, ConstantObject)}


def test_single_module_analysis_without_import_graph(world):
    module = _module(world, "single", 1)
    world.set_entry_module(module)
    analysis = _analysis()

    analysis.analyze(module, {})

    assert _global_values(analysis, module, "x") == {1}
    assert analysis.solver.query().get_statistics()["converged"] == 1


def test_constraints_keep_their_defining_module(world):
    entry = _module(world, "entry", 1)
    imported = _module(world, "imported", 2)
    graph = ModuleGraph()
    graph.add_node(entry)
    graph.add_node(imported)
    world.scope_manager.set_module_graph(graph)
    world.set_entry_module(entry)
    analysis = _analysis()

    analysis.analyze(entry, {})

    assert _global_values(analysis, entry, "x") == {1}
    assert _global_values(analysis, imported, "x") == {2}


def test_module_translation_failure_is_explicit(world, monkeypatch):
    module = _module(world, "broken", 1)
    world.set_entry_module(module)
    analysis = _analysis()

    def fail_translation(_module):
        raise ValueError("unsupported statement")

    monkeypatch.setattr(analysis.translator, "translate_module", fail_translation)
    analysis.analyze(module, {})

    unknowns = analysis.solver.query().get_unknown_details()
    assert any(record["kind"] == "translation_error" and record["location"] == "broken" for record in unknowns)


def test_translation_failure_remains_open_when_diagnostics_are_disabled(world, monkeypatch):
    module = _module(world, "broken_quiet", 1)
    world.set_entry_module(module)
    config = Config(track_unknowns=False)
    analysis = PointerAnalysis(AnalysisConfig(
        name="quiet_failure", id="PointerAnalysis", description="semantic status regression",
        prev_analysis=["ir"], options=config.to_dict(),
    ))

    def fail_translation(_module):
        raise ValueError("unsupported operation")

    monkeypatch.setattr(analysis.translator, "translate_module", fail_translation)
    result = analysis.analyze(module, {})
    query = result.query()
    assert query.get_unknown_details() == []
    assert any("untranslated_source" in effect["effects"] for effect in query.get_residual_effects())
    status = query.get_semantic_status()
    assert status["constraint_fixpoint_reached"] is True
    assert status["residual_obligation_count"] > 0
    assert status["unknown_diagnostics_enabled"] is False
    assert status["certificate_eligible"] is False


def test_quiet_fixedpoint_is_not_a_source_coverage_certificate(world):
    module = _module(world, "finite_assign", 1)
    world.set_entry_module(module)
    result = _analysis().analyze(module, {})
    status = result.query().get_semantic_status()
    assert status["constraint_fixpoint_reached"] is True
    assert status["source_semantics_coverage"] == "unproved"
    assert status["residual_enumeration_exhaustive"] is False
    assert status["whole_program_soundness_proved"] is False


def test_imported_function_reads_its_defining_module_globals(world):
    def translate(name, source):
        tree = ast.parse(source)
        module = IRModule(name, tree, name, f"{name}.py")
        lowered = ThreeAddressTransformer().visit(tree)
        transform = IRTransformer(module)
        transform.process_stmts(lowered.body)
        world.scope_manager.set_ir(module, "ir", transform.get_stmts())
        return module

    entry = translate("entry", "x = 1\nimport library\nresult = library.read()")
    library = translate("library", "x = 2\ndef read():\n    return x")
    imported = next(stmt for stmt in world.scope_manager.get_ir(entry, "ir") if isinstance(stmt, IRImport))
    graph = ModuleGraph()
    graph.add_edge(entry, imported, library)
    world.scope_manager.set_module_graph(graph)
    world.set_entry_module(entry)
    analysis = _analysis()

    analysis.analyze(entry, {})

    assert _global_values(analysis, entry, "result") == {2}
