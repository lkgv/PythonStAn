"""Configuration contracts and behavioral integration, not a soundness proof."""
from dataclasses import asdict, fields
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.pointer.ai.api import AnalysisBudget, AISummary, AISummaryEngine
from pythonstan.analysis.pointer.ai.config import Config as AIConfig
from pythonstan.analysis.pointer.kcfa.config import Config as PTAConfig
from pythonstan.analysis.pointer.ai.analysis import AIAnalysisDriver
from pythonstan.analysis.pointer.kcfa.context_selector import ContextSelector, parse_policy


def test_every_ai_budget_is_exposed_with_same_default_and_roundtrips():
    config = AIConfig()
    assert asdict(config.to_budget()) == asdict(AnalysisBudget())
    assert AIConfig.from_dict(config.to_dict()) == config
    assert AIConfig.from_dict({}) == config
    for option in fields(AnalysisBudget):
        original = getattr(config, option.name)
        changed = not original if type(original) is bool else original + 1
        selected = AIConfig.from_dict({option.name: changed})
        assert getattr(selected.to_budget(), option.name) == changed


@pytest.mark.parametrize("cls", [AIConfig, PTAConfig])
def test_config_defaults_are_construction_independent(cls):
    assert cls.from_dict({}) == cls()
    assert cls.from_dict(cls().to_dict()) == cls()


@pytest.mark.parametrize("options", [{"enable_narrowing": "false"}, {"max_iterations": True}, {"max_const_strings": 0}, {"max_recursion_depth": -1}, {"unknown_option": 1}])
def test_ai_bad_config_rejected(options):
    with pytest.raises(ValueError):
        AIConfig.from_dict(options)
    with pytest.raises(ValueError):
        PTAConfig(ai_budget=options)


@pytest.mark.parametrize("options", [{"k": 2}, {"index_sensitive": "false"}, {"max_points_to_size": 5}, {"max_import_depth": 0}, {"build_class_hierarchy": False}, {"enable_instrumentation": True}])
def test_kcfa_no_op_or_mistyped_options_refused(options):
    with pytest.raises(ValueError):
        PTAConfig.from_dict(options)


def test_nested_solver_options_have_one_authority_and_copied_collections():
    options = {"merge_points": [1, 3], "loop_iters": {"3": 7}, "max_total_traces": 8,
               "enable_trace_partitioning": True, "loop_unroll": 2}
    config = AIConfig(max_stmt_visits=9, solver_options=options)
    options["merge_points"].append(8)
    solver = config.to_solver_config()
    assert solver.max_block_visits == 9
    assert solver.merge_points == {1, 3} and solver.loop_iters == {3: 7}
    solver.merge_points.add(100)
    assert config.to_solver_config().merge_points == {1, 3}
    assert AIConfig.from_dict(json.loads(json.dumps(config.to_dict()))).to_solver_config() == config.to_solver_config()
    for bad in ({"max_iterations": 9}, {"max_total_traces": 0}, {"merge_points": [True]}, {"loop_iters": {"x": 2}}):
        with pytest.raises(ValueError):
            AIConfig(solver_options=bad)


def test_ai_preserves_each_upstream_context_before_join(monkeypatch):
    from pythonstan.analysis.pointer.ai import analysis as module
    scope = SimpleNamespace(get_qualname=lambda: "app.route")
    contexts = [SimpleNamespace(context="get"), SimpleNamespace(context="post")]
    plan = SimpleNamespace(scopes=[scope], missing=[])
    driver = AIAnalysisDriver(AnalysisConfig("ai", "AIAnalysisDriver", options={"type": "ai analysis"}))
    monkeypatch.setattr(driver, "_get_pta_results", lambda _: SimpleNamespace(_state=object()))
    monkeypatch.setattr(driver, "_get_kcfa_helpers", lambda: (None, None))
    monkeypatch.setattr(driver, "_get_entry_context", lambda _: "entry")
    monkeypatch.setattr(driver, "_get_scope_contexts", lambda *_: contexts)
    monkeypatch.setattr(driver, "_build_arg_bindings", lambda _, __, selected: selected)
    monkeypatch.setattr(driver, "_build_var_bindings_from_kcfa", lambda *_: [])
    monkeypatch.setattr(module, "PtaQueryAdapter", lambda *_: None)
    monkeypatch.setattr(module, "build_plan", lambda _: plan)
    observed = []
    class Engine:
        def __init__(self, *args, **kwargs):
            assert kwargs["solver_config"].enable_value_partition
        def analyze_callee(self, **kwargs):
            observed.append(kwargs)
            return AISummary(ret=frozenset({kwargs["call_context"]}))
    monkeypatch.setattr(module, "AISummaryEngine", Engine)
    result = driver.analyze(scope, {})
    assert [call["arg_bindings"] for call in observed] == [[contexts[0]], [contexts[1]]]
    assert [call["caller_scope"] for call in observed] == contexts
    assert result.get_summary("app.route").ret == frozenset({"get", "post"})
    assert len(result.context_summaries["app.route"]) == 2


def test_ai_cannot_repartition_a_completed_kcfa_result():
    driver = AIAnalysisDriver(AnalysisConfig("ai", "AIAnalysisDriver", options={"type": "ai analysis", "context_policy": "1-cfa"}))
    with pytest.raises(ValueError, match="match its KCFA"):
        driver._validate_context_policy(ContextSelector(parse_policy("2-cfa")))
    driver._validate_context_policy(ContextSelector(parse_policy("1-cfa")))


