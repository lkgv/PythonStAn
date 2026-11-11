"""A real finite copy arena cannot starve every point request in fair mode."""
from benchmark.round11_solver.starvation_control import run


def test_same_fixed_budget_services_points_and_keeps_pending_obligations():
    result = run(size=32, budget=16)
    short, complete = result["rows"]
    legacy, fair = short["arms"]["legacy"], short["arms"]["fair"]
    assert legacy["statistics"]["work_items_static"] == 16
    assert legacy["statistics"]["work_items_points_to"] == 0
    assert fair["statistics"]["work_items_static"] == 8
    assert fair["statistics"]["work_items_points_to"] == 8
    assert fair["statistics"]["work_kind_first_service_iteration"]["points_to"] == 2
    for arm in short["arms"].values():
        assert arm["statistics"]["iterations"] == 16
        assert arm["statistics"]["converged"] == 0
        assert arm["statistics"]["iteration_limit_reached"] == 1
        assert arm["statistics"]["pending_work"] > 0
    assert complete["exact_environment_equal"]
    assert complete["arms"]["legacy"]["entire_environment"] == complete["arms"]["fair"]["entire_environment"]
    assert all(arm["target_values_observed"] == 32 for arm in complete["arms"].values())
    assert all(arm["statistics"]["converged"] == 1 for arm in complete["arms"].values())
