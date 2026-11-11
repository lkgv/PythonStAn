"""Exact facts and representation ownership across module-local closure."""
import ast
import gc
import weakref

import pytest

from pythonstan.analysis import AnalysisConfig
from pythonstan.analysis.closure import ClosureAnalysis
from pythonstan.analysis.project import ProjectConfig, analyze_project
from pythonstan.analysis.project.frontend_lifetime import ModuleClosureLifetime, FrontendLifetimeError
from pythonstan.ir import IRModule, IRFunc
from pythonstan.world import World
from pythonstan.world.analysis_manager import AnalysisManager
from pythonstan.world.config import Config


SOURCES = {
    "entry": '''ambient = 7
def outer(seed):
    captured = seed
    class Box:
        def method(self):
            return captured + ambient
    def decorator(fn):
        def wrapped(arg=captured):
            return fn(arg) + ambient
        return wrapped
    async def stream():
        yield captured
    def sibling():
        nonlocal captured
        captured = ambient
        return captured
    return Box, decorator, stream, sibling
''',
    "other": '''global_value = 9
def factory(seed):
    def leaf():
        return seed + global_value
    return leaf
'''}


def lower(tmp_path, sources=SOURCES):
    World.setup(); world = World()
    world.build(Config(str(tmp_path / "entry.py"), str(tmp_path), mock_libs=False))
    manager = AnalysisManager(); manager.build([]); world.analysis_manager = manager
    modules = []
    for name, text in sources.items():
        path = tmp_path / (name.replace(".", "/") + ".py")
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)
        module = IRModule(name, ast.parse(text), name.rsplit(".", 1)[-1], str(path))
        world.scope_manager.scopes.add(module)
        world.scope_manager.names2scope[name] = module
        for phase in ("three address", "ir", "block cfg", "cfg"):
            manager.analysis(phase, module)
        modules.append(module)
    return world, modules


def closure():
    return ClosureAnalysis(AnalysisConfig("closure", "Closure", options={"type": "closure analysis"}))


def facts_observer(driver):
    facts = {}
    original = driver.liveness_analysis.analyze
    def observe(scope, cfg):
        original(scope, cfg)
        facts[scope] = {direction: sorted((node.idx, sorted(fact)) for node, fact
            in driver.liveness_analysis.results[direction].items()) for direction in ("in", "out")}
    driver.liveness_analysis.analyze = observe
    return facts


def test_module_partition_has_identical_raw_facts_and_cells_and_releases_only_cfg(tmp_path):
    world, modules = lower(tmp_path)
    sm = world.scope_manager
    scopes = set(sm.get_scopes())
    initial = {scope: set(scope.cell_vars) for scope in scopes}
    reference = closure(); expected_facts = facts_observer(reference)
    reference.analyze(modules[0], {})
    expected_cells = {scope: set(scope.cell_vars) for scope in scopes}
    for scope, values in initial.items(): scope.cell_vars = set(values)
    cfg_refs = [weakref.ref(sm.get_ir(scope, "cfg")) for scope in scopes]
    canonical = {scope: (sm.get_ir(scope, "ir"), sm.get_ir(scope, "block cfg")) for scope in scopes}
    actual = closure(); actual_facts = facts_observer(actual)
    stats = {}; owner = ModuleClosureLifetime(actual, SOURCES, stats)
    for module in modules: assert owner.consume(module)
    owner.finish()
    assert actual_facts == expected_facts
    assert {scope: set(scope.cell_vars) for scope in scopes} == expected_cells
    assert set(sm.get_scopes()) == scopes
    assert stats["retired_statement_cfgs"] == len(scopes)
    assert stats["final_closure_scopes"] == 0
    assert all(sm.get_ir(scope, "cfg") is None for scope in scopes)
    assert all((sm.get_ir(scope, "ir"), sm.get_ir(scope, "block cfg")) == canonical[scope] for scope in scopes)
    gc.collect()
    assert all(ref() is None for ref in cfg_refs)


def test_analyze_module_uses_identity_tree_and_leaves_other_module_unconsumed(tmp_path):
    world, modules = lower(tmp_path)
    driver = closure(); seen = facts_observer(driver)
    driver.analyze_module(modules[0])
    assert set(seen) == set(driver.module_scopes(modules[0]))
    assert not set(seen).intersection(driver.module_scopes(modules[1]))
    with pytest.raises(ValueError, match="omits a lexical child"):
        driver.analyze_scopes([modules[0]])


