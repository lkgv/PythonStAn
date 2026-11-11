"""Native port controls independent of the optional Inspector implementation."""
import gc
import weakref
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pythonstan.analysis.pointer.kcfa import diagnostics, points_to_set
from pythonstan.analysis.pointer.kcfa.constraints import CopyConstraint
from pythonstan.analysis.pointer.kcfa.context import CallSite
from pythonstan.analysis.pointer.kcfa.diagnostics import NativeDiagnostics
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
from pythonstan.ir.ir_statements import IRCall
from tests.round14_bootstrap.test_guarded_publication import _driver, _scope, _seed, _global


@pytest.fixture(autouse=True)
def _arena():
    points_to_set.reset_object_table()
    yield
    points_to_set.reset_object_table()


def _fingerprint(driver):
    return (dict(driver.state._env), frozenset(driver.state._pointer_flow_graph.edges),
            frozenset(driver.state._call_graph.edges), driver.solver._iteration,
            driver.solver._bootstrap_publications, driver.solver._pending_work_by_kind())


def _call(scope):
    import ast
    statement = IRCall(ast.parse("result = take(value)").body[0])
    return SimpleNamespace(call_site=CallSite(statement, scope.name, 0))


def _port(scope, call, **budgets):
    return NativeDiagnostics("targeted", "native-test", {"file": scope.stmt.filename,
        "line": 1, "model": "keyword_collector"}, **budgets)


def test_off_has_no_invented_run_counters_or_events():
    driver = _driver()
    counters = driver.solver.get_inspector_counters()
    assert driver.solver._diagnostics is None
    assert counters["mode"] == "off" and counters["cumulative"] is None
    assert counters["actual_bootstrap"]["fallbacks"] is None
    assert counters["actual_bootstrap"]["decision_coverage"] == "not_observed"
    assert driver.solver.drain_inspector_events()["events"] == []


def test_pure_counters_preserve_eager_native_stores_schedule_and_mixed_behavior(monkeypatch):
    _, scope = _scope()
    off, counted = _driver(), _driver()
    counted.solver.enable_inspector_diagnostics("counters", "counter-control")

    def forbidden(*args, **kwargs):
        raise AssertionError("counter mode formatted a source/event")

    monkeypatch.setattr(diagnostics, "_source", forbidden)
    monkeypatch.setattr(diagnostics, "_encoded", forbidden)
    for driver in (off, counted):
        # One seed has an actual pending fact and must stay queued;
        # the remaining eligible seeds may be admitted eagerly.
        driver.solver.add_constraint(scope, scope.context,
            CopyConstraint(Variable("len", VariableKind.GLOBAL), Variable("output")))
        from pythonstan.analysis.pointer.kcfa.object import ObjectFactory
        from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
        owner = _global(driver, scope, "len")
        application = PointsToSet.singleton(ObjectFactory.create_builtin_function("application_len", scope.context))
        driver.state._worklist.add((scope, NormalNode(owner), application))
        _seed(driver, scope)
        driver.solver.solve_to_fixpoint()
    assert _fingerprint(off) == _fingerprint(counted)
    assert counted.solver._inspector_enabled is False
    assert counted.solver._work_step_observer is None
    counters = counted.solver.get_inspector_counters()
    actual = counters["actual_bootstrap"]
    assert actual["observed_behavior"] == "mixed_eager_and_queued"
    assert actual["publications"] == off.solver._bootstrap_publications > 0
    assert actual["fallbacks"] == 1
    assert actual["fallback_reasons"]["existing_facts_or_consumers"] == 1
    assert counters["cumulative"]["selected_work_items"] == counted.solver._iteration
    assert counters["cumulative"]["completed_work_items"] == counted.solver._iteration
    assert counters["cumulative"]["logical_work_items"] == (counted.solver._iteration + actual["publications"])
    assert counted.solver.drain_inspector_events()["events"] == []


@pytest.mark.parametrize("name", ["selected", "bump", "output_delta"])
def test_counter_method_mutation_after_eager_publication_is_refused_and_sticky(name):
    _, scope = _scope()
    driver = _driver()
    driver.solver.enable_inspector_diagnostics("counters", "mutation-control")
    _seed(driver, scope)
    assert driver.solver._bootstrap_publications > 0
    setattr(driver.solver._diagnostics, name, lambda *args: None)
    with pytest.raises(RuntimeError, match="bootstrap contract"):
        next(driver.solver)
    delattr(driver.solver._diagnostics, name)
    with pytest.raises(RuntimeError, match="bootstrap contract"):
        next(driver.solver)


