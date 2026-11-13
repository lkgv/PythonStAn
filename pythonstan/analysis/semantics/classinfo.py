"""Lazy ClassInfo alternatives and ordered instance/subclass matching."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.identity import ObjectId
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.state.flow import Flow, replace_flow, require_payload
from pythonstan.analysis.state.store import Frame, HeapObject, Store
from pythonstan.models.typeclasses import (
    BUILTIN_TYPE_NAMES,
    builtin_type_ancestors,
    canonical_builtin_type,
)

if TYPE_CHECKING:
    from pythonstan.analysis.engines.abstract.transfer import Transfer


@dataclass(frozen=True)
class BuiltinType:
    name: str


@dataclass(frozen=True)
class SourceClass:
    identity: ObjectId


@dataclass(frozen=True)
class TupleClassInfo:
    identity: ObjectId
    length: int


@dataclass(frozen=True)
class UnknownClassInfo:
    reason: str


@dataclass(frozen=True)
class InvalidClassInfo:
    pass


type ClassInfo = BuiltinType | SourceClass | TupleClassInfo | UnknownClassInfo | InvalidClassInfo

_SCALAR_RUNTIME_KINDS = frozenset(
    {"int", "str", "bool", "float", "none", "bytes", "complex", "ellipsis", "NotImplementedType"}
)
_TYPE_RECURSION_BOUND = 48


class ClassRelations:
    def __init__(self, transfer: Transfer) -> None:
        self.t = transfer

    def _unknown(self, store: Store, node: ast.AST | None, reason: str) -> list[Flow]:
        return [
            replace_flow(out, value=Value(bools=frozenset((False, True))))
            if out.kind == "normal"
            else out
            for out in self.t.unknown(store, node, "classinfo:" + reason)
        ]

    def _ancestors(
        self, identifier: ObjectId, store: Store, seen: frozenset[ObjectId] = frozenset()
    ) -> frozenset[ObjectId] | None:
        self.t.d.charge(1, "classinfo_nominal_parent")
        if identifier in seen or len(seen) >= _TYPE_RECURSION_BOUND:
            return None
        if identifier.startswith("builtin:"):
            name = identifier.split(":", 1)[1]
            if name not in BUILTIN_TYPE_NAMES:
                return None
            return frozenset(ObjectId("builtin:" + item) for item in builtin_type_ancestors(name))
        obj = store.heap.get(identifier)
        if obj is None or obj.kind != "class" or obj.havoc:
            return None
        result = {identifier, ObjectId("builtin:object")}
        for base in obj.metadata.lookup("bases", ()):
            parents = self._ancestors(base, store, seen | {identifier})
            if parents is None:
                return None
            result.update(parents)
        return frozenset(result)

    def _class_protocol(self, obj: HeapObject, store: Store, names: frozenset[str]) -> bool:
        """Recognize unsupported source protocols before a negative nominal claim."""
        if obj.havoc or not obj.class_ids:
            return True
        for clsid in obj.class_ids:
            ancestors = self._ancestors(clsid, store)
            if ancestors is None:
                return True
            for ancestor in ancestors:
                if ancestor.startswith("builtin:"):
                    continue
                cls = store.heap.get(ancestor)
                if cls is None or cls.havoc or not cls.default.is_bottom():
                    return True
                if any(name in cls.fields or name in cls.maybe_missing for name in names):
                    return True
        return False

    def _classify(self, value: Value, store: Store) -> list[ClassInfo]:
        result: list[ClassInfo] = []
        for facet in self.t.ops.facets(self.t.ops.normalize(value)):
            self.t.d.charge(1, "classinfo_alternative")
            if facet.kind in {"unknown", "unbound", "type", "types.UnionType"}:
                result.append(UnknownClassInfo("unknown_type_expression"))
            elif facet.kind in _SCALAR_RUNTIME_KINDS:
                result.append(InvalidClassInfo())
            elif facet.kind != "reference":
                result.append(UnknownClassInfo("unmodeled_type_expression"))
            else:
                for identifier in facet.value.refs:
                    self.t.d.charge(1, "classinfo_reference")
                    if identifier.startswith("builtin:"):
                        name = identifier.split(":", 1)[1]
                        result.append(
                            BuiltinType(canonical_builtin_type(name))
                            if name in BUILTIN_TYPE_NAMES
                            else InvalidClassInfo()
                        )
                        continue
                    obj = store.heap.get(identifier)
                    if obj is None or obj.havoc:
                        result.append(UnknownClassInfo("unavailable_type_object"))
                    elif obj.kind == "class":
                        if not obj.singleton:
                            result.append(UnknownClassInfo("summary_class_identity"))
                        elif obj.class_ids and self._class_protocol(
                            obj,
                            store,
                            frozenset(
                                {
                                    "__instancecheck__",
                                    "__subclasscheck__",
                                    "__getattribute__",
                                    "__getattr__",
                                }
                            ),
                        ):
                            result.append(UnknownClassInfo("custom_metaclass_protocol"))
                        else:
                            result.append(SourceClass(identifier))
                    elif obj.kind == "tuple":
                        length = obj.metadata.lookup("length")
                        result.append(
                            TupleClassInfo(identifier, length)
                            if length is not None and length >= 0
                            else UnknownClassInfo("unknown_tuple_shape")
                        )
                    elif obj.kind == "instance" and self._class_protocol(
                        obj,
                        store,
                        frozenset(
                            {
                                "__instancecheck__",
                                "__subclasscheck__",
                                "__bases__",
                                "__getattribute__",
                                "__getattr__",
                            }
                        ),
                    ):
                        result.append(UnknownClassInfo("custom_classinfo_protocol"))
                    elif obj.kind in {
                        "instance",
                        "list",
                        "dict",
                        "set",
                        "range",
                        "function",
                        "bound_method",
                        "builtin_method",
                        "module",
                        "generator",
                        "iterator",
                        "property",
                        "staticmethod",
                        "classmethod",
                        "super",
                        "dict_keys",
                        "dict_values",
                        "dict_items",
                    }:
                        result.append(InvalidClassInfo())
                    else:
                        result.append(UnknownClassInfo("unmodeled_classinfo_object"))
        return result

    def _nominal(
        self, clsid: ObjectId, target: BuiltinType | SourceClass, store: Store
    ) -> bool | None:
        parents = self._ancestors(clsid, store)
        if parents is None:
            return None
        expected = (
            ObjectId("builtin:" + target.name)
            if isinstance(target, BuiltinType)
            else target.identity
        )
        return expected in parents

    def _instance(
        self, subject: Value, target: BuiltinType | SourceClass, store: Store, node: ast.AST | None
    ) -> list[Flow]:
        if isinstance(target, BuiltinType) and target.name == "object":
            return [self.t.normal(store, Value.constant(True))]
        truths: set[bool] = set()
        uncertain = False
        for facet in self.t.ops.facets(subject):
            if facet.kind in {"unknown", "unbound"}:
                uncertain = True
            elif facet.kind in _SCALAR_RUNTIME_KINDS:
                truths.add(
                    isinstance(target, BuiltinType)
                    and target.name in builtin_type_ancestors(facet.kind)
                )
            elif facet.kind != "reference":
                uncertain = True
            else:
                for identifier in facet.value.refs:
                    if identifier.startswith("builtin:"):
                        actual = (
                            "type"
                            if identifier.split(":", 1)[1] in BUILTIN_TYPE_NAMES
                            else "builtin_function_or_method"
                        )
                        truths.add(
                            isinstance(target, BuiltinType)
                            and target.name in builtin_type_ancestors(actual)
                        )
                        continue
                    obj = store.heap.get(identifier)
                    if obj is None or obj.havoc:
                        uncertain = True
                        continue
                    if obj.class_ids:
                        for clsid in obj.class_ids:
                            matched = self._nominal(clsid, target, store)
                            if matched is True:
                                truths.add(True)
                            elif matched is None or self._class_protocol(
                                obj,
                                store,
                                frozenset({"__class__", "__getattribute__", "__getattr__"}),
                            ):
                                uncertain = True
                            else:
                                truths.add(False)
                    elif obj.kind == "instance":
                        uncertain = True
                    elif obj.kind == "exception":
                        name = obj.metadata.lookup("exception_name")
                        if name is None or name not in BUILTIN_TYPE_NAMES:
                            uncertain = True
                        else:
                            truths.add(
                                isinstance(target, BuiltinType)
                                and target.name in builtin_type_ancestors(name)
                            )
                    elif obj.kind == "class":
                        truths.add(isinstance(target, BuiltinType) and target.name == "type")
                    elif obj.kind in {
                        "list",
                        "tuple",
                        "dict",
                        "set",
                        "range",
                        "property",
                        "staticmethod",
                        "classmethod",
                        "super",
                    }:
                        truths.add(
                            isinstance(target, BuiltinType)
                            and target.name in builtin_type_ancestors(obj.kind)
                        )
                    elif obj.kind in {
                        "function",
                        "bound_method",
                        "builtin_method",
                        "module",
                        "dict_keys",
                        "dict_values",
                        "dict_items",
                    }:
                        truths.add(False)
                    else:
                        uncertain = True
        result: list[Flow] = []
        if truths:
            result.append(self.t.normal(store.copy(), Value(bools=frozenset(truths))))
        if uncertain:
            result.extend(self._unknown(store.copy(), node, "instance_class_protocol"))
        return result

    def _subclass(
        self, subject: Value, target: BuiltinType | SourceClass, store: Store, node: ast.AST | None
    ) -> list[Flow]:
        result: list[Flow] = []
        for facet in self.t.ops.facets(subject):
            if facet.kind in {"unknown", "unbound", "type"}:
                result.extend(self._unknown(store.copy(), node, "unknown_subclass_argument"))
            elif facet.kind in _SCALAR_RUNTIME_KINDS:
                result.append(self.t.error(store.copy(), "TypeError"))
            elif facet.kind != "reference":
                result.extend(self._unknown(store.copy(), node, "unmodeled_subclass_argument"))
            else:
                for identifier in facet.value.refs:
                    obj = store.heap.get(identifier)
                    known_builtin = (
                        identifier.startswith("builtin:")
                        and identifier.split(":", 1)[1] in BUILTIN_TYPE_NAMES
                    )
                    if known_builtin or obj is not None and obj.kind == "class" and not obj.havoc:
                        matched = self._nominal(identifier, target, store)
                        result.extend(
                            [self.t.normal(store.copy(), Value.constant(matched))]
                            if matched is not None
                            else self._unknown(store.copy(), node, "unknown_subclass_hierarchy")
                        )
                    elif (
                        obj is None
                        or obj.havoc
                        or obj.kind == "instance"
                        and self._class_protocol(
                            obj, store, frozenset({"__bases__", "__getattribute__", "__getattr__"})
                        )
                    ):
                        result.extend(self._unknown(store.copy(), node, "custom_subclass_argument"))
                    else:
                        result.append(self.t.error(store.copy(), "TypeError"))
        return result

    def _evaluate(
        self,
        subject: Value,
        classinfo: Value,
        store: Store,
        node: ast.AST | None,
        *,
        subclass: bool,
        seen: frozenset[ObjectId] = frozenset(),
    ) -> list[Flow]:
        self.t.sync(store)
        result: list[Flow] = []
        for alternative in self._classify(classinfo, store):
            if isinstance(alternative, InvalidClassInfo):
                result.append(self.t.error(store.copy(), "TypeError"))
            elif isinstance(alternative, UnknownClassInfo):
                result.extend(self._unknown(store.copy(), node, alternative.reason))
            elif isinstance(alternative, (BuiltinType, SourceClass)):
                result.extend(
                    self._subclass(subject, alternative, store.copy(), node)
                    if subclass
                    else self._instance(subject, alternative, store.copy(), node)
                )
            elif alternative.identity in seen or len(seen) >= _TYPE_RECURSION_BOUND:
                result.extend(self._unknown(store.copy(), node, "cyclic_or_deep_classinfo_tuple"))
            else:
                pending = [store.copy()]
                for index in range(alternative.length):
                    following: list[Store] = []
                    for current in pending:
                        self.t.d.charge(1, "classinfo_tuple_item")
                        obj = current.heap.get(alternative.identity)
                        if (
                            obj is None
                            or obj.havoc
                            or index in obj.maybe_missing
                            or index not in obj.fields
                        ):
                            tested = self._unknown(current, node, "unknown_classinfo_tuple_item")
                        else:
                            tested = self._evaluate(
                                subject,
                                obj.fields[index],
                                current,
                                node,
                                subclass=subclass,
                                seen=seen | {alternative.identity},
                            )
                        for out in tested:
                            if out.kind != "normal":
                                result.append(out)
                                continue
                            truths = require_payload(out).bools
                            if True in truths:
                                result.append(self.t.normal(out.store.copy(), Value.constant(True)))
                            if False in truths:
                                following.append(out.store.copy())
                    pending = following
                    if not pending:
                        break
                result.extend(self.t.normal(current, Value.constant(False)) for current in pending)
        return result

    def check(
        self,
        subject: Value,
        classinfo: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        *,
        subclass: bool,
    ) -> list[Flow]:
        result: list[Flow] = []
        for facet in self.t.ops.facets(self.t.ops.normalize(subject)):
            subjects = (
                [Value.ref(identifier) for identifier in facet.value.refs]
                if facet.kind == "reference"
                else [facet.value]
            )
            for value in subjects:
                result.extend(
                    self._evaluate(value, classinfo, store.copy(), node, subclass=subclass)
                )
        return result
