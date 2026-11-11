"""Representation and source-level regressions for private compact closure."""
import ast

import pytest

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.closure import ClosureAnalysis
from pythonstan.analysis.compact_liveness import CompactLivenessSolver, DecodedLivenessFacts
from pythonstan.analysis.dataflow.driver import DataflowAnalysisDriver
from pythonstan.analysis.dataflow.liveness import LivenessAnalysis
from pythonstan.analysis.dataflow.solver import WorklistSolver
from pythonstan.ir import IRModule
from pythonstan.world import World
from pythonstan.world.analysis_manager import AnalysisManager
from pythonstan.world.config import Config


SOURCE = '''
ambient = 13
def outer(seed, *args, **kwargs):
    captured = seed
    class Container:
        def method(self):
            return captured, ambient, __class__
    def decorate(fn):
        def wrapped(value=captured):
            return fn(value), kwargs, args
        return wrapped
    async def stream():
        try:
            yield captured
        finally:
            emit(ambient)
    def nonlocal_write():
        nonlocal captured
        captured = ambient
        return captured
    return Container, decorate, stream, nonlocal_write

def independent(value):
    try:
        while value:
            value = update(value)
    except Exception as error:
        value = recover(error)
    finally:
        release(value)
    return value

if guard:
    def same_name(): return first
else:
    def same_name(): return second
'''


def lower(tmp_path, source=SOURCE):
    path = tmp_path / "entry.py"
    path.write_text(source)
    World.setup()
    world = World()
    world.build(Config(str(path), str(tmp_path), mock_libs=False))
    manager = AnalysisManager()
    manager.build([])
    world.analysis_manager = manager
    module = IRModule("entry", ast.parse(source), "entry", str(path))
    sm = world.scope_manager
    sm.scopes.add(module)
    sm.names2scope["entry"] = module
    for phase in ("three address", "ir", "block cfg", "cfg"):
        manager.analysis(phase, module)
    return world, module


def closure(representation):
    return ClosureAnalysis(AnalysisConfig("closure", "Closure", options={
        "type": "closure analysis", "liveness_representation": representation}))


@pytest.mark.parametrize("source", [SOURCE,
    "def f(x):\n return len(x)\n",
    "len = replacement\ndef f(x):\n return len(x)\n",
    "def f(x):\n def g():\n  return x\n return g\n",
    "class C:\n def m(self):\n  return __class__, super()\n",
    "async def outer(x):\n async def inner():\n  yield x\n return inner\n",
])
def test_same_arena_every_raw_fact_and_cell_matches_ordinary_sets(tmp_path, source):
    world, module = lower(tmp_path, source)
    sm = world.scope_manager
    scopes = set(sm.get_scopes())
    before = {scope: set(scope.cell_vars) for scope in scopes}
    representations = {scope: tuple(sm.get_ir(scope, fmt) for fmt in ("ir", "cfg", "block cfg"))
                       for scope in scopes}
    observations = []
    for policy in ("set", "compact"):
        for scope, cells in before.items():
            scope.cell_vars = set(cells)
        driver = closure(policy)
        raw = {}
        analyze = driver.liveness_analysis.analyze
        def observe(scope, cfg):
            analyze(scope, cfg)
            raw[scope] = {direction: {node: set(fact) for node, fact
                in driver.liveness_analysis.results[direction].items()} for direction in ("in", "out")}
        driver.liveness_analysis.analyze = observe
        driver.analyze(module, {})
        assert driver.liveness_analysis.results == {"per_scope": {}}
        observations.append((raw, {scope: set(scope.cell_vars) for scope in scopes}))
        assert all(sm.get_ir(scope, fmt) is representations[scope][index]
                   for scope in scopes for index, fmt in enumerate(("ir", "cfg", "block cfg")))
    assert observations[0] == observations[1]
    assert set(observations[1][0]) == scopes


