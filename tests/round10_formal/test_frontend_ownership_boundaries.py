"""Independent ownership adversaries for project-local CFG consumption."""
import ast
from types import SimpleNamespace

import pytest

from pythonstan.analysis.project.frontend_lifetime import (
    FrontendLifetimeError, ModuleClosureLifetime,
)
from pythonstan.ir import IRFunc, IRModule
from pythonstan.world.scope_manager import ScopeManager


def function(name):
    return IRFunc(name, ast.parse("def child(): pass").body[0])


@pytest.fixture
def model():
    sm = ScopeManager()
    sm.build()
    module = IRModule("owned", ast.parse(""), "owned", "owned.py")
    sm.scopes.add(module)
    sm.set_ir(module, "cfg", object())
    sm.set_ir(module, "block cfg", object())
    calls = []
    def tree(root):
        result, pending = [], [root]
        while pending:
            node = pending.pop()
            result.append(node)
            pending.extend(sm.get_subscopes(node))
        return tuple(result)
    closure = SimpleNamespace(world=SimpleNamespace(scope_manager=sm),
                              module_scopes=tree,
                              analyze_scopes=lambda scopes: calls.append(tuple(scopes)))
    report = {}
    lifetime = ModuleClosureLifetime(closure, ("owned",), report)
    return sm, module, closure, lifetime, report, calls


def test_only_statement_cfg_is_consumed_and_ir_scope_identity_survives(model):
    sm, module, _, lifetime, report, _ = model
    child = function("owned.child")
    sm.add_func(module, child)
    sm.set_ir(child, "cfg", object())
    sm.set_ir(child, "block cfg", object())
    sm.set_ir(child, "ir", ["canonical body"])
    blocks = {scope: sm.get_ir(scope, "block cfg") for scope in (module, child)}
    canonical = sm.get_ir(child, "ir")
    assert lifetime.consume(module)
    lifetime.finish()
    assert report["retired_statement_cfgs"] == 2
    for scope in (module, child):
        assert sm.get_ir(scope, "cfg") is None
        assert sm.get_ir(scope, "block cfg") is blocks[scope]
        assert scope in sm.get_scopes()
    assert sm.get_ir(child, "ir") is canonical


def test_cfg_replacement_during_closure_fails_before_any_retirement(model):
    sm, module, closure, lifetime, report, _ = model
    child = function("owned.child")
    sm.add_func(module, child)
    sm.set_ir(child, "cfg", object())
    old_module_cfg = sm.get_ir(module, "cfg")
    replacement = object()
    closure.analyze_scopes = lambda scopes: sm.set_ir(child, "cfg", replacement)
    with pytest.raises(FrontendLifetimeError, match="ownership changed"):
        lifetime.consume(module)
    assert report["ownership_valid"] is False
    assert report["retired_statement_cfgs"] == 0
    assert sm.get_ir(module, "cfg") is old_module_cfg
    assert sm.get_ir(child, "cfg") is replacement


def test_duplicate_lexical_name_is_deferred_and_never_authorizes_eviction(model):
    sm, module, _, lifetime, report, calls = model
    first, second = function("owned.child"), function("owned.child")
    sm.add_func(module, first)
    sm.add_func(module, second)
    sm.set_ir(first, "cfg", object())
    sm.set_ir(second, "cfg", object())
    assert lifetime.consume(module) is False
    assert calls == []
    lifetime.finish()
    assert set(calls[0]) == {module, first, second}
    assert report["retired_statement_cfgs"] == 0
    assert "duplicate_lexical_qualified_name" in report["deferred_modules"][0]["reasons"]
    assert sm.get_ir(first, "cfg") is not None
    assert sm.get_ir(second, "cfg") is not None


def test_inventoried_module_and_lexical_name_collision_is_deferred(model):
    sm, module, closure, _, _, calls = model
    child = function("owned.child")
    sm.add_func(module, child)
    sm.set_ir(child, "cfg", object())
    report = {}
    lifetime = ModuleClosureLifetime(closure, ("owned", "owned.child"), report)
    assert lifetime.consume(module) is False
    assert calls == []
    assert "lexical_name_matches_inventory_module" in report["deferred_modules"][0]["reasons"]


def test_unexpected_late_name_collision_is_an_explicit_failure(model):
    sm, module, _, lifetime, report, _ = model
    assert lifetime.consume(module)
    newer = IRModule("owned", ast.parse(""), "owned", "different.py")
    sm.scopes.add(newer)
    sm.set_ir(newer, "cfg", object())
    with pytest.raises(FrontendLifetimeError, match="retir"):
        lifetime.consume(newer)
    assert report["ownership_valid"] is False
    assert sm.get_ir(newer, "cfg") is not None


def test_new_distinct_name_descendant_during_closure_cannot_retire_parent(model):
    sm, module, closure, lifetime, report, _ = model
    old_cfg = sm.get_ir(module, "cfg")
    def grow(scopes):
        child = function("owned.new_child")
        sm.add_func(module, child)
        sm.set_ir(child, "cfg", object())
    closure.analyze_scopes = grow
    with pytest.raises(FrontendLifetimeError):
        lifetime.consume(module)
    assert report["ownership_valid"] is False
    assert report["retired_statement_cfgs"] == 0
    assert sm.get_ir(module, "cfg") is old_cfg


def test_new_distinct_name_descendant_after_consumption_cannot_bypass_parent(model):
    sm, module, _, lifetime, report, _ = model
    assert lifetime.consume(module)
    child = function("owned.late_child")
    sm.add_func(module, child)
    sm.set_ir(child, "cfg", object())
    with pytest.raises(FrontendLifetimeError):
        lifetime.finish()
    assert report["ownership_valid"] is False
