"""Object identity, compatibility mutation and guarded retirement obligations."""
import ast

import pytest

from pythonstan.ir import IRFunc, IRModule
from pythonstan.world.scope_manager import ScopeManager


def manager(*scopes):
    result = ScopeManager()
    result.build()
    result.scopes.update(scopes)
    return result


def twins():
    module = IRModule("package.child", ast.parse(""), "child", "package/child.py")
    lexical = IRFunc("package.child", ast.parse("def child(): return 1").body[0])
    return module, lexical


def test_module_and_lexical_names_never_alias_canonical_ir():
    module, lexical = twins()
    sm = manager(module, lexical)
    bodies, cfgs = (object(), object()), (object(), object())
    for scope, body, cfg in zip((module, lexical), bodies, cfgs):
        sm.set_ir(scope, "ir", body)
        sm.set_ir(scope, "cfg", cfg)
    for index, scope in enumerate((module, lexical)):
        assert sm.get_ir(scope, "ir") is bodies[index]
        assert sm.get_ir(scope, "cfg") is cfgs[index]
    assert sm.scope_ir[("package.child", "cfg")] is cfgs[1]


@pytest.mark.parametrize("mutation", ["pop", "delete", "clear", "popitem", "assign", "update", "ior"])
def test_legacy_mutations_cannot_resurrect_ambiguous_exact_entries(mutation):
    first, second = twins()
    sm = manager(first, second)
    sm.set_ir(first, "cfg", object())
    sm.set_ir(second, "cfg", object())
    key, replacement = (first.get_qualname(), "cfg"), object()
    if mutation == "pop":
        sm.scope_ir.pop(key)
    elif mutation == "delete":
        del sm.scope_ir[key]
    elif mutation == "clear":
        sm.scope_ir.clear()
    elif mutation == "popitem":
        assert sm.scope_ir.popitem()[0] == key
    elif mutation == "assign":
        sm.scope_ir[key] = replacement
    elif mutation == "update":
        sm.scope_ir.update({key: replacement})
    else:
        sm.scope_ir |= {key: replacement}
    assert sm.get_ir(first, "cfg") is None
    assert sm.get_ir(second, "cfg") is None
    # Rebuilding one owner must not resurrect the invalidated other owner.
    rebuilt = object()
    sm.set_ir(first, "cfg", rebuilt)
    assert sm.get_ir(first, "cfg") is rebuilt
    assert sm.get_ir(second, "cfg") is None


def test_legacy_unique_owner_assignment_updates_the_exact_cache():
    scope, _ = twins()
    sm = manager(scope)
    sm.set_ir(scope, "cfg", object())
    key, replacement = (scope.get_qualname(), "cfg"), object()
    sm.scope_ir[key] = replacement
    assert sm.get_ir(scope, "cfg") is replacement
    sm.scope_ir.clear()
    assert sm.get_ir(scope, "cfg") is None
    after_clear = object()
    sm.scope_ir.update({key: after_clear})
    assert sm.get_ir(scope, "cfg") is after_clear


def test_legacy_setdefault_preserves_existing_value_but_never_rebuilds_ambiguous_owner():
    first, second = twins()
    sm = manager(first, second)
    a, b, replacement = object(), object(), object()
    sm.set_ir(first, "cfg", a)
    sm.set_ir(second, "cfg", b)
    key = (first.get_qualname(), "cfg")
    assert sm.scope_ir.setdefault(key, replacement) is b
    assert sm.get_ir(first, "cfg") is a
    assert sm.get_ir(second, "cfg") is b
    sm.scope_ir.pop(key)
    assert sm.scope_ir.setdefault(key, replacement) is replacement
    assert sm.get_ir(first, "cfg") is None
    assert sm.get_ir(second, "cfg") is None


def test_retirement_requires_exact_expected_identity_and_preserves_other_owner():
    first, second = twins()
    sm = manager(first, second)
    a, b, block = object(), object(), object()
    sm.set_ir(first, "cfg", a)
    sm.set_ir(second, "cfg", b)
    sm.set_ir(first, "block cfg", block)
    assert sm.retire_ir(first, "cfg", b) is False
    assert sm.get_ir(first, "cfg") is a
    assert sm.retire_ir(first, "cfg", a) is True
    assert sm.get_ir(first, "cfg") is None
    assert sm.get_ir(second, "cfg") is b
    assert sm.scope_ir[(first.get_qualname(), "cfg")] is b
    assert sm.get_ir(first, "block cfg") is block
    assert sm.retire_ir(first, "cfg", a) is False
    assert sm.retire_ir(second, "cfg", b) is True
    assert (first.get_qualname(), "cfg") not in sm.scope_ir


def test_an_unregistered_equal_name_scope_cannot_read_or_retire_another_owner():
    first, second = twins()
    sm = manager(first)
    original = object()
    sm.set_ir(first, "cfg", original)
    assert sm.get_ir(second, "cfg") is None
    assert sm.retire_ir(second, "cfg", original) is False
    assert sm.get_ir(first, "cfg") is original


@pytest.mark.parametrize("mutation", ["clear", "assign", "update", "pop"])
def test_replaced_debug_view_alias_cannot_mutate_the_active_exact_cache(mutation):
    first, second = twins()
    unique = IRModule("unique", ast.parse(""), "unique", "unique.py")
    sm = manager(first, second, unique)
    sm.set_ir(first, "cfg", object())
    sm.set_ir(second, "cfg", object())
    sm.set_ir(unique, "cfg", object())
    old_view = sm.scope_ir
    key, replacement = (unique.get_qualname(), "cfg"), object()
    # Rebinding from a name view cannot retain ambiguous exact entries.
    sm.scope_ir = {key: replacement}
    assert sm.get_ir(first, "cfg") is None
    assert sm.get_ir(second, "cfg") is None
    if mutation == "clear":
        old_view.clear()
    elif mutation == "assign":
        old_view[key] = object()
    elif mutation == "update":
        old_view.update({key: object()})
    else:
        old_view.pop(key)
    assert sm.get_ir(unique, "cfg") is replacement
    assert sm.scope_ir[key] is replacement