def test_cli_runs_ai_and_reports_effective_domain_options(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("def subject():\n    return None\n")
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"max_dict_parts": 3, "solver_options": {"max_total_traces": 5}, "entry_scope": "app.subject"}))
    root = Path(__file__).resolve().parents[2]
    run = subprocess.run([sys.executable, "scripts/do_ai.py", "--file", str(source), "--ai-options", str(options), "--no-ai-intrinsics"], cwd=root, capture_output=True, text=True, timeout=30)
    payload = json.loads(run.stdout)
    assert run.returncode in {0, 2}
    assert "app.subject" in payload["summaries"]
    assert payload["effective_config"]["max_dict_parts"] == 3
    assert payload["effective_config"]["solver_options"]["max_total_traces"] == 5
    assert payload["certified"] is False


def test_widen_delay_public_alias_controls_loop_threshold():
    config = AIConfig(solver_options={"widen_delay": 0})
    assert config.to_solver_config().loop_unroll == 0
    with pytest.raises(ValueError, match="aliases"):
        AIConfig(solver_options={"widen_delay": 1, "loop_unroll": 4})


def test_address_budget_changes_abstraction_without_losing_concrete_objects():
    from pythonstan.analysis.pointer.ai.state import AbsVal
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext, SummaryContext
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocKind, AllocSite
    # Distinct contexts of one site collapse to a covering summary, not an
    # arbitrary surviving element. Check the semantic order, not set inclusion.
    site = AllocSite("allocation", AllocKind.OBJECT)
    first = AbstractObject(CallStringContext((), 1), site)
    second = AbstractObject(CallStringContext((), 2), site)
    left, right = AbsVal.from_addrs(frozenset({first})), AbsVal.from_addrs(frozenset({second}))
    bounded = left.widen(right, max_addrs=AIConfig(max_addr_set_size=1).to_budget().max_addr_set_size)
    precise = left.widen(right, max_addrs=AIConfig(max_addr_set_size=10).to_budget().max_addr_set_size)
    assert left.leq(bounded) and right.leq(bounded)
    assert precise.leq(bounded)
    assert any(isinstance(obj.context, SummaryContext) for obj in bounded.addrs)
    assert not any(isinstance(obj.context, SummaryContext) for obj in precise.addrs)


@pytest.mark.parametrize("name", ["ai_bounded", "ai_balanced", "ai_partitioned"])
def test_named_profiles_are_valid_and_run_actual_ai(name, tmp_path):
    root = Path(__file__).resolve().parents[2]
    profile = root / "configs" / "analysis_profiles" / (name + ".json")
    config = AIConfig.from_dict(json.loads(profile.read_text()))
    assert config.to_budget().max_iterations > 0
    source = tmp_path / "app.py"
    source.write_text("def main():\n    return None\nmain()\n")
    run = subprocess.run([sys.executable, "scripts/do_ai.py", "--file", str(source), "--ai-options", str(profile), "--function", "app.main", "--no-ai-intrinsics"], cwd=root, capture_output=True, text=True, timeout=30)
    payload = json.loads(run.stdout)
    assert run.returncode == 0, payload
    assert payload["summaries"]["app.main"]["analyzed_contexts"] >= 1


@pytest.mark.parametrize("policy,minimum_contexts", [("0-cfa", 1), ("1-cfa", 2), ("2-cfa", 2)])
def test_real_cli_context_policies_retain_both_call_results(policy, minimum_contexts, tmp_path):
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "app.py"
    source.write_text("class A:\n    pass\nclass B:\n    pass\ndef identity(x):\n    return x\ndef main():\n    first = identity(A())\n    second = identity(B())\n    return second\nmain()\n")
    run = subprocess.run([sys.executable, "scripts/do_ai.py", "--file", str(source), "--function", "app.identity", "--context-policy", policy, "--no-ai-intrinsics"], cwd=root, capture_output=True, text=True, timeout=30)
    payload = json.loads(run.stdout)
    assert run.returncode == 0, payload
    result = payload["summaries"]["app.identity"]
    assert result["analyzed_contexts"] >= minimum_contexts
    assert any("A()" in value for value in result["return_objects"])
    assert any("B()" in value for value in result["return_objects"])


def test_mixed_unknown_and_literal_name_is_not_seeded_as_exhaustive_strings():
    import ast
    from pythonstan.ir import IRFunc
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext
    from pythonstan.analysis.pointer.kcfa.object import AbstractObject, ConstantObject, AllocKind, AllocSite
    context = CallStringContext((), 1)
    known = ConstantObject(context, AllocSite("literal", AllocKind.CONSTANT), "route")
    unknown = AbstractObject(context, AllocSite("external", AllocKind.UNKNOWN))
    scope = IRFunc("app.subject", ast.parse("def subject(name):\n    pass\n").body[0])
    caller = SimpleNamespace(context=context)
    state = SimpleNamespace(_get_variable_direct=lambda *_: object(), get_points_to=lambda _: {known, unknown})
    driver = AIAnalysisDriver(AnalysisConfig("ai", "AIAnalysisDriver", options={"type": "ai analysis"}))
    value = driver._build_arg_bindings(state, scope, [caller])[0].value
    assert value.get_const_strs() is None
    assert value.addrs == frozenset({known, unknown})


@pytest.mark.parametrize("case_ids", [[], ["not-a-case"]])
def test_ablation_refuses_empty_or_unknown_selection(case_ids):
    from benchmark.empirical.config_ablations import run
    with pytest.raises(ValueError, match="known cases"):
        run(repeats=1, case_ids=case_ids)
