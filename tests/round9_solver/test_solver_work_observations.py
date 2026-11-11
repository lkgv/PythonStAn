"""Work observations may describe execution but must never schedule more facts."""
import pytest

from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
from tests.pointer.kcfa.test_ai_bridge import analyze_source


SOURCE = '''
def callback(): return 1
def invoke(function): function()
mapping = {"chosen": callback}
copy = dict(mapping)
copy.update(mapping)
for name, function in copy.items():
    invoke(function)
alias = mapping
mapping = alias
'''


def assert_queue_accounting(statistics):
    work = sum(statistics[name] for name in ("work_items_static", "work_items_constraint_replay",
               "work_items_builtin_replay", "work_items_points_to"))
    assert work == statistics["iterations"]
    assert statistics["work_items_builtin_replay"] == statistics["builtin_dependency_replays"]
    assert sum(statistics["pending_work_by_kind"].values()) == statistics["pending_work"]
    assert statistics["points_to_noop_work_items"] <= statistics["work_items_points_to"]
    assert statistics["builtin_replay_notifications_coalesced"] <= statistics["builtin_replay_notifications"]


@pytest.mark.parametrize("max_iterations", [40, 50000])
def test_query_frequency_preserves_work_budget_and_source_results(tmp_path, monkeypatch, max_iterations):
    original_query, original_edges = analyze_source(tmp_path, SOURCE, max_iterations=max_iterations,
                                                  enable_ai_intrinsics=True)
    original_stats = original_query.get_statistics()
    assert_queue_accounting(original_stats)
    next_step = PointerSolver.__next__
    def observed_next(solver):
        counts_before = solver._pending_work_by_kind()
        solver.query().get_statistics()
        assert solver._pending_work_by_kind() == counts_before
        return next_step(solver)
    monkeypatch.setattr(PointerSolver, "__next__", observed_next)
    observed_query, observed_edges = analyze_source(tmp_path, SOURCE, max_iterations=max_iterations,
                                                  enable_ai_intrinsics=True)
    observed_stats = observed_query.get_statistics()
    assert_queue_accounting(observed_stats)
    assert observed_edges == original_edges
    for key in ("iterations", "constraints_applied", "converged", "pending_work", "pending_work_by_kind",
                "work_items_static", "work_items_constraint_replay", "work_items_builtin_replay",
                "work_items_points_to", "points_to_noop_work_items", "points_to_delta_objects",
                "builtin_replay_notifications", "builtin_replay_notifications_coalesced"):
        assert observed_stats[key] == original_stats[key]
    if max_iterations == 40:
        assert observed_stats["iteration_limit_reached"] == 1
    else:
        assert observed_stats["converged"] == 1
        assert ("subject.invoke", "subject.callback") in observed_edges