def test_duplicate_names_are_conservative_retained_fallback(tmp_path):
    sources = {"entry": "if flag:\n def f(): return first\nelse:\n def f(): return second\n"}
    world, modules = lower(tmp_path, sources)
    driver = closure(); stats = {}; owner = ModuleClosureLifetime(driver, sources, stats)
    scopes = driver.module_scopes(modules[0])
    assert len([s for s in scopes if s.get_qualname() == "entry.f"]) == 2
    assert not owner.consume(modules[0])
    assert stats["deferred_modules"][0]["reasons"] == ["duplicate_lexical_qualified_name"]
    owner.finish()
    assert stats["retired_statement_cfgs"] == 0
    assert all(world.scope_manager.get_ir(scope, "cfg") is not None for scope in scopes)


def test_inventory_name_collision_defers_before_a_later_module_can_overwrite(tmp_path):
    sources = {"entry": "def child():\n return outer\n", "entry.child": "def f():\n return inner\n"}
    world, modules = lower(tmp_path, sources)
    driver = closure(); stats = {}; owner = ModuleClosureLifetime(driver, sources, stats)
    assert not owner.consume(modules[0])
    assert not owner.consume(modules[1])
    owner.finish()
    assert stats["retired_statement_cfgs"] == 0
    assert stats["ownership_valid"]


def test_late_lexical_child_fails_closed_after_parent_retirement(tmp_path):
    world, modules = lower(tmp_path, {"entry": "x = 1\n"})
    stats = {}; owner = ModuleClosureLifetime(closure(), ["entry"], stats)
    owner.consume(modules[0])
    node = ast.parse("def late():\n return hidden\n").body[0]
    late = IRFunc("entry.late", node)
    world.scope_manager.add_func(modules[0], late)
    with pytest.raises(FrontendLifetimeError, match="lexical child appeared"):
        owner.finish()
    assert not stats["ownership_valid"]


def test_module_closure_allocation_failure_preserves_actual_phase_and_inventory(tmp_path, monkeypatch):
    (tmp_path / "entry.py").write_text("def f():\n return ambient\n")
    (tmp_path / "later.py").write_text("x = 1\n")
    def fail(self, selected):
        raise MemoryError("synthetic module closure allocation failure")
    monkeypatch.setattr(ClosureAnalysis, "analyze_scopes", fail)
    events = []
    report = analyze_project({"project_root": str(tmp_path), "entry_file": "entry.py"}, progress=events.append)
    assert report["status"] == "resource_exhausted"
    assert report["analysis_failure_phase"] == "module_closure_started"
    assert not report["solver_completed"]
    assert not report["analysis_executed"]
    assert len(report["modules"]) == 2
    assert report["modules"][0]["lowered"]
    assert not report["modules"][1]["parsed"]
    assert events[-1]["phase"] == "analysis_resource_exhausted"


@pytest.mark.parametrize("value", [None, False, 1, "stream", []])
def test_policy_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="frontend_lifetime_policy"):
        ProjectConfig("/tmp", "entry.py", frontend_lifetime_policy=value)


@pytest.mark.parametrize("policy", ["auto", "retain", "module_closure"])
def test_frontend_inspection_keeps_cfg_for_every_policy(tmp_path, policy):
    (tmp_path / "entry.py").write_text("def f():\n return ambient\n")
    report = analyze_project({"project_root": str(tmp_path), "entry_file": "entry.py",
        "analysis_stage": "frontend", "frontend_lifetime_policy": policy})
    assert report["status"] == "frontend_completed"
    assert report["frontend_lifetime"]["effective_policy"] == "retain"
    assert all(World().scope_manager.get_ir(scope, "cfg") is not None
               for scope in World().scope_manager.get_scopes())


@pytest.mark.parametrize("policy", ["auto", "retain", "module_closure"])
def test_solver_policy_is_reported_and_retains_block_cfg(tmp_path, policy):
    (tmp_path / "entry.py").write_text("def f():\n return 1\nx = f()\n")
    report = analyze_project({"project_root": str(tmp_path), "entry_file": "entry.py",
        "auto_framework_entries": False, "frontend_lifetime_policy": policy})
    assert report["solver_completed"], report.get("analysis_error")
    streamed = policy != "retain"
    assert report["frontend_lifetime"]["effective_policy"] == ("module_closure" if streamed else "retain")
    assert all((World().scope_manager.get_ir(scope, "cfg") is None) is streamed
               for scope in World().scope_manager.get_scopes())
    assert all(World().scope_manager.get_ir(scope, "block cfg") is not None
               for scope in World().scope_manager.get_scopes())
