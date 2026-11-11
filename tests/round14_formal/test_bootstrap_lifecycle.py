"""Independent finite lifecycle controls under a frozen guard amendment."""
import pytest

from pythonstan.analysis.pointer.kcfa.processor.processor import Processor
from pythonstan.analysis.pointer.kcfa.points_to_set import reset_object_table
from tests.round14_formal.test_guarded_bootstrap import _driver, _seed
from tests.round13_scheduler.test_bootstrap_obligations import _scopes


@pytest.fixture(autouse=True)
def isolated_epoch():
    reset_object_table()
    yield
    reset_object_table()


@pytest.mark.parametrize("diagnostic", ["inspector", "observer"])
def test_later_diagnostics_are_refused_without_installing_them(diagnostic):
    from pythonstan.analysis.pointer.kcfa.bootstrap import bootstrap_statistics

    context, scopes = _scopes(count=1)
    driver = _driver()
    _seed(driver, context, scopes)
    assert bootstrap_statistics(driver.solver)["bootstrap_publications"] > 0
    with pytest.raises(ValueError):
        if diagnostic == "inspector":
            driver.solver.enable_inspector_boundaries()
        else:
            driver.solver.set_work_step_observer(lambda event: None)
    assert driver.solver._inspector_enabled is False
    assert driver.solver._work_step_observer is None
    driver.solver.solve_to_fixpoint()
    assert driver.solver._stats["converged"] == 1


def test_invalid_lifetime_refuses_before_clock_reset_and_remains_invalid_after_restore():
    context, scopes = _scopes(count=1)
    driver = _driver()
    _seed(driver, context, scopes)
    original = driver.solver.processor
    driver.solver.processor = Processor()
    driver.solver._iteration = 17
    driver.solver._stats["work_items_points"] = 11
    with pytest.raises(RuntimeError):
        iter(driver.solver)
    assert driver.solver._iteration == 17
    assert driver.solver._stats["work_items_points"] == 11
    driver.solver.processor = original
    with pytest.raises(RuntimeError):
        iter(driver.solver)
    assert driver.solver._iteration == 17


@pytest.mark.parametrize("entry", ["direct-helper", "production-initializer"])
def test_test_only_certified_transfer_fault_is_permanently_tainted(monkeypatch, entry):
    """Deliberately bypass method certification to exercise exception handling.

    The private native snapshot is rebound ONLY inside this test. This is not
    a claim that client-overridden transfers or custom stores are admitted.
    The injected failure models allocation failure after the first real store.
    """
    from pythonstan.analysis.pointer.kcfa import bootstrap
    from pythonstan.analysis.pointer.kcfa.context import Ctx
    from pythonstan.analysis.pointer.kcfa.object import ObjectFactory
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    from tests.round14_formal.test_guarded_bootstrap import _location

    context, scopes = _scopes(count=1)
    driver = _driver()

    def partial_native_transfer(self, scope, node, points):
        self.state.set_points_to(node, points)
        raise MemoryError("controlled failure between processed and Ctx stores")

    certificate = tuple((name, partial_native_transfer, partial_native_transfer.__code__)
                        if name == "_apply_dynamic" else (name, function, code)
                        for name, function, code in bootstrap._SOLVER_METHODS)
    with monkeypatch.context() as local:
        local.setattr(PointerSolver, "_apply_dynamic", partial_native_transfer)
        local.setattr(bootstrap, "_SOLVER_METHODS", certificate)
        with pytest.raises(MemoryError, match="controlled failure"):
            if entry == "direct-helper":
                source = _location(driver, scopes[0], "len")
                points = PointsToSet.singleton(ObjectFactory.create_builtin_function("len", context))
                bootstrap.try_publish_builtin_seed(driver.solver, scopes[0], source, points)
            else:
                _seed(driver, context, scopes)
    assert any(isinstance(location, NormalNode) for location in driver.state._env)
    assert not any(isinstance(location, Ctx) for location in driver.state._env)
    assert driver.solver._bootstrap_invalid is True
    assert driver.solver._inspector_tainted is True
    assert driver.solver._stats["converged"] == 0
    assert bootstrap.bootstrap_statistics(driver.solver)["bootstrap_publications"] == 0
    with pytest.raises(RuntimeError, match="sticky"):
        iter(driver.solver)


def test_project_config_roundtrip_and_native_policy_pass_through(tmp_path):
    from dataclasses import asdict
    from pythonstan.analysis.project import ProjectConfig, analyze_project

    (tmp_path / "app.py").write_text('def chosen():\n    return 7\ndef entry():\n    return chosen()\nentry()\n')
    reports = {}
    assert ProjectConfig(str(tmp_path), "app.py").builtin_bootstrap_policy == "queued"
    for policy in ("queued", "eager_inert"):
        config = ProjectConfig.from_dict({"project_root": str(tmp_path), "entry_file": "app.py",
            "auto_framework_entries": False, "max_iterations": 2000,
            "builtin_bootstrap_policy": policy})
        assert ProjectConfig.from_dict(asdict(config)) == config
        reports[policy] = analyze_project(config)
        assert reports[policy]["solver_completed"] is True
        assert ["app.entry", "app.chosen"] in reports[policy]["call_edges"]
        assert reports[policy]["whole_program_soundness_proved"] is False
    assert reports["queued"]["statistics"]["bootstrap_publications"] == 0
    assert reports["eager_inert"]["statistics"]["bootstrap_publications"] > 0
