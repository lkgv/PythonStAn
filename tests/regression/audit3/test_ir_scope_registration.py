"""Lowering uses the nominal registration service for each lexical scope."""

from __future__ import annotations

import ast
from typing import Literal, override

from pythonstan.analysis.transform.ir import IRTransformer
from pythonstan.ir import IRClass, IRFunc, IRModule, IRScope
from pythonstan.world.scope_manager import ScopeManager
from pythonstan.world.world import World


class _RegistrationRecorder(ScopeManager):
    def __init__(self) -> None:
        self.registrations: list[tuple[Literal["function", "class"], IRScope, IRScope]] = []
        self.build()

    @override
    def add_func(self, scope: IRScope, func: IRFunc) -> None:
        assert isinstance(func, IRFunc)
        self.registrations.append(("function", scope, func))
        super().add_func(scope, func)

    @override
    def add_class(self, scope: IRScope, cls: IRClass) -> None:
        assert isinstance(cls, IRClass), "Function lowering called the class registration service"
        self.registrations.append(("class", scope, cls))
        super().add_class(scope, cls)


def test_functions_methods_and_classes_use_their_registration_contract() -> None:
    source = """def outer():
    def inner():
        pass
    class Local:
        pass
class Container:
    def method(self):
        pass
    async def async_method(self):
        pass
    class Nested:
        pass
"""
    World.setup()
    manager = _RegistrationRecorder()
    World().scope_manager = manager
    tree = ast.parse(source)
    module = IRModule("registration", tree, "registration", "registration.py")
    transformer = IRTransformer(module)
    try:
        transformer.process_stmts(tree.body)
        observed = {child.get_qualname(): kind for kind, _, child in manager.registrations}
        assert observed == {
            "registration.outer.inner": "function",
            "registration.outer.Local": "class",
            "registration.outer": "function",
            "registration.Container.method": "function",
            "registration.Container.async_method": "function",
            "registration.Container.Nested": "class",
            "registration.Container": "class",
        }
        for _, parent, child in manager.registrations:
            assert child in manager.scopes
            assert manager.father[child] is parent
            assert child in manager.get_subscopes(parent)
            assert child.source_module is module
            assert manager.get_statement_ir(child) is not None
        for _, _, child in manager.registrations:
            if isinstance(child, IRFunc):
                assert child.is_instance_method is isinstance(manager.father[child], IRClass)
    finally:
        World.setup()
