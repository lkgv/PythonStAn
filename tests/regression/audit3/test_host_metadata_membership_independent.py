"""Actual host slots and opaque cached exports retain their Python behavior."""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import override

import pytest

from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.heap_model import Field
from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject,
    AllocKind,
    AllocSite,
    ClassObject,
    DictObject,
    InstanceObject,
    ModuleObject,
)
from pythonstan.analysis.pointer.kcfa.processor.heap_mapping import (
    copy_mapping_fields,
    instance_namespace_view,
)
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.compat.host_iteration import contains_object
from pythonstan.ir.ir_statements import IRClass, IRModule, IRScope
from pythonstan.world.module_exports import source_export_names, source_has_export


class _FilenameModule(IRScope):
    _source_export_names: object

    def __init__(self, filename: object) -> None:
        super().__init__("subject")
        self.filename = filename

    def get_filename(self) -> object:
        return self.filename

    @override
    def get_ast(self) -> ast.Module:
        return ast.parse("fallback_export = 1")


class _ShadowedPath:
    def __init__(self, actual: Path, shadow: Path) -> None:
        self.actual = actual
        self.class_calls = 0
        self.shadow_calls = 0

        def shadow_reader() -> str:
            self.shadow_calls += 1
            return str(shadow)

        # A host special-method lookup must ignore this ordinary attribute.
        object.__setattr__(self, "__fspath__", shadow_reader)

    def __fspath__(self) -> str:
        self.class_calls += 1
        return str(self.actual)


def test_actual_module_source_uses_the_filesystem_type_slot(tmp_path: Path) -> None:
    actual, shadow = tmp_path / "actual.py", tmp_path / "shadow.py"
    actual.write_text("actual_export = 1")
    shadow.write_text("shadow_export = 1")
    filename = _ShadowedPath(actual, shadow)
    module = _FilenameModule(filename)

    assert source_has_export(module, "actual_export")
    assert not source_has_export(module, "shadow_export")
    assert not source_has_export(module, "fallback_export")
    assert filename.class_calls == 2
    assert filename.shadow_calls == 0


def test_source_read_failure_retains_ast_fallback_and_caches_it(tmp_path: Path) -> None:
    source = tmp_path / "broken.py"
    source.write_text("def invalid(")
    module = _FilenameModule(source)
    first = source_export_names(module)
    assert source_has_export(module, "fallback_export")
    source.write_text("later_export = 1")
    assert source_export_names(module) is first
    assert not source_has_export(module, "later_export")


def test_foreign_export_cache_keeps_identity_and_uses_membership_without_iteration() -> None:
    class Cache:
        def __init__(self) -> None:
            self.queries: list[object] = []

        def __contains__(self, item: object) -> bool:
            self.queries.append(item)
            return item == "cached_export"

        def __iter__(self) -> Iterator[str]:
            raise AssertionError("Export cache membership must not enumerate the cache")

        def __bool__(self) -> bool:
            raise AssertionError("Export cache presence is checked against None")

    cache = Cache()
    module = _FilenameModule(object())
    module._source_export_names = cache
    assert source_export_names(module) is cache
    assert source_has_export(module, "cached_export")
    assert not source_has_export(module, "missing")
    assert cache.queries == ["cached_export", "missing"]


def test_getitem_only_cache_keeps_legacy_membership_and_stops_at_first_match() -> None:
    class Cache:
        def __init__(self) -> None:
            self.indices: list[int] = []

        def __getitem__(self, index: int) -> str:
            self.indices.append(index)
            return ("cached_export",)[index]

    cache = Cache()
    module = _FilenameModule(object())
    module._source_export_names = cache
    assert source_export_names(module) is cache
    assert source_has_export(module, "cached_export")
    assert cache.indices == [0]
    assert not source_has_export(module, "missing")
    assert cache.indices == [0, 0, 1]