def test_decoded_copy_mutation_does_not_change_compact_facts(tmp_path):
    world, _ = lower(tmp_path, "def f(x):\n return x, ambient\n")
    sm = world.scope_manager
    scope = sm.names2scope["entry.f"]
    cfg = sm.get_ir(scope, "cfg")
    config = AnalysisConfig("live", "LivenessAnalysis", options={"type": "dataflow analysis"})
    incoming, outgoing = CompactLivenessSolver.solve(LivenessAnalysis(scope, cfg, config))
    assert isinstance(incoming, DecodedLivenessFacts)
    assert isinstance(outgoing, DecodedLivenessFacts)
    entry = cfg.get_entry()
    expected = outgoing[entry]
    assert {"x", "ambient"} <= expected
    copy = outgoing[entry]
    assert copy == expected and copy is not expected
    copy.clear()
    assert outgoing[entry] == expected
    assert outgoing.statistics["decoded_fact_cache_entries"] == 0
    assert all(type(mask) is int for mask in outgoing._facts.values())
    with pytest.raises(TypeError):
        outgoing[entry] = {"invented"}


def test_public_liveness_keeps_mutable_dictionary_and_fact_identity(tmp_path):
    world, _ = lower(tmp_path, "def f(x):\n return x\n")
    scope = world.scope_manager.names2scope["entry.f"]
    cfg = world.scope_manager.get_ir(scope, "cfg")
    driver = DataflowAnalysisDriver(AnalysisConfig("live", "LivenessAnalysis", options={
        "type": "dataflow analysis"}))
    assert driver.retain_scope_results is True
    assert driver.solver is not CompactLivenessSolver
    driver.analyze(scope, {})
    facts = driver.results["out"]
    assert type(facts) is dict
    entry_fact = facts[cfg.get_entry()]
    entry_fact.add("diagnostic_mutation")
    assert facts[cfg.get_entry()] is entry_fact
    assert "diagnostic_mutation" in driver.results["per_scope"][scope]["out"][cfg.get_entry()]


def test_private_retention_enabled_after_construction_uses_mutable_sets(tmp_path):
    world, module = lower(tmp_path)
    driver = closure("compact")
    driver.liveness_analysis.retain_scope_results = True
    driver.analyze(module, {})
    scope = world.scope_manager.names2scope["entry.outer"]
    result = driver.liveness_analysis.results["per_scope"][scope]
    assert type(result["in"]) is dict and type(result["out"]) is dict
    entry = world.scope_manager.get_ir(scope, "cfg").get_entry()
    result["out"][entry].add("retained_observation")
    assert "retained_observation" in result["out"][entry]


def test_custom_analysis_override_uses_ordinary_solver(tmp_path):
    world, _ = lower(tmp_path, "def f(x):\n return x\n")
    scope = world.scope_manager.names2scope["entry.f"]
    cfg = world.scope_manager.get_ir(scope, "cfg")
    config = AnalysisConfig("live", "LivenessAnalysis", options={"type": "dataflow analysis"})
    analysis = LivenessAnalysis(scope, cfg, config)
    ordinary = analysis.transfer_node
    analysis.transfer_node = lambda node, fact: ordinary(node, fact) | {"custom_use"}
    incoming, outgoing = CompactLivenessSolver.solve(analysis)
    assert type(incoming) is dict and type(outgoing) is dict
    assert "custom_use" in outgoing[cfg.get_entry()]
    assert (incoming, outgoing) == WorklistSolver.solve(analysis)


@pytest.mark.parametrize("policy", [None, False, 0, [], {}, "other"])
def test_invalid_private_representation_is_rejected(policy):
    with pytest.raises(ValueError, match="liveness_representation"):
        closure(policy)


def test_default_private_solver_can_be_replaced_for_failure_and_retry(tmp_path):
    world, module = lower(tmp_path)
    driver = closure("compact")
    assert driver.liveness_analysis.solver is CompactLivenessSolver
    class Failure:
        @staticmethod
        def solve(analysis):
            raise MemoryError("injected compact boundary failure")
    driver.liveness_analysis.solver = Failure
    with pytest.raises(MemoryError, match="injected compact boundary failure"):
        driver.analyze(module, {})
    driver.liveness_analysis.solver = CompactLivenessSolver
    driver.analyze(module, {})
    by_name = world.scope_manager.names2scope
    assert {"captured", "ambient", "__class__"} <= by_name["entry.outer.Container.method"].cell_vars
    assert {"captured"} <= by_name["entry.outer.stream"].cell_vars
