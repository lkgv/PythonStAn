"""Attribute replacement cannot bypass compatibility-cache invalidation."""
import ast

from pythonstan.ir import IRModule
from pythonstan.world.scope_manager import ScopeManager


def test_replacing_public_cache_invalidates_entries_and_detaches_old_view():
    manager = ScopeManager()
    manager.build()
    scope = IRModule("subject", ast.parse(""), "subject", "subject.py")
    first, second, stale = object(), object(), object()
    manager.set_ir(scope, "cfg", first)
    old_view = manager.scope_ir
    manager.scope_ir = {}
    assert manager.get_ir(scope, "cfg") is None
    manager.set_ir(scope, "cfg", second)
    # Old dictionary aliases are detached from the authoritative new view.
    old_view[("subject", "cfg")] = stale
    old_view.clear()
    assert manager.get_ir(scope, "cfg") is second
    assert manager.scope_ir[("subject", "cfg")] is second


def test_replacement_mapping_is_fail_closed_for_ambiguous_names():
    manager = ScopeManager()
    manager.build()
    scopes = [IRModule("subject", ast.parse(""), "subject", "subject.py") for _ in range(2)]
    for scope in scopes:
        manager.set_ir(scope, "cfg", object())
    replacement = object()
    manager.scope_ir = {("subject", "cfg"): replacement}
    assert manager.scope_ir[("subject", "cfg")] is replacement
    assert all(manager.get_ir(scope, "cfg") is None for scope in scopes)
