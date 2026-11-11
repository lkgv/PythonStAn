"""Private closure lifetime is independent of public per-scope dataflow results."""
import ast
import gc
import weakref

import pytest

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.closure import ClosureAnalysis
from pythonstan.analysis.dataflow.driver import DataflowAnalysisDriver
from pythonstan.ir import IRModule
from pythonstan.world import World
from pythonstan.world.analysis_manager import AnalysisManager
from pythonstan.world.config import Config


SOURCE = """
def first(arg):
    local = arg
    return local
def second(arg):
    return arg
def outer(arg):
    value = arg
    def inner():
        return value
    def sibling():
        return value
    return inner, sibling
class Holder:
    def method(self, arg):
        def nested():
            return arg
        return nested
"""


def lower(tmp_path):
    path = tmp_path / "subject.py"; path.write_text(SOURCE)
    World.setup(); world = World()
    world.build(Config(str(path), str(tmp_path), mock_libs=False))
    manager = AnalysisManager(); manager.build([]); world.analysis_manager = manager
    module = IRModule("subject", ast.parse(SOURCE), "subject", str(path))
    world.scope_manager.scopes.add(module); world.scope_manager.names2scope["subject"] = module
    for phase in ("three address", "ir", "block cfg", "cfg"): manager.analysis(phase, module)
    return world, module


def liveness(options=None):
    return DataflowAnalysisDriver(AnalysisConfig(name="live", id="LivenessAnalysis",
        options={"type": "dataflow analysis", **(options or {})}))


@pytest.mark.parametrize("retain", [False, True])
def test_public_default_or_explicit_streaming_keeps_latest_aliases(tmp_path, retain):
    world, module = lower(tmp_path)
    first = world.scope_manager.names2scope["subject.first"]
    second = world.scope_manager.names2scope["subject.second"]
    # Leaving the option absent must retain the established public API.
    driver = liveness(None if retain else {"retain_scope_results": False})
    driver.analyze(first, {})
    old_fact = weakref.ref(next(iter(driver.results["out"].values())))
    driver.analyze(second, {})
    gc.collect()
    expected = {first, second} if retain else set()
    assert set(driver.results["per_scope"]) == expected
    assert (old_fact() is not None) is retain
    cfg = world.scope_manager.get_ir(second, "cfg")
    assert set(driver.results["in"]) == set(driver.results["out"]) == cfg.blks
    if retain:
        assert driver.results["in"] is driver.results["per_scope"][second]["in"]


@pytest.mark.parametrize("value", [0, 1, "false", None, []])
def test_retention_option_rejects_non_boolean(value):
    with pytest.raises(ValueError, match="retain_scope_results"):
        liveness({"retain_scope_results": value})


def test_closure_streaming_and_retaining_have_equal_raw_facts_and_cells(tmp_path):
    world, module = lower(tmp_path)
    initial = {scope: set(scope.cell_vars) for scope in world.scope_manager.get_scopes()}
    observed = []
    for retain in (True, False):
        for scope, value in initial.items(): scope.cell_vars = set(value)
        analysis = ClosureAnalysis(AnalysisConfig(name="closure", id="Closure", options={"type":"closure analysis"}))
        assert analysis.liveness_analysis.retain_scope_results is False
        analysis.liveness_analysis = liveness({"retain_scope_results": retain})
        analyze = analysis.liveness_analysis.analyze
        raw_facts = {}
        refs = []
        def capture(scope, previous):
            analyze(scope, previous)
            result = analysis.liveness_analysis.results
            raw_facts[scope.get_qualname()] = {
                direction: sorted((node.idx, sorted(fact)) for node, fact in result[direction].items())
                for direction in ("in", "out")}
            refs.extend(weakref.ref(fact) for direction in ("in", "out") for fact in result[direction].values())
        analysis.liveness_analysis.analyze = capture
        analysis.analyze(module, {})
        cells = {scope.get_qualname(): sorted(scope.cell_vars) for scope in initial}
        assert cells["subject.outer.inner"] == ["value"]
        if not retain:
            gc.collect()
            assert analysis.liveness_analysis.results == {"per_scope": {}}
            # Canonical cells own copies; no flow-map fact is kept by closure.
            assert all(reference() is None for reference in refs)
        observed.append((raw_facts, cells))
    assert observed[0] == observed[1]


def test_cell_filter_does_not_mutate_retained_raw_entry_fact(tmp_path):
    world, module = lower(tmp_path)
    analysis = ClosureAnalysis(AnalysisConfig(name="closure", id="Closure", options={"type":"closure analysis"}))
    analysis.liveness_analysis = liveness()  # Explicit diagnostics retain raw facts.
    analysis.analyze(module, {})
    scope = world.scope_manager.names2scope["subject.second"]
    entry = world.scope_manager.get_ir(scope, "cfg").entry_blk
    fact = analysis.liveness_analysis.results["per_scope"][scope]["out"][entry]
    assert "arg" in fact and "arg" not in scope.cell_vars
    assert fact is not scope.cell_vars
