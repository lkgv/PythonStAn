"""Closed-module facade uses real lowering and never trusts incomplete imports."""
import ast
import pytest
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.module_analysis import ModuleAnalyzer
from pythonstan.analysis.pointer.kcfa.module_summary import ModuleSummary, FunctionSummary, ClassSummary
from pythonstan.analysis.pointer.kcfa.object import ConstantObject
from pythonstan.ir.ir_statements import IRModule
from pythonstan.world import World
from tests.pointer.kcfa.builders import make_site


def lower(source, name="closed"):
    from pythonstan.analysis.transform.three_address import ThreeAddressTransformer
    from pythonstan.analysis.transform.ir import IRTransformer
    world = World()
    world.setup()
    world.scope_manager.build()
    tree = ast.parse(source)
    module = IRModule(name, tree, name=name, filename=f"{name}.py")
    world.set_entry_module(module)
    tac = ThreeAddressTransformer().visit(tree)
    world.scope_manager.set_ir(module, "three address form", tac)
    transformer = IRTransformer(module)
    transformer.process_stmts(tac.body)
    world.scope_manager.set_ir(module, "ir", transformer.get_stmts())
    return module


def constants(summary, name):
    return {obj.value for obj in summary.exports[name] if isinstance(obj, ConstantObject)}


def test_closed_module_uses_real_calls_defaults_and_fixedpoint():
    module = lower("def f(x=7):\n return x\nresult=f()\n_private=11\n")
    analyzer = ModuleAnalyzer(Config(log_level="ERROR"))
    summary = analyzer.analyze_module(module, {})
    assert summary.module_name == "closed"
    assert set(summary.exports) == {"f", "result"}
    assert constants(summary, "result") == {7}
    assert not summary.is_complete
    query = analyzer.last_analysis.query()
    assert query.get_statistics()["converged"] == 1
    assert any(edge.callee.stmt.name == "f" for edge in query.call_graph().edges)


def test_real_pipeline_lowering_can_feed_closed_module_facade(tmp_path):
    from pythonstan.world.pipeline import Pipeline
    file = tmp_path / "closed.py"
    file.write_text("def f(x):\n return x\nresult=f(19)\n")
    World.setup()
    pipeline = Pipeline(config={
        "filename": str(file), "project_path": str(tmp_path), "library_paths": [],
        "import_level": 0, "mock_libs": False, "time_count": False,
        "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                      "options": Config(log_level="ERROR").to_dict()}],
    })
    pipeline.run()
    summary = ModuleAnalyzer(Config(log_level="ERROR")).analyze_module(World().get_entry_module(), {})
    assert constants(summary, "result") == {19}


def test_empty_lowered_body_is_valid():
    summary = ModuleAnalyzer(Config()).analyze_module(lower(""), {})
    assert summary.exports == {}
    assert not summary.is_complete


def test_missing_lowering_is_not_treated_as_an_empty_program():
    module = lower("")
    World().scope_manager.scope_ir.pop((module.get_qualname(), "ir"))
    with pytest.raises(ValueError, match="has not been lowered"):
        ModuleAnalyzer(Config()).analyze_module(module, {})


def test_literal_all_preserves_private_export_and_excludes_other_binding():
    module = lower('__all__=["_public"]\n_public=23\nother=29\n')
    summary = ModuleAnalyzer(Config()).analyze_module(module, {})
    assert set(summary.exports) == {"_public"}
    assert constants(summary, "_public") == {23}


def test_mutated_all_is_explicitly_incomplete_and_retains_private_witnesses():
    module = lower('__all__=["public"]\n__all__.append("_private")\npublic=1\n_private=2\n')
    summary = ModuleAnalyzer(Config()).analyze_module(module, {})
    assert "dynamic___all___not_resolved" in summary.incomplete_reasons
    assert {"public", "_private"} <= summary.exports.keys()


def test_incomplete_summary_is_rejected_before_analysis_reset():
    module = lower("answer=31\n")
    analyzer = ModuleAnalyzer(Config())
    prior = analyzer.analyze_module(module, {})
    with pytest.raises(ValueError, match="module_heap_and_callback_effects_not_serialized"):
        analyzer.analyze_module(module, {"dependency": prior})
    assert constants(prior, "answer") == {31}


@pytest.mark.parametrize("source", ["import dependency\n", "def f():\n import dependency\n"])
def test_importing_source_requires_whole_program_analysis(source):
    with pytest.raises(ValueError, match="does not resolve imports"):
        ModuleAnalyzer(Config()).analyze_module(lower(source), {})


def test_wrong_entry_identity_rejected():
    module = lower("answer=1")
    other = IRModule(module.get_qualname(), module.get_ast(), name="closed")
    with pytest.raises(ValueError, match="entry-module identity"):
        ModuleAnalyzer(Config()).analyze_module(other, {})


def test_solver_budget_is_not_presented_as_complete_module_summary():
    analyzer = ModuleAnalyzer(Config(max_iterations=1))
    summary = analyzer.analyze_module(lower("x=1\ny=x\n"), {})
    assert "module_solver_did_not_converge" in summary.incomplete_reasons
    assert not summary.is_complete


def test_function_merge_rejects_incompatible_parameter_lists():
    with pytest.raises(ValueError, match="parameter lists"):
        FunctionSummary("f", ("x",)).merge(FunctionSummary("f", ("y",)))


def test_class_merge_rejects_distinct_allocation_identity():
    from pythonstan.analysis.pointer.kcfa.object import AllocKind
    first, second = make_site(kind=AllocKind.CLASS, name="C"), make_site(kind=AllocKind.CLASS, name="C")
    with pytest.raises(ValueError, match="allocation identities"):
        ClassSummary("C", first).merge(ClassSummary("C", second))


def test_class_merge_rejects_incompatible_ordered_bases():
    from pythonstan.analysis.pointer.kcfa.object import AllocKind
    site = make_site(kind=AllocKind.CLASS, name="C")
    with pytest.raises(ValueError, match="ordered bases"):
        ClassSummary("C", site, bases=("A", "B")).merge(ClassSummary("C", site, bases=("B", "A")))


def test_compatible_summary_merge_preserves_structure():
    from pythonstan.analysis.pointer.kcfa.object import AllocKind
    site = make_site(kind=AllocKind.CLASS, name="C")
    method = FunctionSummary("f", ("x",))
    merged = ClassSummary("C", site, bases=("A",), methods={"f": method}).merge(
        ClassSummary("C", site, bases=("A",), methods={"f": method}))
    assert merged.alloc_site is site
    assert merged.bases == ("A",)
    assert merged.methods["f"].params == ("x",)
