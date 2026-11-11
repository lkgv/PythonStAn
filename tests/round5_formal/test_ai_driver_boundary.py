"""A completed child summary cannot certify an incomplete upstream solve."""
from types import SimpleNamespace

import pytest

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.pointer.ai import analysis as module
from pythonstan.analysis.pointer.ai.api import AISummary
from pythonstan.ir import IRModule
from tests.pointer.kcfa.test_ai_bridge import analyze_source


@pytest.mark.parametrize("statistics,complete", [
    ({"converged": 0}, False), ({"converged": False}, False), ({}, False),
    ({"converged": None}, False), ({"converged": "1"}, False),
    ({"converged": 1.0}, False), ({"converged": 2}, False),
    ({"converged": 1}, True), ({"converged": True}, True),
])
def test_ai_preserves_upstream_completeness_evidence(monkeypatch, statistics, complete):
    scope = SimpleNamespace(get_qualname=lambda: "app.callback")
    context = SimpleNamespace(context="actual")
    driver = module.AIAnalysisDriver(AnalysisConfig("ai", "AIAnalysisDriver", options={"type": "ai analysis"}))
    monkeypatch.setattr(driver, "_get_pta_results", lambda _: SimpleNamespace(
        _state=object(), get_statistics=lambda: statistics))
    monkeypatch.setattr(driver, "_get_kcfa_helpers", lambda: (None, None))
    monkeypatch.setattr(driver, "_get_entry_context", lambda _: "entry")
    monkeypatch.setattr(driver, "_get_scope_contexts", lambda *_: [context])
    monkeypatch.setattr(driver, "_build_arg_bindings", lambda *_: [])
    monkeypatch.setattr(driver, "_build_var_bindings_from_kcfa", lambda *_: [])
    monkeypatch.setattr(module, "PtaQueryAdapter", lambda *_: None)
    monkeypatch.setattr(module, "build_plan", lambda _: SimpleNamespace(scopes=[scope], missing=[]))

    class Engine:
        def __init__(self, *args, **kwargs):
            pass

        def analyze_callee(self, **kwargs):
            return AISummary(is_complete=True)

    monkeypatch.setattr(module, "AISummaryEngine", Engine)
    result = driver.analyze(scope, {})
    summary = result.get_summary("app.callback")
    assert summary.is_complete is complete
    assert ("upstream_pta_incomplete" in summary.incomplete_reasons) is not complete
    assert result.context_summaries["app.callback"][0][1].is_complete is complete


def test_module_seeding_uses_actual_flow_nodes_without_other_scope_bindings(tmp_path):
    query, _ = analyze_source(tmp_path, '''
route = "module"
class Box:
    route = "class"
def shadow():
    route = "local"
    return route
result = shadow()
box = Box()
box.route = "instance"
''', enable_ai_intrinsics=False)
    scope = next(scope for scope in query.get_reachable_scopes() if isinstance(scope, IRModule))
    contexts = query._state.get_scopes_for_ir(scope)
    driver = module.AIAnalysisDriver(AnalysisConfig("ai", "AIAnalysisDriver", options={"type": "ai analysis"}))
    bindings = driver._build_var_bindings_from_kcfa(query._state, scope, contexts)
    by_name = {binding.param_name: binding for binding in bindings}
    assert by_name["route"].value.get_const_strs() == frozenset({"module"})
    assert by_name["result"].value.get_const_strs() == frozenset({"local"})
    assert not any(name.startswith("$") for name in by_name)
    assert driver._build_var_bindings_from_kcfa(query._state, scope, []) == []