def test_targeted_admission_is_explicit_queued_fallback_without_boundaries():
    _, scope = _scope()
    driver = _driver()
    driver.solver.enable_inspector_diagnostics("targeted", "targeted-control",
        {"file": scope.stmt.filename, "line": 1, "model": "keyword_collector"})
    templates = _seed(driver, scope)
    assert driver.solver._bootstrap_publications == 0
    assert not driver.solver._inspector_enabled
    actual = driver.solver.get_inspector_counters()["actual_bootstrap"]
    assert actual["observed_behavior"] == "queued_fallback_only"
    assert actual["fallbacks"] == len(templates.seeds)
    assert actual["fallback_reasons"]["targeted_diagnostics"] == len(templates.seeds)
    driver.solver.solve_to_fixpoint()
    assert driver.solver._stats["converged"] == 1


def test_source_filter_miss_allocates_no_event_descriptor_or_reference(monkeypatch):
    _, scope = _scope()
    call = _call(scope)
    port = _port(scope, call)

    def forbidden(*args, **kwargs):
        raise AssertionError("filter miss allocated an event/descriptor")

    monkeypatch.setattr(diagnostics, "_source", forbidden)
    monkeypatch.setattr(diagnostics, "_encoded", forbidden)
    assert not port.selected(scope, call, "other_model")
    call.call_site.statement.stmt.lineno = 99
    assert port.record("model_enter", scope, call, "keyword_collector", refs={"object": object()}) is None
    assert port.health()["reference_count"] == port.health()["recorded_events"] == 0
    assert port.health()["descriptor_bytes"] == 0


def test_weak_refs_do_not_retain_graph_and_epoch_changes_refuse_mapping():
    _, scope = _scope()
    call = _call(scope)
    port = _port(scope, call)
    owner = SimpleNamespace(payload="bounded")
    # SimpleNamespace is not weak-referenceable: unsupported refs are explicit.
    assert port.record("model_enter", scope, call, "keyword_collector", refs={"owner": owner}) is None

    class Node:
        pass

    owner = Node()
    weak = weakref.ref(owner)
    sequence = port.record("model_enter", scope, call, "keyword_collector", refs={"owner": owner})
    assert sequence == 1
    batch = port.drain()
    ref = batch["events"][0]["refs"]["owner"]
    assert port.resolve(ref) is owner
    bindings = port.reference_bindings()
    assert bindings[0][0] == ref and bindings[0][1]() is owner
    del owner
    gc.collect()
    assert weak() is None and port.resolve(ref) is None
    assert port.health()["dropped_descriptors"] == 1
    points_to_set.reset_object_table()
    assert port.resolve(ref) is None
    assert port.record("model_enter", scope, call, "keyword_collector") is None
    assert port.health()["window_end"] == "arena_changed"


def test_event_and_reference_budgets_and_destructive_drain_are_bounded():
    _, scope = _scope()
    call = _call(scope)
    port = _port(scope, call, max_events=2, max_refs=1, max_bytes=8192)
    assert port.record("model_enter", scope, call, "keyword_collector", refs={"scope": scope}) == 1
    assert port.record("model_complete", scope, call, "keyword_collector", refs={"scope": scope}) == 2
    assert port.record("model_complete", scope, call, "keyword_collector") is None
    batch = port.drain()
    assert len(batch["events"]) == 2 and len(batch["descriptors"]) == 1
    assert batch["descriptors"][0]["ref"] == batch["events"][0]["refs"]["scope"]
    assert batch["health"]["window_end"] == "event_budget"
    assert batch["health"]["accounted_bytes"] <= 8192
    assert port.drain()["events"] == port.drain()["descriptors"] == []


def test_weak_watch_aba_replacement_checks_referent_identity():
    _, scope = _scope()
    call = _call(scope)
    port = _port(scope, call)

    class Node:
        pass

    expired, owner = Node(), Node()
    dead = weakref.ref(expired)
    del expired
    gc.collect()
    source = {"file": scope.stmt.filename, "line": 1, "column": 0, "call_index": 0}
    # Controlled table state simulates the otherwise nondeterministic reuse of
    # a Python address after the weak referent has died.
    port._watched_owners[id(owner)] = (dead, "target", source, "keyword_collector", 1)
    port._watch_bytes = 128 + len(diagnostics._encoded(source))
    port._watch(owner, "target", source, "keyword_collector", 2)
    assert port._watched_owners[id(owner)][0]() is owner
    assert port._watched_owners[id(owner)][4] == 2
    assert port._watch_bytes == 128 + len(diagnostics._encoded(source))


