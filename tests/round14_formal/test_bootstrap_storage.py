"""Independent storage guard and full queued-fallback controls."""
import pytest

from pythonstan.analysis.pointer.kcfa.points_to_set import reset_object_table
from tests.round14_formal.test_guarded_bootstrap import _Hook, _driver, _location, _seed
from tests.round13_scheduler.test_bootstrap_obligations import _scopes


@pytest.fixture(autouse=True)
def isolated_epoch():
    reset_object_table()
    yield
    reset_object_table()


def test_fully_queued_custom_hook_fallback_does_not_amend_raw_watch_replay():
    from pythonstan.analysis.pointer.kcfa.bootstrap import bootstrap_statistics

    context, scopes = _scopes(count=1)
    queued, fallback = _driver("queued", _Hook()), _driver("eager_inert", _Hook())
    for driver in (queued, fallback):
        _seed(driver, context, scopes)
        driver.solver.solve_to_fixpoint()
        assert bootstrap_statistics(driver.solver)["bootstrap_publications"] == 0
        assert driver.solver._stats["converged"] == 1
        driver.solver._watch_builtin_variable(_location(driver, scopes[0], "len"), ("late_raw_watch",))
    assert list(fallback.solver._builtin_replays) == list(queued.solver._builtin_replays) == []


def test_custom_environment_store_callback_declines_eager_admission():
    from pythonstan.analysis.pointer.kcfa.bootstrap import bootstrap_statistics

    class RecordingDict(dict):
        def __init__(self):
            super().__init__()
            self.writes = []

        def __setitem__(self, key, value):
            self.writes.append(key)
            return super().__setitem__(key, value)

    context, scopes = _scopes(count=1)
    driver = _driver()
    environment = RecordingDict()
    driver.state._env = environment
    _seed(driver, context, scopes)
    assert bootstrap_statistics(driver.solver)["bootstrap_publications"] == 0
    assert not environment.writes
    driver.solver.solve_to_fixpoint()
    assert environment.writes
    assert driver.state.get_points_to(_location(driver, scopes[0], "len"))


def test_equal_environment_replacement_after_admission_is_sticky_refusal():
    context, scopes = _scopes(count=1)
    driver = _driver()
    _seed(driver, context, scopes)
    original = driver.state._env
    driver.state._env = dict(original)
    with pytest.raises(RuntimeError):
        iter(driver.solver)
    driver.state._env = original
    with pytest.raises(RuntimeError):
        iter(driver.solver)
