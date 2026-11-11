"""Independent result-lifetime and semantic tests for private closure liveness.

The optimization may discard completed dense maps; it must preserve public
scope results by default and materialize every required IR cell/free-variable set.
"""
import gc
import weakref

import pytest

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.analysis import get_scope_result
from pythonstan.analysis.dataflow import DataflowAnalysisDriver
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline
from tests.analysis.legacy_ai_support import isolated_world


SOURCE = '''
ambient = 7
def outer(seed):
    captured = seed
    class Box:
        def getter(self):
            return captured + ambient
        @classmethod
        def class_getter(cls):
            return captured
    def left():
        return captured
    def right():
        def nested():
            return captured + ambient
        return nested
    return Box, left, right
def sibling(value):
    def leaf():
        return value
    return leaf
'''


def build(tmp_path, source=SOURCE):
    path = tmp_path / "entry.py"
    path.write_text(source)
    return Pipeline(config={"filename": str(path), "project_path": str(tmp_path),
                            "library_paths": [], "import_level": 0,
                            "mock_libs": False, "analysis": []})


def cells():
    return {scope.get_qualname(): frozenset(scope.get_cell_vars())
            for scope in World().scope_manager.get_scopes()}


@pytest.mark.parametrize("retained", [True, False])
def test_generic_driver_publishes_only_successful_runs_and_can_retry(tmp_path, retained):
    with isolated_world():
        build(tmp_path)
        scopes = sorted(World().scope_manager.get_scopes(), key=lambda s: s.get_qualname())
        first, second = scopes[0], scopes[-1]
        driver = DataflowAnalysisDriver(AnalysisConfig(
            "independent-live", "LivenessAnalysis",
            options={"type": "dataflow analysis", "retain_scope_results": retained}))
        driver.analyze(first, {})
        first_in, first_out = driver.results["in"], driver.results["out"]
        assert bool(first_in) and bool(first_out)
        assert bool(driver.results["per_scope"]) is retained
        if retained:
            assert get_scope_result(driver.results, first)["out"] is first_out
        else:
            with pytest.raises(ValueError, match="has no result"):
                get_scope_result(driver.results, first)
        solver = driver.solver
        class FailBeforePublication:
            @staticmethod
            def solve(analysis):
                raise MemoryError("synthetic analysis failure")
        driver.solver = FailBeforePublication
        with pytest.raises(MemoryError, match="synthetic analysis failure"):
            driver.analyze(second, {})
        # Clearing history before the new solve succeeds would destroy the last
        # legitimate result and break callers that recover from a failed pass.
        assert driver.results["in"] is first_in
        assert driver.results["out"] is first_out
        assert bool(driver.results["per_scope"]) is retained
        driver.solver = solver
        driver.analyze(second, {})
        assert driver.results["in"] is not first_in
        assert driver.results["out"] is not first_out
        if retained:
            assert set(driver.results["per_scope"]) == {first, second}
            assert get_scope_result(driver.results, first)["out"] is first_out
        else:
            assert driver.results["per_scope"] == {}


def test_default_public_scope_history_and_previous_scope_facts_survive(tmp_path):
    with isolated_world():
        build(tmp_path)
        driver = DataflowAnalysisDriver(AnalysisConfig(
            "public-live", "LivenessAnalysis", options={"type": "dataflow analysis"}))
        scopes = sorted(World().scope_manager.get_scopes(), key=lambda s: s.get_qualname())
        old = {}
        for scope in scopes:
            driver.analyze(scope, {})
            old[scope] = driver.results["out"]
        assert set(driver.results["per_scope"]) == set(scopes)
        for scope in scopes:
            assert get_scope_result(driver.results, scope)["out"] is old[scope]
        assert driver.results["out"] is old[scopes[-1]]


def test_private_streaming_cells_match_retained_parent_class_and_sibling_scopes(tmp_path, monkeypatch):
    original = DataflowAnalysisDriver.__init__
    def retaining(self, config):
        if config.name == "liveness-analysis":
            config = AnalysisConfig(config.name, config.id, description=config.description,
                prev_analysis=config.prev_analysis, inter_procedure=config.inter_procedure,
                options={**config.options, "retain_scope_results": True})
        original(self, config)
    with monkeypatch.context() as patch:
        patch.setattr(DataflowAnalysisDriver, "__init__", retaining)
        with isolated_world():
            build(tmp_path)
            reference = cells()
    with isolated_world():
        pipeline = build(tmp_path)
        actual = cells()
        closure = pipeline.analysis_manager.get_analyzer("closure")
        assert actual == reference
        assert {"captured", "ambient"} <= actual["entry.outer.Box.getter"]
        assert {"captured"} <= actual["entry.outer.left"]
        assert {"captured", "ambient"} <= actual["entry.outer.right.nested"]
        assert {"value"} <= actual["entry.sibling.leaf"]
        assert "value" not in actual["entry.outer.left"]
        # The private consumer has copied the needed entry set into IR. Keeping
        # it does not require retaining dense facts for any completed scope.
        assert closure.liveness_analysis.results["per_scope"] == {}
        assert "in" not in closure.liveness_analysis.results
        assert "out" not in closure.liveness_analysis.results


def test_private_cleanup_releases_raw_entry_facts_but_preserves_ir_cells(tmp_path, monkeypatch):
    original = DataflowAnalysisDriver.analyze
    raw_entries = []
    def observe(self, scope, previous):
        result = original(self, scope, previous)
        if self.config.name == "liveness-analysis":
            cfg = World().scope_manager.get_ir(scope, "cfg")
            raw_entries.append(weakref.ref(self.results["out"][cfg.entry_blk]))
        return result
    monkeypatch.setattr(DataflowAnalysisDriver, "analyze", observe)
    with isolated_world():
        build(tmp_path)
        gc.collect()
        assert raw_entries
        assert all(ref() is None for ref in raw_entries)
        assert {"captured", "ambient"} <= cells()["entry.outer.Box.getter"]


def test_private_partial_failure_recomputes_the_dependency_order_on_retry(tmp_path, monkeypatch):
    with isolated_world():
        pipeline = build(tmp_path)
        expected = cells()
        closure = pipeline.analysis_manager.get_analyzer("closure")
        scopes = list(World().scope_manager.get_scopes())
        # Reset durable facts to make stale success impossible to rely on.
        for scope in scopes:
            scope.cell_vars = set()
        original = closure.liveness_analysis.solver
        class FailOneScope:
            calls = 0
            @classmethod
            def solve(cls, analysis):
                cls.calls += 1
                if cls.calls == 2:
                    raise MemoryError("synthetic partial closure failure")
                return original.solve(analysis)
        closure.liveness_analysis.solver = FailOneScope
        with pytest.raises(MemoryError, match="synthetic partial closure failure"):
            closure.analyze(World().get_entry_module(), {})
        assert FailOneScope.calls == 2
        # No checkpoint is promoted to a complete closure analysis. A retry is
        # a fresh bottom-up pass, not continuation from discarded block maps.
        closure.liveness_analysis.solver = original
        closure.analyze(World().get_entry_module(), {})
        assert cells() == expected
