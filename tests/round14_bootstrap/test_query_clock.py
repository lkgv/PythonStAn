"""A retained public query must report the current work window and bootstrap."""
from dataclasses import replace

import pytest

from pythonstan.analysis.pointer.kcfa.points_to_set import reset_object_table
from pythonstan.analysis.pointer.kcfa.variable import Variable
from tests.round14_bootstrap.test_guarded_publication import _driver, _global, _scope, _seed


@pytest.mark.parametrize("policy", ["queued", "eager_inert"])
def test_query_retained_before_initialization_survives_legacy_budget_reset(policy):
    reset_object_table()
    try:
        _, scope = _scope()
        driver = _driver(policy)
        config = replace(driver.solver.config, work_scheduling_policy="legacy")
        driver.solver.config = driver.kcfa_config = config
        query = driver.solver.query()
        assert query.get_statistics()["logical_work_items"] == 0
        templates = _seed(driver, scope)
        source = _global(driver, scope, "len")
        sink = driver.state.get_variable(scope, scope.context, Variable("retained_query_sink"))
        driver.state._add_var_points_flow(source, sink)
        driver.solver.solve_to_fixpoint()
        assert driver.state.get_points_to(sink)
        measured = query.get_statistics()
        assert measured["converged"] == 1
        assert measured["selected_work_items"] == measured["iterations"] > 0
        assert measured["logical_work_items"] == measured["selected_work_items"] + measured["bootstrap_publications"]
        expected_bootstrap = len(templates.seeds) if policy == "eager_inert" else 0
        assert measured["bootstrap_publications"] == expected_bootstrap
        iter(driver.solver)
        restarted = query.get_statistics()
        assert restarted["selected_work_items"] == restarted["iterations"] == 0
        assert restarted["bootstrap_publications"] == restarted["logical_work_items"] == expected_bootstrap
        assert query.get_semantic_status()["constraint_fixpoint_reached"] is False
        assert driver.state.get_points_to(sink)
    finally:
        reset_object_table()
