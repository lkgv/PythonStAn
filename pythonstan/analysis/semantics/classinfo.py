"""Lazy ClassInfo alternatives and ordered instance/subclass matching."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.identity import CellId, ObjectId
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
class DuckClass:
    identity: ObjectId
    bases: tuple[Value, ...]


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


type NominalTarget = BuiltinType | SourceClass | DuckClass
type ClassInfo = NominalTarget | TupleClassInfo | UnknownClassInfo | InvalidClassInfo

_SCALAR_RUNTIME_KINDS = frozenset(
    {"int", "str", "bool", "float", "none", "bytes", "complex", "ellipsis", "NotImplementedType"}
)
_TYPE_RECURSION_BOUND = 48


class ClassRelations:
    def __init__(self, transfer: Transfer) -> None:
        self.t = transfer
        self._site: ast.AST | None = None

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
        if (
            obj is None
            or obj.kind != "class"
            or obj.havoc
            or "__bases__" in obj.fields
            or "__bases__" in obj.maybe_missing
        ):
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

    def _owned_duck(self, identifier: ObjectId, obj: HeapObject, store: Store) -> list[ClassInfo]:
        """Read a stable owned __bases__ tuple, never bypass a source hook.

        Class membership checks use the ordinary __bases__ attribute protocol
        for non-types. Merely recognizing the receiver's nominal class misses
        attributes stored on instances and Python function objects.
        """
        if (obj.kind == "instance" or obj.class_ids) and self._class_protocol(
            obj,
            store,
            frozenset(
                {
                    "__bases__",
                    "__getattribute__",
                    "__getattr__",
                    "__instancecheck__",
                    "__subclasscheck__",
                }
            ),
        ):
            return [UnknownClassInfo("custom_duck_class_protocol")]
        self.t.d.charge(1, "classinfo_owned_bases")
        self.t.d.reads.add((identifier, "__bases__", self.t.d.point(self._site)))
        if "__bases__" in obj.maybe_missing:
            return [UnknownClassInfo("maybe_missing_duck_bases")]
        bases = obj.fields.get("__bases__")
        if bases is None:
            return (
                [InvalidClassInfo()]
                if obj.default.is_bottom()
                else [UnknownClassInfo("unknown_duck_bases")]
            )
        return self._duck_bases(identifier, bases, store)

    def _module_duck(self, identifier: ObjectId, obj: HeapObject, store: Store) -> list[ClassInfo]:
        module = obj.metadata.lookup("module")
        if (
            module is None
            or module not in self.t.d.modules
            or module not in self.t.d.trees
            or module in store.havoc_namespaces
            or obj.class_ids
        ):
            return [UnknownClassInfo("unmodeled_module_class_protocol")]
        self.t.d.charge(1, "classinfo_module_bases")
        self.t.d.reads.add((identifier, "__bases__", self.t.d.point(self._site)))
        bases = store.effective_cell(CellId(f"global:{module}:__bases__"))
        getter = store.effective_cell(CellId(f"global:{module}:__getattr__"))
        custom_class = store.effective_cell(CellId(f"global:{module}:__class__"))
        if not custom_class.without_unbound().is_bottom() or (
            bases.unbound and not getter.without_unbound().is_bottom()
        ):
            return [UnknownClassInfo("custom_module_bases_lookup")]
        result = self._duck_bases(identifier, bases.without_unbound(), store)
        if bases.unbound:
            result.append(InvalidClassInfo())
        return result

    def _duck_bases(self, identifier: ObjectId, bases: Value, store: Store) -> list[ClassInfo]:
        result: list[ClassInfo] = []
        for facet in self.t.ops.facets(self.t.ops.normalize(bases)):
            if facet.kind in {"unknown", "unbound", "type"}:
                result.append(UnknownClassInfo("unknown_duck_bases"))
            elif facet.kind != "reference":
                result.append(InvalidClassInfo())
            else:
                for reference in facet.value.refs:
                    self.t.d.charge(1, "classinfo_duck_bases_tuple")
                    sequence = store.heap.get(reference)
                    if sequence is None or sequence.havoc:
                        result.append(UnknownClassInfo("unknown_duck_bases_tuple"))
                        continue
                    if sequence.kind != "tuple":
                        result.append(InvalidClassInfo())
                        continue
                    length = sequence.metadata.lookup("length")
                    symbolic = sequence.metadata.lookup("length_value")
                    if (
                        length is None
                        or length < 0
                        or not sequence.default.is_bottom()
                        or sequence.maybe_missing
                        or sequence.metadata.lookup("summarized_keys", ())
                        or symbolic is not None
                        and symbolic.exact_constants() != [length]
                    ):
                        result.append(UnknownClassInfo("unknown_duck_bases_shape"))
                        continue
                    items: list[Value] = []
                    for index in range(length):
                        self.t.d.charge(1, "classinfo_duck_base_item")
                        item = sequence.fields.get(index)
                        if item is None:
                            result.append(UnknownClassInfo("missing_duck_base_item"))
                            break
                        items.append(item)
                    else:
                        result.append(DuckClass(identifier, tuple(items)))
        return result

    def _classify(self, value: Value, store: Store, *, subject: bool = False) -> list[ClassInfo]:
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
                        if not obj.singleton and not subject:
                            result.append(UnknownClassInfo("summary_class_identity"))
                        elif (
                            not subject
                            and obj.class_ids
                            and self._class_protocol(
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
                            )
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
                    elif obj.kind in {"instance", "function", "staticmethod", "classmethod"} and (
                        "__bases__" in obj.fields
                        or "__bases__" in obj.maybe_missing
                        or not obj.default.is_bottom()
                    ):
                        result.extend(self._owned_duck(identifier, obj, store))
                    elif obj.kind == "module":
                        result.extend(self._module_duck(identifier, obj, store))
                    elif obj.kind == "bound_method":
                        # Bound methods delegate ordinary attributes to their
                        # function. Absence from the wrapper is not proof that
                        # a classlike __bases__ attribute is absent.
                        result.append(UnknownClassInfo("delegated_method_class_protocol"))
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

    def _nominal(self, clsid: ObjectId, target: NominalTarget, store: Store) -> bool | None:
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
        self, subject: Value, target: NominalTarget, store: Store, node: ast.AST | None
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

    def _derived_info(
        self,
        subject: BuiltinType | SourceClass | DuckClass,
        target: NominalTarget,
        store: Store,
        node: ast.AST | None,
        seen: frozenset[ObjectId],
        *,
        abstract_bases: bool,
    ) -> list[Flow]:
        self.t.d.charge(1, "classinfo_subclass_parent")
        if isinstance(subject, BuiltinType):
            matched = self._nominal(ObjectId("builtin:" + subject.name), target, store)
            return (
                [self.t.normal(store, Value.constant(matched))]
                if matched is not None
                else self._unknown(store, node, "unknown_subclass_hierarchy")
            )
        if not isinstance(target, BuiltinType):
            left = store.heap.get(subject.identity)
            right = store.heap.get(target.identity)
            if subject.identity == target.identity or left is not None and left is right:
                return (
                    [self.t.normal(store, Value.constant(True))]
                    if left is not None and left.singleton
                    else self._unknown(store, node, "summary_duck_identity")
                )
        if isinstance(subject, SourceClass):
            obj = store.heap.get(subject.identity)
            if (
                (abstract_bases or isinstance(target, DuckClass))
                and obj is not None
                and obj.class_ids
                and self._class_protocol(
                    obj, store, frozenset({"__bases__", "__getattribute__", "__getattr__"})
                )
            ):
                return self._unknown(store, node, "custom_subclass_bases")
            matched = self._nominal(subject.identity, target, store)
            return (
                [self.t.normal(store, Value.constant(matched))]
                if matched is not None
                else self._unknown(store, node, "unknown_subclass_hierarchy")
            )
        if subject.identity in seen or len(seen) >= _TYPE_RECURSION_BOUND:
            return self._unknown(store, node, "cyclic_or_deep_duck_bases")
        pending = [store]
        result: list[Flow] = []
        for base in subject.bases:
            following: list[Store] = []
            for current in pending:
                for info in self._classify(base, current, subject=True):
                    # Inner __bases__ entries are ancestry candidates, not
                    # top-level issubclass operands or ClassInfo tuples.
                    # A non-class entry is simply a nonmatch in CPython.
                    tested: list[Flow]
                    if isinstance(info, (InvalidClassInfo, TupleClassInfo)):
                        tested = [self.t.normal(current.copy(), Value.constant(False))]
                    elif isinstance(info, UnknownClassInfo):
                        tested = self._unknown(current.copy(), node, info.reason)
                    else:
                        tested = self._derived_info(
                            info,
                            target,
                            current.copy(),
                            node,
                            seen | {subject.identity},
                            abstract_bases=True,
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

    def _subclass(
        self, subject: Value, target: NominalTarget, store: Store, node: ast.AST | None
    ) -> list[Flow]:
        result: list[Flow] = []
        for info in self._classify(subject, store, subject=True):
            if isinstance(info, (InvalidClassInfo, TupleClassInfo)):
                result.append(self.t.error(store.copy(), "TypeError"))
            elif isinstance(info, UnknownClassInfo):
                result.extend(self._unknown(store.copy(), node, info.reason))
            else:
                result.extend(
                    self._derived_info(
                        info, target, store.copy(), node, frozenset(), abstract_bases=False
                    )
                )
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
            elif isinstance(alternative, (BuiltinType, SourceClass, DuckClass)):
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
        self._site = node
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