def test_cold_callbacks_are_outside_atomic_capture_boundary_and_failure_is_observational():
    _, scope = _scope()
    driver = _driver("queued")
    driver.solver.enable_inspector_boundaries()
    driver.solver.enable_inspector_diagnostics("counters", "cold-control")
    boundaries = []

    def callback(solver):
        boundaries.append((solver._inspector_step_depth, solver._inspector_boundary))
        raise OSError("controlled cold checkpoint failure")

    driver.solver.set_inspector_cold_checkpoint(callback, work_interval=1)
    templates = _seed(driver, scope)
    driver.solver.solve_to_fixpoint()
    assert driver.solver._stats["converged"] == 1
    assert all(depth == 0 and stage in ("between_work_items", "stopped") for depth, stage in boundaries)
    assert len(boundaries) == len(templates.seeds) + 1
    assert driver.solver.get_inspector_counters()["cold_checkpoint_failures"] == len(boundaries)


def test_eager_counter_cold_hook_is_rejected_without_silent_strategy_change():
    driver = _driver()
    driver.solver.enable_inspector_diagnostics("counters", "no-cold-eager")
    with pytest.raises(ValueError, match="unsupported.*eager"):
        driver.solver.set_inspector_cold_checkpoint(lambda solver: None)
    assert driver.solver._inspector_cold_checkpoint is None


def test_legacy_reset_starts_new_window_without_resetting_cumulative_counts():
    _, scope = _scope()
    driver = _driver("queued")
    driver.solver.config = replace(driver.solver.config, work_scheduling_policy="legacy")
    driver.solver.enable_inspector_diagnostics("counters", "legacy-window")
    _seed(driver, scope)
    iterator = iter(driver.solver)
    next(iterator)
    before = driver.solver.get_inspector_counters()
    iter(driver.solver)
    after = driver.solver.get_inspector_counters()
    assert before["cumulative"] == after["cumulative"]
    assert after["window"]["index"] == before["window"]["index"] + 1
    assert after["window"]["selected_work_items"] == 0
    assert after["native_window"]["selected_work_items"] == 0


def test_enqueue_counts_are_successful_requests_including_deduplicated_queue_merges():
    _, scope = _scope()
    driver = _driver("queued")
    driver.solver.enable_inspector_diagnostics("counters", "merge-counts")
    _seed(driver, scope)
    owner = _global(driver, scope, "len")
    node = NormalNode(owner)
    pending = driver.state._worklist.items_dict[node][2].val
    size = len(driver.state._worklist)
    driver.solver._enqueue_points_to(scope, node, pending)
    driver.solver._enqueue_points_to(scope, node, pending)
    counters = driver.solver.get_inspector_counters()
    assert len(driver.state._worklist) == size
    assert counters["cumulative"]["points_enqueue_accepted"] == 2
    assert "including merges" in counters["counter_definitions"]["points_enqueue_accepted"]


def test_window_allocation_failure_does_not_escape_or_skip_native_work(monkeypatch):
    _, scope = _scope()
    driver = _driver("queued")
    driver.solver.enable_inspector_diagnostics("counters", "window-allocation")
    port = driver.solver._diagnostics
    _seed(driver, scope)

    class FailingDict:
        @staticmethod
        def fromkeys(*args):
            raise MemoryError("controlled diagnostic allocation")

    monkeypatch.setattr(diagnostics, "dict", FailingDict, raising=False)
    next(iter(driver.solver))
    assert port.failures == 1
    assert port.window == 0
    assert driver.solver._iteration == 1
    assert driver.state._env
    assert driver.solver.get_inspector_counters()["window"]["coverage"] == "partial"


@pytest.mark.parametrize("selector", [
    {"file": "a.py", "line": 1, "model": "keyword_collector", "context": "unsupported"},
    {"file": "a.py", "line": 1, "model": "keyword_collector", "max_events": 3},
    {"file": "a.py", "line": True, "model": "keyword_collector"},
])
def test_selector_contract_rejects_ignored_or_unsupported_parameters(selector):
    with pytest.raises(ValueError, match="selector"):
        NativeDiagnostics("targeted", "bad-selector", selector)
