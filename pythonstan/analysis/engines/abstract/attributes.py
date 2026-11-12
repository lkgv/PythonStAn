"""Attribute lookup, source descriptors, C3 construction and reflective names.

All target callbacks use the shared abstract Transfer and its full Store. Builtin
attribute names come from a fixed CPython model; target objects are never read.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Sequence
from dataclasses import replace
from itertools import product
from typing import TYPE_CHECKING, Literal

from pythonstan.analysis.contracts.identity import CellId, FrameId, ObjectId
from pythonstan.analysis.contracts.json_data import JsonScalar
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.semantics.names import classify_bindings
from pythonstan.analysis.state.continuation import Continuation
from pythonstan.analysis.state.effects import HeapDelete
from pythonstan.analysis.state.flow import (
    Flow,
    NormalExprFlow,
    flow_payload,
    replace_flow,
    require_payload,
)
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.keys import FieldKey
from pythonstan.frontend.private_names import source_declared_name
from pythonstan.models.builtin_attributes import BUILTIN_ATTRIBUTE_NAMES, known_builtin_attribute
from pythonstan.models.exceptions import EXCEPTION_NAMES

from .state import Frame, HeapObject, Store

if TYPE_CHECKING:
    from .driver import InterproceduralDriver
    from .transfer import Transfer


_NO_BASES: tuple[ObjectId, ...] = ()


def _class_references(obj: HeapObject, name: Literal["mro", "bases"]) -> Sequence[ObjectId]:
    references: Sequence[ObjectId] = obj.metadata.lookup(name, _NO_BASES)
    return references


class AttributeOperations:
    def __init__(self, transfer: Transfer) -> None:
        self.t: Transfer = transfer
        self.d: InterproceduralDriver = transfer.d
        self.ops: ValueOperations = transfer.ops

    def _invoke_bound(
        self,
        method: Value,
        receiver: Value,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        target = self.bound(method, receiver, store, frame, node)
        return self.t.invoke(target, args, keywords, store, frame, node)

    def _insert(self, original: ObjectId, obj: HeapObject, store: Store) -> Value:
        identity = self.d.allocate_identity(original)
        if identity != original:
            obj.singleton = False
        if identity in store.heap:
            obj = store.heap[identity].join(obj, self.ops)
            obj.singleton = False
        store.invalidate_guards(identity)
        store.heap[identity] = obj
        return Value.ref(identity)

    def bound(
        self, value: Value, receiver: Value, store: Store, frame: Frame, node: ast.AST | None
    ) -> Value:
        store.ensure_current(self.ops)
        value = self.ops.normalize(value)
        receiver = self.ops.normalize(receiver)
        scalar = replace(value, refs=frozenset())
        outputs = [] if scalar.is_bottom() else [scalar]
        for ref in value.refs:
            obj = store.heap.get(ref)
            if ref in self.d.functions:
                identity = ObjectId(
                    f"bound:{ref}:{','.join(sorted(receiver.refs))}:{self.d.point(node)}"
                )
                outputs.append(
                    self._insert(
                        identity,
                        HeapObject(
                            "bound_method",
                            metadata={"callable": Value.ref(ref), "receiver": receiver},
                        ),
                        store,
                    )
                )
            elif obj and obj.kind == "staticmethod":
                outputs.append(obj.fields["callable"])
            elif obj and obj.kind == "classmethod":
                classes = set()
                for r in receiver.refs:
                    target = store.heap.get(r)
                    if target:
                        classes |= set(target.class_ids) if target.kind != "class" else {r}
                identity = ObjectId(f"bound:{ref}:{','.join(sorted(classes))}:{self.d.point(node)}")
                outputs.append(
                    self._insert(
                        identity,
                        HeapObject(
                            "bound_method",
                            metadata={
                                "callable": obj.fields["callable"],
                                "receiver": Value(refs=frozenset(classes)),
                            },
                        ),
                        store,
                    )
                )
            else:
                outputs.append(Value.ref(ref))
        if value.top:
            outputs.append(Value.unknown())
        return self.ops.join_all(outputs)

    def construct(
        self,
        clsid: ObjectId,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        cls = store.heap[clsid]
        new = self.lookup_class(clsid, "__new__", store)
        result: list[Flow] = []
        if new.is_bottom():
            created: list[Flow] = [
                NormalExprFlow(
                    StateHandle.capture(store),
                    self.t.alloc(node, store, frame, "instance", class_ids=(clsid,)),
                )
            ]
        else:
            created = self.t.invoke(new, [Value.ref(clsid), *args], keywords, store, frame, node)
        for made in created:
            if made.kind != "normal":
                result.append(made)
                continue
            tags = self.t.exception_class_tags(clsid, made.store)
            if "builtin:BaseException" in tags:
                for ref in require_payload(made).refs:
                    if ref in made.store.heap:
                        made.store.heap[ref].metadata.update(
                            {
                                "exception_name": cls.metadata.lookup("name", "BaseException"),
                                "exception_types": tuple(tags),
                            }
                        )
            initializer = self.lookup_class(clsid, "__init__", made.store)
            applicable = any(
                clsid in made.store.heap.get(r, HeapObject("object")).class_ids
                for r in require_payload(made).refs
            )
            if initializer.is_bottom() or not applicable:
                result.append(made)
            else:
                for initialized in self._invoke_bound(
                    initializer, require_payload(made), args, keywords, made.store, frame, node
                ):
                    if initialized.kind == "normal":
                        result.append(
                            NormalExprFlow(
                                StateHandle.capture(initialized.store), require_payload(made)
                            )
                        )
                    else:
                        result.append(initialized)
        return result

    def lookup_presence(self, clsid: ObjectId, name: str, store: Store) -> tuple[Value, bool]:
        cls = store.heap.get(clsid)
        if cls is None:
            if clsid == "builtin:object" or (
                clsid.startswith("builtin:") and clsid.split(":", 1)[1] in EXCEPTION_NAMES
            ):
                return (Value.bottom(), True)
            self.d.open("unmodeled_builtin_class_lookup:" + clsid, store)
            return (Value.unknown(), True)
        if cls.havoc:
            self.d.open("havoc_class_lookup", store)
            return (Value.unknown(), True)
        result = Value.bottom()
        missing = True
        order = (clsid, *_class_references(cls, "mro"))
        seen = set()
        for identifier in order:
            if identifier in seen:
                continue
            seen.add(identifier)
            owner = store.heap.get(identifier)
            if owner is None:
                continue
            if owner.havoc:
                return (self.ops.join(result, Value.unknown()), True)
            if name in owner.fields:
                result = self.ops.join(result, owner.fields[name])
                if name not in owner.maybe_missing:
                    return (result, False)
            if not owner.default.is_bottom():
                result = self.ops.join(result, owner.default)
        return (result, missing)

    def lookup_class(
        self, clsid: ObjectId, name: str, store: Store, seen: set[ObjectId] | None = None
    ) -> Value:
        return self.lookup_presence(clsid, name, store)[0]

    def _method(self, name: str, receiver: Value, store: Store, node: ast.AST | None) -> Value:
        identity = ObjectId(
            "builtin-method:"
            + ",".join(sorted(receiver.refs))
            + ":"
            + name
            + ":"
            + self.d.point(node)
        )
        return self._insert(
            identity,
            HeapObject(
                "builtin_method", metadata={"name": name, "receiver": self.ops.normalize(receiver)}
            ),
            store,
        )

    def _descriptor_get(
        self,
        value: Value,
        receiver: Value,
        classes: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        result: list[Flow] = []
        scalar = replace(value, refs=frozenset())
        if not scalar.is_bottom():
            result.append(NormalExprFlow(StateHandle.capture(store.copy()), scalar))
        for ref in value.refs:
            obj = store.heap.get(ref)
            if obj and obj.kind == "property":
                getter = obj.fields.get("fget", Value.bottom())
                if getter.is_bottom():
                    result.append(self.t.error(store.copy(), "AttributeError"))
                else:
                    result.extend(self.t.invoke(getter, [receiver], (), store.copy(), frame, node))
            elif obj and obj.kind == "instance":
                getter = self.ops.join_all(
                    self.lookup_class(cls, "__get__", store) for cls in obj.class_ids
                )
                if getter.is_bottom():
                    result.append(NormalExprFlow(StateHandle.capture(store.copy()), Value.ref(ref)))
                else:
                    result.extend(
                        self._invoke_bound(
                            getter,
                            Value.ref(ref),
                            [receiver, classes],
                            (),
                            store.copy(),
                            frame,
                            node,
                        )
                    )
            else:
                state = store.copy()
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(state),
                        self.bound(Value.ref(ref), receiver, state, frame, node),
                    )
                )
        return result

    def _is_data(self, ref: ObjectId, store: Store) -> bool:
        descriptor = store.heap.get(ref)
        if not descriptor:
            return False
        return descriptor.kind == "property" or any(
            not self.lookup_class(cls, "__set__", store).is_bottom()
            or not self.lookup_class(cls, "__delete__", store).is_bottom()
            for cls in descriptor.class_ids
        )

    def getattr(
        self, base: Value, name: str, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        store.ensure_current(self.ops)
        base = self.ops.normalize(base)
        result: list[Flow] = []
        for ref in base.refs:
            for out in self._getattr_raw(Value.ref(ref), name, store.copy(), frame, node):
                receiver = out.store.heap.get(ref)
                if (
                    out.kind == "raise"
                    and out.exception == "AttributeError"
                    and receiver
                    and receiver.kind == "instance"
                    and not receiver.havoc
                ):
                    fallback = self.ops.join_all(
                        self.lookup_class(cls, "__getattr__", out.store)
                        for cls in receiver.class_ids
                    )
                    if not fallback.is_bottom():
                        result.extend(
                            self._invoke_bound(
                                fallback,
                                Value.ref(ref),
                                [Value.constant(name)],
                                (),
                                out.store,
                                frame,
                                node,
                            )
                        )
                        continue
                result.append(out)
        scalar = replace(base, refs=frozenset())
        if not scalar.is_bottom():
            result.extend(self._getattr_raw(scalar, name, store.copy(), frame, node))
        return result

    def _getattr_raw(
        self, base: Value, name: str, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        result: list[Flow] = []
        store.ensure_current(self.ops)
        base = self.ops.normalize(base)
        for ref in base.refs:
            obj = store.heap.get(ref)
            state = store.copy()
            if ref.startswith("builtin:"):
                kind = ref.split(":", 1)[1]
                if name == "__name__":
                    result.append(NormalExprFlow(StateHandle.capture(state), Value.constant(kind)))
                    continue
                if known_builtin_attribute(kind, name) or (
                    kind in EXCEPTION_NAMES and known_builtin_attribute("object", name)
                ):
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(state),
                            self._method(name, Value.ref(ref), state, node),
                        )
                    )
                else:
                    result.append(self.t.error(state, "AttributeError"))
                continue
            if obj is None:
                result.extend(self.t.unknown(state, node, "unknown_object_attribute:" + name))
                continue
            if obj.havoc:
                result.extend(self.t.unknown(state, node, "havoc_object_attribute:" + name))
                continue
            if obj.kind == "bound_method":
                if name == "__self__":
                    result.append(
                        NormalExprFlow(StateHandle.capture(state), obj.metadata["receiver"])
                    )
                    continue
                if name == "__func__":
                    result.append(
                        NormalExprFlow(StateHandle.capture(state), obj.metadata["callable"])
                    )
                    continue
                result.extend(self.getattr(obj.metadata["callable"], name, state, frame, node))
                continue
            if obj.kind == "property" and name in {"fget", "fset", "fdel"}:
                value = obj.fields.get(name, Value.bottom())
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(state),
                        Value.constant(None) if value.is_bottom() else value,
                    )
                )
                continue
            if obj.kind == "namespace":
                if name in {
                    "get",
                    "keys",
                    "values",
                    "items",
                    "copy",
                    "setdefault",
                    "update",
                    "clear",
                    "pop",
                    "__getitem__",
                    "__setitem__",
                    "__contains__",
                }:
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(state),
                            self._method(name, Value.ref(ref), state, node),
                        )
                    )
                else:
                    result.extend(
                        self.t.unknown(state, node, "unmodeled_namespace_attribute:" + name)
                    )
                continue
            if obj.kind == "dict_view":
                if name in {"__iter__", "__len__", "__contains__"}:
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(state),
                            self._method(name, Value.ref(ref), state, node),
                        )
                    )
                elif name in {
                    "mapping",
                    "__reversed__",
                    "__and__",
                    "__or__",
                    "__sub__",
                    "__xor__",
                } or known_builtin_attribute("object", name):
                    result.extend(
                        self.t.unknown(
                            state, node, "unmodeled_dictionary_view_attribute:" + name, havoc=False
                        )
                    )
                else:
                    result.append(self.t.error(state, "AttributeError"))
                continue
            if obj.kind == "function":
                if name == "__dict__":
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(state), self.t.namespaces.object_view(state, ref)
                        )
                    )
                    continue
                metadata_key = {
                    "__name__": "name",
                    "__qualname__": "python_qualname",
                    "__module__": "module",
                }.get(name)
                metadata_value = (
                    obj.metadata.lookup(metadata_key) if metadata_key is not None else None
                )
                if isinstance(metadata_value, str):
                    result.append(
                        NormalExprFlow(StateHandle.capture(state), Value.constant(metadata_value))
                    )
                    continue
                if name == "__annotations__":
                    from pythonstan.analysis.contracts.private_slots import (
                        function_annotations_slot,
                    )

                    slot = function_annotations_slot()
                    if slot in obj.fields:
                        result.append(NormalExprFlow(StateHandle.capture(state), obj.fields[slot]))
                        continue
                if name in {
                    "__defaults__",
                    "__kwdefaults__",
                    "__code__",
                    "__closure__",
                    "__annotations__",
                    "__name__",
                    "__qualname__",
                    "__module__",
                    "__doc__",
                    "__type_params__",
                }:
                    result.extend(
                        self.t.unknown(
                            state, node, "function_metadata_attribute:" + name, havoc=False
                        )
                    )
                    continue
            if obj.kind == "module":
                module = obj.metadata["module"]
                if module not in self.d.modules or module not in self.d.trees:
                    result.extend(
                        self.t.unknown(
                            state, node, "external_module_attribute:" + module + "." + name
                        )
                    )
                    continue
                if name == "__dict__":
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(state), self.t.namespaces.global_view(module, state)
                        )
                    )
                    continue
                self.t.namespaces._seed_module(module, state)
                value = state.effective_cell(CellId(f"global:{module}:{name}"))
                if not value.without_unbound().is_bottom():
                    result.append(
                        NormalExprFlow(StateHandle.capture(state.copy()), value.without_unbound())
                    )
                if value.unbound:
                    if name in {"__builtins__", "__loader__", "__spec__", "__cached__", "__path__"}:
                        result.extend(
                            self.t.unknown(
                                state, node, "runtime_module_metadata:" + name, havoc=False
                            )
                        )
                    result.append(self.t.error(state, "AttributeError"))
                continue
            if obj.kind in {
                "list",
                "dict",
                "tuple",
                "set",
                "str",
                "generator",
                "coroutine",
                "iterator",
                "property",
            }:
                allowed = {
                    "list": {"append", "extend", "insert", "pop", "clear", "copy", "__iter__"},
                    "dict": {
                        "pop",
                        "clear",
                        "get",
                        "setdefault",
                        "update",
                        "keys",
                        "values",
                        "items",
                        "copy",
                        "__iter__",
                    },
                    "tuple": {"__iter__"},
                    "set": {"pop", "clear", "copy", "__iter__"},
                    "generator": {"send", "throw", "close", "__next__", "__iter__"},
                    "coroutine": {"send", "throw", "close", "__await__"},
                    "iterator": {"__iter__", "__next__"},
                    "property": {"setter", "deleter", "getter"},
                }.get(obj.kind, set())
                if name in allowed:
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(state),
                            self._method(name, Value.ref(ref), state, node),
                        )
                    )
                    continue
            if obj.kind in {
                "list",
                "dict",
                "tuple",
                "set",
                "range",
                "iterator",
                "generator",
                "coroutine",
            }:
                if known_builtin_attribute(obj.kind, name):
                    result.extend(
                        self.t.unknown(
                            state,
                            node,
                            "unmodeled_builtin_attribute:" + obj.kind + "." + name,
                            havoc=False,
                        )
                    )
                else:
                    result.append(self.t.error(state, "AttributeError"))
                continue
            if obj.kind == "super":
                owners = obj.metadata["class"].refs
                receiver = obj.metadata["receiver"]
                value = Value.bottom()
                uncertain = False
                for instance in receiver.refs:
                    target = state.heap.get(instance)
                    for clsid in target.class_ids if target else ():
                        cls = state.heap.get(clsid)
                        if not cls or cls.havoc:
                            uncertain = True
                            continue
                        mro = (clsid, *_class_references(cls, "mro"))
                        for owner in owners:
                            if owner in mro:
                                for candidate_id in mro[mro.index(owner) + 1 :]:
                                    candidate = state.heap.get(candidate_id)
                                    if candidate and candidate.havoc:
                                        uncertain = True
                                        break
                                    if candidate and name in candidate.fields:
                                        value = self.ops.join(value, candidate.fields[name])
                                        if name not in candidate.maybe_missing:
                                            break
                if uncertain:
                    result.extend(self.t.unknown(state, node, "havoc_super_class_relation"))
                if not value.is_bottom():
                    result.extend(
                        self._descriptor_get(
                            value, receiver, Value(refs=frozenset(owners)), state, frame, node
                        )
                    )
                elif not uncertain:
                    result.append(self.t.error(state, "AttributeError"))
                continue
            if obj.kind == "class":
                if name == "__dict__":
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(state),
                            self.t.namespaces.object_view(state, ref, readonly=True),
                        )
                    )
                    continue
                if name in {"__mro__", "__bases__"}:
                    members = (
                        (ref, *_class_references(obj, "mro"))
                        if name == "__mro__"
                        else _class_references(obj, "bases")
                    )
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(state),
                            self.t.alloc(
                                node,
                                state,
                                frame,
                                "tuple",
                                {i: Value.ref(identifier) for i, identifier in enumerate(members)},
                                {"length": len(members)},
                            ),
                        )
                    )
                    continue
                if name in {"__name__", "__qualname__", "__module__"}:
                    class_attribute = obj.fields.get(name)
                    if class_attribute is None:
                        class_attribute = Value.constant(
                            obj.metadata.lookup(
                                {
                                    "__name__": "name",
                                    "__qualname__": "qualname",
                                    "__module__": "module",
                                }[name],
                                frame.module,
                            )
                        )
                    result.append(NormalExprFlow(StateHandle.capture(state), class_attribute))
                    continue
                value, missing = self.lookup_presence(ref, name, state)
                scalar = replace(value, refs=frozenset())
                if not scalar.is_bottom():
                    result.append(NormalExprFlow(StateHandle.capture(state.copy()), scalar))
                for descriptor_ref in value.refs:
                    descriptor = state.heap.get(descriptor_ref)
                    if descriptor and descriptor.kind == "classmethod":
                        copy = state.copy()
                        result.append(
                            NormalExprFlow(
                                StateHandle.capture(copy),
                                self.bound(
                                    Value.ref(descriptor_ref), Value.ref(ref), copy, frame, node
                                ),
                            )
                        )
                    elif descriptor and descriptor.kind == "staticmethod":
                        result.append(
                            NormalExprFlow(
                                StateHandle.capture(state.copy()), descriptor.fields["callable"]
                            )
                        )
                    elif descriptor and descriptor.kind == "instance":
                        result.extend(
                            self._descriptor_get(
                                Value.ref(descriptor_ref),
                                Value.constant(None),
                                Value.ref(ref),
                                state,
                                frame,
                                node,
                            )
                        )
                    else:
                        result.append(
                            NormalExprFlow(
                                StateHandle.capture(state.copy()), Value.ref(descriptor_ref)
                            )
                        )
                if missing:
                    if known_builtin_attribute("object", name) or name in {
                        "mro",
                        "__subclasses__",
                        "__prepare__",
                        "__call__",
                        "__instancecheck__",
                        "__subclasscheck__",
                        "__flags__",
                        "__basicsize__",
                        "__itemsize__",
                        "__dictoffset__",
                        "__weakrefoffset__",
                        "__text_signature__",
                    }:
                        result.extend(
                            self.t.unknown(
                                state, node, "unmodeled_type_attribute:" + name, havoc=False
                            )
                        )
                    else:
                        result.append(self.t.error(state.copy(), "AttributeError"))
                continue
            instance_classes: tuple[ObjectId | None, ...] = tuple(obj.class_ids) or (None,)
            for instance_class in instance_classes:
                current = state.copy()
                classes_value = Value.ref(instance_class) if instance_class else Value.bottom()
                if obj.kind == "instance" and instance_class:
                    custom, custom_missing = self.lookup_presence(
                        instance_class, "__getattribute__", current
                    )
                    if not custom.is_bottom():
                        result.extend(
                            self._invoke_bound(
                                custom,
                                Value.ref(ref),
                                [Value.constant(name)],
                                (),
                                current.copy(),
                                frame,
                                node,
                            )
                        )
                    if not custom_missing:
                        continue
                descriptor_value, descriptor_missing = (
                    self.lookup_presence(instance_class, name, current)
                    if instance_class
                    else (Value.bottom(), True)
                )
                if descriptor_value.top:
                    result.extend(self.t.unknown(current, node, "unknown_class_descriptor"))
                    continue
                data_refs = frozenset(
                    desc for desc in descriptor_value.refs if self._is_data(desc, current)
                )
                if data_refs:
                    result.extend(
                        self._descriptor_get(
                            Value(refs=data_refs),
                            Value.ref(ref),
                            classes_value,
                            current.copy(),
                            frame,
                            node,
                        )
                    )
                remainder = replace(descriptor_value, refs=descriptor_value.refs - data_refs)
                if not descriptor_missing and remainder.is_bottom():
                    continue
                if (
                    name == "__class__"
                    and instance_class
                    and descriptor_missing
                    and remainder.is_bottom()
                ):
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(current.copy()), Value.ref(instance_class)
                        )
                    )
                    continue
                value, missing = current.read_slot(ref, name, self.ops)
                self.d.reads.add((ref, name, self.d.point(node)))
                if not value.is_bottom():
                    result.append(NormalExprFlow(StateHandle.capture(current.copy()), value))
                if not missing and (not value.is_bottom()):
                    continue
                if (
                    name == "__dict__"
                    and obj.kind in {"instance", "object"}
                    and remainder.is_bottom()
                ):
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(current),
                            self.t.namespaces.object_view(current, ref),
                        )
                    )
                    continue
                if not remainder.is_bottom():
                    result.extend(
                        self._descriptor_get(
                            remainder, Value.ref(ref), classes_value, current.copy(), frame, node
                        )
                    )
                if descriptor_missing:
                    if obj.kind == "function" and (
                        known_builtin_attribute("object", name)
                        or name
                        in {
                            "__get__",
                            "__annotations__",
                            "__defaults__",
                            "__kwdefaults__",
                            "__globals__",
                            "__dict__",
                            "__code__",
                            "__closure__",
                            "__qualname__",
                            "__name__",
                            "__module__",
                            "__doc__",
                            "__type_params__",
                        }
                    ):
                        result.extend(
                            self.t.unknown(
                                current, node, "unmodeled_function_attribute:" + name, havoc=False
                            )
                        )
                        continue
                    if obj.kind == "instance" and known_builtin_attribute("object", name):
                        result.extend(
                            self.t.unknown(
                                current, node, "unmodeled_object_slot:" + name, havoc=False
                            )
                        )
                        continue
                    result.append(self.t.error(current, "AttributeError"))
        scalar = replace(base, refs=frozenset())
        if scalar.top:
            result.extend(self.t.unknown(store, node, "unknown_attribute:" + name))
            return result
        if scalar.is_bottom():
            return result
        constants = scalar.exact_constants()
        if name in {"real", "imag", "numerator", "denominator"}:
            values = []
            unsupported = False
            if constants is not None:
                for constant in constants:
                    if isinstance(constant, (int, float)):
                        if name == "real":
                            attribute_value = constant.real
                        elif name == "imag":
                            attribute_value = constant.imag
                        elif isinstance(constant, int):
                            attribute_value = (
                                constant.numerator if name == "numerator" else constant.denominator
                            )
                        else:
                            unsupported = True
                            continue
                        values.append(Value.constant(attribute_value))
                    else:
                        unsupported = True
            else:
                if (
                    not scalar.ints.is_bottom
                    or scalar.bools
                    or "int" in scalar.top_sorts
                    or ("bool" in scalar.top_sorts)
                ):
                    if name in {"real", "numerator"}:
                        integer = Value(ints=scalar.ints, top_sorts=scalar.top_sorts & {"int"})
                        booleans = scalar.bools or (
                            frozenset((False, True)) if "bool" in scalar.top_sorts else frozenset()
                        )
                        values.append(
                            self.ops.join(
                                integer,
                                self.ops.join_all(Value.constant(int(flag)) for flag in booleans),
                            )
                        )
                    else:
                        values.append(Value.constant(0 if name == "imag" else 1))
                if scalar.floats or "float" in scalar.top_sorts:
                    if name == "real":
                        values.append(
                            Value(floats=scalar.floats, top_sorts=scalar.top_sorts & {"float"})
                        )
                    elif name == "imag":
                        values.append(Value.constant(0.0))
                    else:
                        unsupported = True
                if (
                    not scalar.strings.is_bottom()
                    or scalar.none
                    or scalar.top_sorts - {"int", "bool", "float"}
                ):
                    unsupported = True
            if values:
                result.append(
                    NormalExprFlow(StateHandle.capture(store.copy()), self.ops.join_all(values))
                )
            if unsupported:
                result.append(self.t.error(store.copy(), "AttributeError"))
            return result
        string_part = replace(
            scalar,
            ints=self.ops.integer.bottom(),
            bools=frozenset(),
            floats=frozenset(),
            none=False,
            top_sorts=scalar.top_sorts & {"str"},
        )
        if name in {
            "upper",
            "lower",
            "startswith",
            "endswith",
            "strip",
            "split",
            "join",
            "format",
            "replace",
        } and (not string_part.is_bottom()):
            state = store.copy()
            result.append(
                NormalExprFlow(
                    StateHandle.capture(state), self._method(name, string_part, state, node)
                )
            )
            scalar = replace(
                scalar, strings=self.ops.string.bottom(), top_sorts=scalar.top_sorts - {"str"}
            )
        if scalar.is_bottom():
            return result
        sorts = set(scalar.top_sorts)
        if not scalar.ints.is_bottom:
            sorts.add("int")
        if not scalar.strings.is_bottom():
            sorts.add("str")
        if scalar.bools:
            sorts.add("bool")
        if scalar.floats:
            sorts.add("float")
        if scalar.none:
            sorts.add("NoneType")
        for sort in sorts:
            if sort not in BUILTIN_ATTRIBUTE_NAMES and sort != "NoneType":
                result.extend(
                    self.t.unknown(
                        store,
                        node,
                        "unmodeled_scalar_attribute_sort:" + sort + "." + name,
                        havoc=sort not in {"bytes", "complex", "ellipsis", "EllipsisType"},
                    )
                )
            elif (
                known_builtin_attribute(sort, name)
                or sort == "NoneType"
                and known_builtin_attribute("object", name)
            ):
                result.extend(
                    self.t.unknown(
                        store, node, "unmodeled_scalar_attribute:" + sort + "." + name, havoc=False
                    )
                )
            else:
                result.append(self.t.error(store.copy(), "AttributeError"))
        return result

    def setattr(
        self, base: Value, name: str, value: Value, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        store.ensure_current(self.ops)
        base = self.ops.normalize(base)
        value = self.ops.normalize(value)
        result: list[Flow] = []
        if base.top or base.top_sorts:
            result.extend(self.t.unknown(store, node, "unknown_attribute_write:" + name))
        for ref in base.refs:
            state = store.copy()
            obj = state.heap.get(ref)
            if obj is None:
                result.extend(self.t.unknown(state, node, "unknown_attribute_receiver"))
                continue
            if obj.havoc:
                result.extend(self.t.unknown(state, node, "havoc_attribute_mutation"))
                continue
            if obj.kind in {
                "list",
                "dict",
                "tuple",
                "set",
                "range",
                "iterator",
                "generator",
                "coroutine",
            }:
                result.append(self.t.error(state, "AttributeError"))
                continue
            if obj.kind == "namespace":
                result.extend(self.t.unknown(state, node, "unmodeled_namespace_attribute_write"))
                continue
            if obj.kind == "function" and name in {
                "__defaults__",
                "__kwdefaults__",
                "__code__",
                "__closure__",
                "__dict__",
                "__annotations__",
                "__name__",
                "__qualname__",
                "__module__",
                "__doc__",
                "__type_params__",
            }:
                result.extend(self.t.unknown(state, node, "function_metadata_mutation:" + name))
                continue
            if obj.kind == "module" and name == "__dict__":
                result.append(self.t.error(state, "AttributeError"))
                continue
            if obj.kind == "instance" and name == "__dict__":
                result.extend(self.t.unknown(state, node, "instance_dictionary_replacement"))
                continue
            if obj.kind == "class" and name in {"__mro__", "__dict__"}:
                result.append(self.t.error(state, "AttributeError"))
                continue
            if (
                obj.kind == "class"
                and name == "__bases__"
                or (obj.kind == "instance" and name == "__class__")
            ):
                result.extend(self.t.unknown(state, node, "dynamic_class_relation:" + name))
                continue
            if obj.kind == "instance":
                custom_rows = [
                    self.lookup_presence(cls, "__setattr__", state) for cls in obj.class_ids
                ]
                custom = self.ops.join_all(row[0] for row in custom_rows)
                if not custom.is_bottom():
                    result.extend(
                        self._invoke_bound(
                            custom,
                            Value.ref(ref),
                            [Value.constant(name), value],
                            (),
                            state.copy(),
                            frame,
                            node,
                        )
                    )
                    if custom_rows and not any(row[1] for row in custom_rows):
                        continue
            descriptor_rows = [self.lookup_presence(cls, name, state) for cls in obj.class_ids]
            descriptors = self.ops.join_all(row[0] for row in descriptor_rows)
            descriptor_missing = not descriptor_rows or any(row[1] for row in descriptor_rows)
            handled = set()
            for descriptor_ref in descriptors.refs:
                descriptor = state.heap.get(descriptor_ref)
                if descriptor and descriptor.kind == "property":
                    handled.add(descriptor_ref)
                    setter = descriptor.fields.get("fset", Value.bottom())
                    if setter.is_bottom():
                        result.append(self.t.error(state.copy(), "AttributeError"))
                    else:
                        result.extend(
                            self.t.invoke(
                                setter, [Value.ref(ref), value], (), state.copy(), frame, node
                            )
                        )
                elif descriptor:
                    setter = self.ops.join_all(
                        self.lookup_class(cls, "__set__", state) for cls in descriptor.class_ids
                    )
                    if not setter.is_bottom():
                        handled.add(descriptor_ref)
                        result.extend(
                            self._invoke_bound(
                                setter,
                                Value.ref(descriptor_ref),
                                [Value.ref(ref), value],
                                (),
                                state.copy(),
                                frame,
                                node,
                            )
                        )
            remainder = replace(descriptors, refs=descriptors.refs - handled)
            if descriptor_missing or not remainder.is_bottom():
                if obj.kind == "module":
                    state.write_cell(CellId("global:" + obj.metadata["module"] + ":" + name), value)
                else:
                    state.write_slot(ref, name, value, self.ops, weak=len(base.refs) > 1)
                if obj.kind != "module":
                    self.d.publish_oracle_field(
                        ref, name, state.heap[ref].fields[name], "ai_attribute_write"
                    )
                self.d.writes.add((ref, name, self.d.point(node)))
                result.append(self.t.normal(state))
        if not base.refs and (not base.top) and (not base.top_sorts) and (not base.is_bottom()):
            result.append(self.t.error(store.copy(), "AttributeError"))
        return result

    def c3(self, bases: Sequence[ObjectId], store: Store) -> tuple[ObjectId, ...]:
        if len(set(bases)) != len(bases):
            raise ValueError("duplicate base")
        sequences: list[list[ObjectId]] = []
        for base in bases:
            obj = store.heap.get(base)
            if obj and obj.havoc:
                raise LookupError("havoc class hierarchy")
            sequences.append([base, *_class_references(obj, "mro")] if obj else [base])
        sequences.append(list(bases))
        result: list[ObjectId] = []
        while any(sequences):
            sequences = [sequence for sequence in sequences if sequence]
            candidate = next(
                (
                    sequence[0]
                    for sequence in sequences
                    if not any((sequence[0] in other[1:] for other in sequences))
                ),
                None,
            )
            if candidate is None:
                raise ValueError("inconsistent MRO")
            result.append(candidate)
            for sequence in sequences:
                if sequence and sequence[0] == candidate:
                    sequence.pop(0)
        return tuple(result)

    def _needs_annotations(self, node: ast.ClassDef) -> bool:

        def visit(current: ast.AST) -> bool:
            if isinstance(current, ast.AnnAssign):
                return True
            if isinstance(
                current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                return False
            return any(visit(child) for child in ast.iter_child_nodes(current))

        return any(visit(statement) for statement in node.body)

    def _python_qualname(self, node: ast.ClassDef, frame: Frame) -> str:
        # Lexical qualnames are compiled from scope nesting, not from a mutable
        # parent class's runtime __qualname__ field.
        found: str | None = None

        def walk(current: ast.AST, prefix: tuple[str, ...]) -> None:
            nonlocal found
            if current is node:
                found = ".".join((*prefix, (source_declared_name(node) or node.name)))
                return
            children: Iterable[ast.AST]
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                children = current.body
                child_prefix = (
                    *prefix,
                    (source_declared_name(current) or current.name),
                    "<locals>",
                )
            elif isinstance(current, ast.ClassDef):
                children = current.body
                child_prefix = (*prefix, (source_declared_name(current) or current.name))
            else:
                children = ast.iter_child_nodes(current)
                child_prefix = prefix
            for child in children:
                if found is None:
                    walk(child, child_prefix)

        walk(self.d.trees[frame.module], ())
        return (
            found
            if found is not None
            else frame.qualname.removeprefix(frame.module + ".") + "." + node.name
        )

    def define_class(self, node: ast.ClassDef, store: Store, frame: Frame) -> list[Flow]:
        if node.keywords:
            result: list[Flow] = []
            for out in self.t.unknown(store, node, "class_keyword_or_metaclass_protocol"):
                if out.kind == "normal":
                    self.t.write_name(node.name, Value.unknown(), out.store, frame, node)
                result.append(out)
            return result
        states, errors = self.t.eval_nodes((*node.decorator_list, *node.bases), store, frame)
        result = list(errors)
        for initial, values in states:
            decorators = values[: len(node.decorator_list)]
            basevalues = values[len(node.decorator_list) :]
            choices: list[tuple[ObjectId, ...]] = []
            unknown = False
            for base in basevalues:
                initial.ensure_current(self.ops)
                base = self.ops.normalize(base)
                candidates = tuple(
                    ref
                    for ref in base.refs
                    if ref in initial.heap
                    and initial.heap[ref].kind == "class"
                    or ref == "builtin:object"
                    or (ref.startswith("builtin:") and ref.split(":", 1)[1] in EXCEPTION_NAMES)
                )
                scalar = replace(base, refs=frozenset())
                if not scalar.is_bottom() or len(candidates) != len(base.refs):
                    if (
                        base.top
                        or base.top_sorts
                        or any(
                            ref.startswith("builtin:") for ref in base.refs if ref not in candidates
                        )
                    ):
                        unknown = True
                    else:
                        result.append(self.t.error(initial.copy(), "TypeError"))
                if not candidates:
                    choices = []
                    break
                choices.append(candidates)
            if unknown:
                for out in self.t.unknown(initial, node, "dynamic_class_bases"):
                    if out.kind == "normal":
                        self.t.write_name(node.name, Value.unknown(), out.store, frame, node)
                    result.append(out)
            if basevalues and len(choices) != len(basevalues):
                continue
            combinations = product(*choices) if choices else [(ObjectId("builtin:object"),)]
            for bases in combinations:
                self.d.charge(1, "class_base_alternatives")
                state = initial.copy()
                try:
                    mro = self.c3(bases, state)
                except ValueError:
                    result.append(self.t.error(state, "TypeError"))
                    continue
                except LookupError:
                    result.extend(self.t.unknown(state, node, "havoc_class_bases"))
                    continue
                original = ObjectId(f"class:{self.d.point(node)}@" + "|".join(frame.context))
                identifier = self.d.allocate_identity(original)
                local, global_names, nonlocal_names, _ = classify_bindings(node)
                closure = dict(frame.closure)
                if not frame.is_module:
                    closure.update((name, frame.cell(name)) for name in frame.locals)
                qualname = self._python_qualname(node, frame)
                local = local | frozenset(("__module__", "__qualname__"))
                if self._needs_annotations(node):
                    local |= frozenset(("__annotations__",))
                classframe = Frame(
                    frame.module,
                    frame.qualname + "." + node.name,
                    frame.context,
                    FrameId(identifier),
                    local,
                    tuple(closure.items()),
                    global_names,
                    nonlocal_names,
                    is_class=True,
                )
                prefix = "local:" + identifier + ":"
                state.write_cell(CellId(prefix + "__module__"), Value.constant(frame.module))
                state.write_cell(CellId(prefix + "__qualname__"), Value.constant(qualname))
                doc = ast.get_docstring(node, clean=False)
                if doc is not None:
                    state.write_cell(CellId(prefix + "__doc__"), Value.constant(doc))
                if self._needs_annotations(node):
                    state.write_cell(
                        CellId(prefix + "__annotations__"),
                        self.t.alloc(node, state, frame, "dict", {}, {"length": 0}),
                    )
                for out in self.t.block(node.body, [self.t.normal(state)], classframe):
                    if out.kind != "normal":
                        result.append(out)
                        continue
                    fields: dict[FieldKey, Value] = {
                        cell[len(prefix) :]: value.without_unbound()
                        for cell, value in out.store.cells.items()
                        if cell.startswith(prefix) and (not value.without_unbound().is_bottom())
                    }
                    namespace = out.store.heap.get(ObjectId("namespace:locals:" + identifier))
                    if namespace:
                        fields.update(namespace.fields)
                    if "__doc__" not in fields:
                        fields["__doc__"] = Value.constant(None)
                    created = HeapObject(
                        "class",
                        fields,
                        metadata={
                            "name": (source_declared_name(node) or node.name),
                            "module": frame.module,
                            "qualname": qualname,
                            "bases": tuple(bases),
                            "mro": mro,
                        },
                    )
                    if any(out.store.heap.get(base) and out.store.heap[base].havoc for base in mro):
                        created.havoc = True
                    if identifier != original:
                        created.singleton = False
                    if namespace and (namespace.havoc or not namespace.default.is_bottom()):
                        created.havoc = True
                    if identifier in out.store.heap:
                        created = out.store.heap[identifier].join(created, self.ops)
                        created.singleton = False
                    out.store.invalidate_guards(identifier)
                    out.store.heap[identifier] = created
                    pending: list[Flow] = [
                        NormalExprFlow(StateHandle.capture(out.store), Value.ref(identifier))
                    ]
                    for name, value in fields.items():
                        for descriptor_ref in value.refs:
                            descriptor = out.store.heap.get(descriptor_ref)
                            hook = (
                                self.ops.join_all(
                                    self.lookup_class(cls, "__set_name__", out.store)
                                    for cls in descriptor.class_ids
                                )
                                if descriptor
                                else Value.bottom()
                            )
                            if hook.is_bottom():
                                continue
                            following: list[Flow] = []
                            for current in pending:
                                if current.kind != "normal":
                                    following.append(current)
                                    continue
                                for called in self._invoke_bound(
                                    hook,
                                    Value.ref(descriptor_ref),
                                    [Value.ref(identifier), Value.constant(name)],
                                    (),
                                    current.store.copy(),
                                    frame,
                                    node,
                                ):
                                    following.append(
                                        replace_flow(
                                            called,
                                            value=(
                                                Value.ref(identifier)
                                                if called.kind == "normal"
                                                else flow_payload(called)
                                            ),
                                            # Preserve the original hook constructor's reset.
                                            continuation=Continuation(),
                                            cleanups=Continuation(),
                                            resume_target=None,
                                        )
                                    )
                            pending = following
                    hook = Value.bottom()
                    for ancestor in mro:
                        owner = out.store.heap.get(ancestor)
                        if owner and "__init_subclass__" in owner.fields:
                            hook = self.ops.join(hook, owner.fields["__init_subclass__"])
                            if "__init_subclass__" not in owner.maybe_missing:
                                break
                    if not hook.is_bottom():
                        following = []
                        for current in pending:
                            if current.kind != "normal":
                                following.append(current)
                                continue
                            scalar_hook = replace(hook, refs=frozenset())
                            calls: list[Flow] = []
                            if not scalar_hook.is_bottom():
                                calls.extend(
                                    self.t.invoke(
                                        scalar_hook,
                                        [Value.ref(identifier)],
                                        (),
                                        current.store.copy(),
                                        frame,
                                        node,
                                    )
                                )
                            for hookref in hook.refs:
                                target = current.store.heap.get(hookref)
                                callable_value = (
                                    target.fields["callable"]
                                    if target and target.kind in {"classmethod", "staticmethod"}
                                    else Value.ref(hookref)
                                )
                                arguments = (
                                    []
                                    if target and target.kind == "staticmethod"
                                    else [Value.ref(identifier)]
                                )
                                calls.extend(
                                    self.t.invoke(
                                        callable_value,
                                        arguments,
                                        (),
                                        current.store.copy(),
                                        frame,
                                        node,
                                    )
                                )
                            following.extend(
                                called
                                if called.kind != "normal"
                                else NormalExprFlow(
                                    StateHandle.capture(called.store), Value.ref(identifier)
                                )
                                for called in calls
                            )
                        pending = following
                    for decorator in reversed(decorators):
                        pending = [
                            called
                            for current in pending
                            for called in (
                                self.t.invoke(
                                    decorator,
                                    [require_payload(current)],
                                    (),
                                    current.store,
                                    frame,
                                    node,
                                )
                                if current.kind == "normal"
                                else [current]
                            )
                        ]
                    for current in pending:
                        if current.kind == "normal":
                            self.t.write_name(
                                node.name, require_payload(current), current.store, frame, node
                            )
                            result.append(self.t.normal(current.store))
                        else:
                            result.append(current)
        return result

    def delattr(
        self, base: Value, name: str, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        store.ensure_current(self.ops)
        base = self.ops.normalize(base)
        result: list[Flow] = []
        if base.top or base.top_sorts:
            result.extend(self.t.unknown(store, node, "unknown_attribute_delete:" + name))
        for ref in base.refs:
            state = store.copy()
            obj = state.heap.get(ref)
            if obj is None:
                result.extend(self.t.unknown(state, node, "unknown_attribute_delete_receiver"))
                continue
            if obj.havoc:
                result.extend(self.t.unknown(state, node, "havoc_attribute_mutation"))
                continue
            if obj.kind in {
                "list",
                "dict",
                "tuple",
                "set",
                "range",
                "iterator",
                "generator",
                "coroutine",
            }:
                result.append(self.t.error(state, "AttributeError"))
                continue
            if obj.kind == "function" and name in {
                "__defaults__",
                "__kwdefaults__",
                "__annotations__",
                "__dict__",
                "__code__",
                "__closure__",
                "__name__",
                "__qualname__",
                "__module__",
                "__doc__",
                "__type_params__",
            }:
                result.extend(self.t.unknown(state, node, "function_metadata_delete:" + name))
                continue
            if obj.kind == "class" and name in {"__mro__", "__bases__", "__dict__"}:
                result.append(self.t.error(state, "AttributeError"))
                continue
            if obj.kind == "instance":
                hook_rows = [
                    self.lookup_presence(cls, "__delattr__", state) for cls in obj.class_ids
                ]
                hook = self.ops.join_all(row[0] for row in hook_rows)
                if not hook.is_bottom():
                    result.extend(
                        self._invoke_bound(
                            hook,
                            Value.ref(ref),
                            [Value.constant(name)],
                            (),
                            state.copy(),
                            frame,
                            node,
                        )
                    )
                    if hook_rows and not any(row[1] for row in hook_rows):
                        continue
            descriptor_rows = [self.lookup_presence(cls, name, state) for cls in obj.class_ids]
            descriptors = self.ops.join_all(row[0] for row in descriptor_rows)
            descriptor_missing = not descriptor_rows or any(row[1] for row in descriptor_rows)
            handled = set()
            for descriptor_ref in descriptors.refs:
                descriptor = state.heap.get(descriptor_ref)
                if descriptor and descriptor.kind == "property":
                    handled.add(descriptor_ref)
                    method = descriptor.fields.get("fdel", Value.bottom())
                    if method.is_bottom():
                        result.append(self.t.error(state.copy(), "AttributeError"))
                    else:
                        result.extend(
                            self.t.invoke(method, [Value.ref(ref)], (), state.copy(), frame, node)
                        )
                elif descriptor:
                    method = self.ops.join_all(
                        self.lookup_class(cls, "__delete__", state) for cls in descriptor.class_ids
                    )
                    if not method.is_bottom():
                        handled.add(descriptor_ref)
                        result.extend(
                            self._invoke_bound(
                                method,
                                Value.ref(descriptor_ref),
                                [Value.ref(ref)],
                                (),
                                state.copy(),
                                frame,
                                node,
                            )
                        )
            if (
                not descriptor_missing
                and replace(descriptors, refs=descriptors.refs - handled).is_bottom()
            ):
                continue
            if obj.kind == "module":
                cell = CellId("global:" + obj.metadata["module"] + ":" + name)
                value = state.effective_cell(cell)
                if value.unbound:
                    result.append(self.t.error(state.copy(), "AttributeError"))
                if not value.without_unbound().is_bottom():
                    state.write_cell(cell, Value(unbound=True))
                    result.append(self.t.normal(state))
            else:
                value, missing = state.read_slot(ref, name, self.ops)
                if missing:
                    result.append(self.t.error(state.copy(), "AttributeError"))
                if not value.is_bottom():
                    obj.fields.pop(name, None)
                    obj.maybe_missing.add(name)
                    state.writes.add(HeapDelete(ref, name))
                    result.append(self.t.normal(state))
        return result

    def reflective(
        self,
        name: str,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        if keywords:
            return [self.t.error(store, "TypeError")]
        allowed = {"getattr": (2, 3), "hasattr": (2, 2), "setattr": (3, 3), "delattr": (2, 2)}
        lower, upper = allowed[name]
        if not lower <= len(args) <= upper:
            return [self.t.error(store, "TypeError")]
        names: Sequence[JsonScalar] | None = args[1].exact_constants()
        result: list[Flow] = []
        residual = names is None
        if names is None:
            candidates: set[str] = set()
            for ref in args[0].refs:
                obj = store.heap.get(ref)
                if not obj:
                    continue
                candidates.update(key for key in obj.fields if type(key) is str)
                if obj.kind == "module":
                    prefix = "global:" + obj.metadata["module"] + ":"
                    candidates.update(
                        cell[len(prefix) :] for cell in store.cells if cell.startswith(prefix)
                    )
                for clsid in obj.class_ids:
                    cls = store.heap.get(clsid)
                    if not cls:
                        continue
                    for ownerid in (clsid, *_class_references(cls, "mro")):
                        owner = store.heap.get(ownerid)
                        if owner:
                            candidates.update(key for key in owner.fields if type(key) is str)
            cap = max(0, self.d.abstract_integer("attribute_name_limit", 256))
            names = [
                candidate
                for candidate in sorted(candidates)
                if args[1].top or args[1].top_sorts or args[1].strings.may_equal(candidate)
            ][:cap]
            self.d.increment_stat("reflective_known_names", len(names))
            scalar_other = replace(
                args[1],
                strings=self.ops.string.bottom(),
                refs=frozenset(),
                top=False,
                top_sorts=args[1].top_sorts - {"str"},
            )
            if not scalar_other.is_bottom() or args[1].refs:
                result.append(self.t.error(store.copy(), "TypeError"))
        for attribute in names:
            if type(attribute) is not str:
                result.append(self.t.error(store.copy(), "TypeError"))
                continue
            if name == "setattr":
                result.extend(self.setattr(args[0], attribute, args[2], store.copy(), frame, node))
                continue
            if name == "delattr":
                result.extend(self.delattr(args[0], attribute, store.copy(), frame, node))
                continue
            for out in self.getattr(args[0], attribute, store.copy(), frame, node):
                if name == "hasattr":
                    if out.kind == "normal":
                        result.append(
                            NormalExprFlow(StateHandle.capture(out.store), Value.constant(True))
                        )
                    elif out.exception == "AttributeError":
                        result.append(
                            NormalExprFlow(StateHandle.capture(out.store), Value.constant(False))
                        )
                    else:
                        result.append(out)
                elif out.exception == "AttributeError" and len(args) == 3:
                    result.append(NormalExprFlow(StateHandle.capture(out.store), args[2]))
                else:
                    result.append(out)
        if residual:
            self.d.increment_stat("reflective_residuals")
            for out in self.t.unknown(store, node, "reflective_name_residual:" + name):
                if out.kind == "normal":
                    if name in {"setattr", "delattr"}:
                        result.append(self.t.normal(out.store))
                    elif name == "hasattr":
                        result.append(
                            NormalExprFlow(
                                StateHandle.capture(out.store),
                                Value(bools=frozenset((False, True))),
                            )
                        )
                    else:
                        result.append(out)
                else:
                    result.append(out)
        return result