def test_membership_ignores_an_instance_shadow_of_the_contains_slot() -> None:
    class Container:
        def __contains__(self, item: object) -> bool:
            return item == "native_slot"

    container = Container()

    def instance_shadow(item: object) -> bool:
        return True

    object.__setattr__(container, "__contains__", instance_shadow)
    assert contains_object(container, "native_slot")
    assert not contains_object(container, "shadow_only")


def test_membership_preserves_the_original_protocol_exception() -> None:
    sentinel = RuntimeError("membership sentinel")

    class Container:
        def __contains__(self, item: object) -> bool:
            raise sentinel

    with pytest.raises(RuntimeError) as stopped:
        contains_object(Container(), "name")
    assert stopped.value is sentinel


def test_actual_extra_exports_do_not_branch_on_set_truthiness() -> None:
    class FalsyExports(set[str]):
        def __bool__(self) -> bool:
            return False

    module = IRModule("subject", ast.parse("answer = 1"), filename="")
    module._source_extra_exports = FalsyExports({"dynamic_export"})
    assert PointerAnalysisState().is_module_export(module, "dynamic_export")


def _native_model() -> tuple[PointerAnalysisState, Scope, InstanceObject, DictObject]:
    context = CallStringContext((), 1)
    module = IRModule("subject", ast.parse(""))
    module_object = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
    scope = Scope.new(module_object, None, context, module, None)
    definition = ast.parse("class C: pass").body[0]
    assert isinstance(definition, ast.ClassDef)
    cls = IRClass("subject.C", definition)
    cls_object = ClassObject(context, AllocSite(cls, AllocKind.CLASS), scope, cls)
    owner = InstanceObject(context, AllocSite("owner", AllocKind.INSTANCE), cls_object)
    view = DictObject(context, AllocSite("view", AllocKind.DICT))
    return PointerAnalysisState(), scope, owner, view


def test_actual_namespace_view_accepts_the_existing_dynamic_get_service() -> None:
    state, scope, owner, view = _native_model()

    class Manager:
        def __init__(self) -> None:
            self.lookups: list[str] = []
            self.calls: list[tuple[Scope, InstanceObject]] = []

        def field_created(self, at: Scope | None, obj: AbstractObject, field: Field) -> None:
            pass

        def __getattr__(self, name: str) -> Callable[[Scope, InstanceObject], DictObject]:
            self.lookups.append(name)
            if name != "get":
                raise AttributeError(name)

            def provide(at: Scope, instance: InstanceObject) -> DictObject:
                self.calls.append((at, instance))
                return view

            return provide

    manager = Manager()
    state._instance_namespace_views = manager
    assert instance_namespace_view(state, scope, owner) is view
    assert manager.lookups == ["get"]
    assert manager.calls == [(scope, owner)]
    assert state.peek_instance_namespace_views() is manager


def test_actual_mapping_copy_accepts_the_existing_dynamic_add_service() -> None:
    state, scope, _owner, target = _native_model()
    source = DictObject(target.context, AllocSite("source", AllocKind.DICT))
    excluded = frozenset({"skip"})

    class Manager:
        def __init__(self) -> None:
            self.lookups: list[str] = []
            self.calls: list[
                tuple[Scope, AbstractObject, AbstractObject, frozenset[str], bool]
            ] = []

        def field_created(self, at: Scope | None, obj: AbstractObject, field: Field) -> None:
            pass

        def __getattr__(
            self, name: str
        ) -> Callable[[Scope, AbstractObject, AbstractObject, frozenset[str], bool], None]:
            self.lookups.append(name)
            if name != "add":
                raise AttributeError(name)

            def add(
                at: Scope,
                origin: AbstractObject,
                destination: AbstractObject,
                omitted: frozenset[str],
                indexed: bool,
            ) -> None:
                self.calls.append((at, origin, destination, omitted, indexed))

            return add

    manager = Manager()
    state._mapping_field_copies = manager
    copy_mapping_fields(state, scope, source, target, excluded, True)
    assert manager.lookups == ["add"]
    assert manager.calls == [(scope, source, target, excluded, True)]
    assert manager.calls[0][3] is excluded
    assert state.peek_mapping_field_copies() is manager
