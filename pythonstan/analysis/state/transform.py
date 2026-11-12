"""Detached semantic graph transformations, independent of composition engines.

Values and source AST are immutable. Mutable stores/metadata are copied with one
memo so aliases and cycles in suspended continuations remain aliases and cycles.
Literal target string keys are never interpreted as host object references.
"""

from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Mapping

from pythonstan.analysis.contracts.identity import CellId
from pythonstan.analysis.domains.reabstract import (
    IdentitySubstitution,
    ReferenceKey,
    resolve_mapping,
)
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.state.effects import (
    CellWrite,
    Havoc,
    HeapDefaultWrite,
    HeapDelete,
    HeapWrite,
    MetadataUpdate,
    remap_effect,
)
from pythonstan.analysis.state.metadata import (
    BindingsField, BooleanField, ComponentField, ContinuationField, ExceptionField,
    FrameField, IntegerField, KeySequenceField, KeysField, LiteralGraphField, Metadata,
    ReferenceField, ReferencesField, StatementsField, StoreField, TextField, TextSequenceField,
    ValueField, ValueSequenceField,
)
from pythonstan.analysis.state.guards import MembershipGuard

_ADDRESS_METADATA = frozenset(
    {
        "function",
        "owner",
        "object",
        "objid",
        "class_id",
        "receiver_id",
        "iterator_id",
        "namespace_owner",
    }
)


def semantic_payload_equal(left, right, seen=None):
    if isinstance(left, Value) or isinstance(right, Value):
        return type(left) is type(right) and left == right
    if left is right:
        return True
    if type(left) is not type(right):
        return False
    seen = set() if seen is None else seen
    pair = id(left), id(right)
    if pair in seen:
        return True
    seen.add(pair)
    if isinstance(left, Metadata) and isinstance(right, Metadata):
        left_fields = left.transformation_entries()
        right_fields = right.transformation_entries()
        return left.kind == right.kind and left_fields.keys() == right_fields.keys() and all(
            type(left_fields[key]) is type(right_fields[key])
            and semantic_payload_equal(left_fields[key].value, right_fields[key].value, seen)
            for key in left_fields
        )
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            semantic_payload_equal(left[k], right[k], seen) for k in left
        )
    if isinstance(left, (tuple, list)):
        return len(left) == len(right) and all(
            semantic_payload_equal(a, b, seen) for a, b in zip(left, right)
        )
    if isinstance(left, ast.AST):
        return ast.dump(left, include_attributes=True) == ast.dump(right, include_attributes=True)
    if is_dataclass(left):
        return all(
            semantic_payload_equal(getattr(left, f.name), getattr(right, f.name), seen)
            for f in fields(left)
            if f.name != "identity_epoch"
        )
    return left == right


def payload_signature(value, seen=None):
    """Hashable continuation identity; never hashes mutable Store directly."""
    seen = {} if seen is None else seen
    if isinstance(value, Value):
        return ("value", value)
    if value is None or isinstance(value, (str, int, bool, bytes, Enum)):
        return (type(value).__name__, value)
    if isinstance(value, float):
        import struct

        return ("float", struct.pack("!d", value))
    if isinstance(value, ast.AST):
        return ("ast", ast.dump(value, include_attributes=True))
    if id(value) in seen:
        return ("cycle", seen[id(value)])
    seen[id(value)] = len(seen)
    if isinstance(value, Metadata):
        return (
            "metadata", value.kind,
            tuple(
                (key, type(field).__name__, payload_signature(field.value, seen))
                for key, field in value.transformation_entries().items()
            ),
        )
    if isinstance(value, dict):
        return (
            "dict",
            tuple(
                (payload_signature(k, seen), payload_signature(v, seen))
                for k, v in sorted(value.items(), key=lambda pair: repr(pair[0]))
            ),
        )
    if isinstance(value, (tuple, list)):
        return (type(value).__name__, tuple(payload_signature(x, seen) for x in value))
    if isinstance(value, (set, frozenset)):
        return (
            type(value).__name__,
            tuple(sorted((payload_signature(x, seen) for x in value), key=repr)),
        )
    if is_dataclass(value):
        return (
            type(value).__name__,
            tuple(
                (f.name, payload_signature(getattr(value, f.name), seen))
                for f in fields(value)
                if f.name != "identity_epoch"
            ),
        )
    raise TypeError("Unrepresentable semantic payload: " + type(value).__name__)


class PayloadTransformer:
    def __init__(self, mapping=None, ops=None, scalar=None, epoch=None, memo=None, charge=None):
        if isinstance(mapping, IdentitySubstitution):
            self.mapping = mapping.object_aliases
            self.cell_mapping = mapping.cell_aliases
        else:
            # Compatibility projection for the historical mixed-map component API.
            # Canonical transactions pass IdentitySubstitution with separate maps.
            self.mapping = {} if mapping is None else mapping
            self.cell_mapping = {CellId(old): CellId(new) for old, new in self.mapping.items()}
        self.ops = ops
        self.scalar = scalar
        self.epoch = epoch
        self.memo = {} if memo is None else memo
        self.work = 0
        self.charge = charge
        self._dict_roles = {}
        self._dict_active = set()

    def address(self, identifier):
        return resolve_mapping(identifier, self.mapping)

    def cell_address(self, identifier):
        return resolve_mapping(identifier, self.cell_mapping)

    def key(self, key):
        if isinstance(key, Value):
            return self.visit(key)
        if isinstance(key, ReferenceKey):
            return ReferenceKey(self.address(key.identifier))
        if isinstance(key, tuple):
            return tuple(self.key(x) for x in key)
        # Plain strings, enum labels and numeric dictionary keys are literals.
        return key

    def value_map(self, data, key_map, collisions=None):
        return self.mapped_dict(
            data, "cells" if key_map == self.cell_address else "values", collisions
        )

    def metadata(self, data):
        if isinstance(data, Metadata):
            return self.visit(data)
        return self.mapped_dict(data, "metadata")

    def metadata_field(self, field: ComponentField) -> ComponentField:
        """Reference roles come from the validated record, never key-name guesses."""
        if isinstance(field, ReferenceField):
            return ReferenceField(self.address(field.value))
        if isinstance(field, ReferencesField):
            values = tuple(self.address(item) for item in field.value)
            if isinstance(field.value, list):
                result = self.visit(field.value)
                result[:] = values
            else:
                result = values
                self.memo[id(field.value)] = result
            return ReferencesField(result)
        if isinstance(field, BindingsField):
            bindings = tuple((name, self.cell_address(cell)) for name, cell in field.value)
            if isinstance(field.value, list):
                result = self.visit(field.value)
                result[:] = bindings
            else:
                result = bindings
                self.memo[id(field.value)] = result
            return BindingsField(result)
        # These wrapper records are projections created by transformation_entries,
        # not source graph nodes. Never memoize their temporary Python identities:
        # a later projection can reuse the same id for a different field class.
        match field:
            case IntegerField(value):
                return IntegerField(value)
            case BooleanField(value):
                return BooleanField(value)
            case TextField(value):
                return TextField(value)
            case ValueField(value):
                return ValueField(self.visit(value))
            case FrameField(value):
                return FrameField(self.visit(value))
            case StoreField(value):
                return StoreField(self.visit(value))
            case ContinuationField(value):
                return ContinuationField(self.visit(value))
            case StatementsField(value):
                return StatementsField(self.visit(value))
            case ExceptionField(value):
                return ExceptionField(self.visit(value))
            case KeysField(value):
                return KeysField(self.visit(value))
            case KeySequenceField(value):
                return KeySequenceField(self.visit(value))
            case ValueSequenceField(value):
                return ValueSequenceField(self.visit(value))
            case TextSequenceField(value):
                return TextSequenceField(self.visit(value))
            case LiteralGraphField(value):
                return LiteralGraphField(self.visit(value))
            case _:
                raise TypeError("Unknown typed metadata role")

    def mapped_dict(self, data, role="plain", collisions=None):
        """Upgrade an earlier literal-map alias in place when its schema arrives."""
        identity = id(data)
        if role != "plain":
            self._dict_roles[identity] = role
        role = self._dict_roles.get(identity, role)
        result = self.memo.setdefault(identity, {})
        if identity in self._dict_active:
            return result
        result.clear()
        self._dict_active.add(identity)
        started_role = role
        try:
            for key, value in data.items():
                mapped = self.visit(value)
                role = self._dict_roles.get(identity, role)
                mapped_key = (
                    self.cell_address(key)
                    if role == "cells"
                    else self.address(key)
                    if role == "heap"
                    else self.key(key)
                )
                if role == "metadata":
                    if key in _ADDRESS_METADATA and isinstance(value, str):
                        mapped = self.address(value)
                    elif key in {"mro", "bases", "class_ids"} and isinstance(
                        value, (list, tuple, set, frozenset)
                    ):
                        mapped = type(value)(
                            self.address(item) if isinstance(item, str) else self.visit(item)
                            for item in value
                        )
                    elif key == "closure" and isinstance(value, (list, tuple)):
                        mapped = type(value)(
                            (name, self.cell_address(cell)) for name, cell in value
                        )
                if role == "heap" and mapped_key != key:
                    mapped.singleton = False
                if mapped_key in result:
                    if collisions is not None:
                        collisions.add(mapped_key)
                    previous = result[mapped_key]
                    if previous is not mapped:
                        if self.ops is None:
                            raise ValueError("Key collision requires domain operations")
                        if isinstance(previous, Value) and isinstance(mapped, Value):
                            mapped = self.ops.join(previous, mapped)
                        elif role == "heap":
                            mapped = previous.join(mapped, self.ops)
                            mapped.singleton = False
                        else:
                            raise ValueError("Unrepresentable non-Value key collision")
                result[mapped_key] = mapped
        finally:
            self._dict_active.remove(identity)
        # A root Store reached through a cycle can register the heap/cell role
        # while a generic alias is still being copied. Rebuild the same memo
        # object after that walk so no old address keys survive beside aliases.
        if self._dict_roles.get(identity, started_role) != started_role:
            return self.mapped_dict(data, self._dict_roles[identity], collisions)
        return result

    def visit(self, value):
        from .store import Frame, FunctionInfo, HeapObject, Store

        self.work += 1
        if self.charge is not None:
            self.charge(1)
        if isinstance(value, ReferenceKey):
            return ReferenceKey(self.address(value.identifier))
        if isinstance(value, MembershipGuard):
            return MembershipGuard(self.address(value.owner), self.key(value.key), value.positive)
        if isinstance(
            value, (CellWrite, HeapWrite, HeapDelete, HeapDefaultWrite, MetadataUpdate, Havoc)
        ):
            return remap_effect(value, self.mapping, self.cell_mapping)
        if isinstance(value, Value):
            result = (
                self.ops.remap(value, self.mapping)
                if self.ops is not None and self.mapping
                else value
            )
            return self.scalar(result) if self.scalar is not None else result
        if value is None or isinstance(value, (str, int, float, bool, bytes, Enum, ast.AST)):
            return value
        if id(value) in self.memo:
            return self.memo[id(value)]
        if isinstance(value, Metadata):
            result = value.new_shell()
            self.memo[id(value)] = result
            result.replace_entries({
                key: self.metadata_field(field)
                for key, field in value.transformation_entries().items()
            })
            return result
        if isinstance(value, Store):
            result = object.__new__(Store)
            self.memo[id(value)] = result
            result.cells = self.value_map(value.cells, self.cell_address)
            result.heap = self.mapped_dict(value.heap, "heap")
            result.initialized = self.visit(value.initialized)
            result.writes = self.memo.setdefault(id(value.writes), set())
            result.writes.clear()
            result.writes.update(
                remap_effect(effect, self.mapping, self.cell_mapping) for effect in value.writes
            )
            result.events = self.visit(value.events)
            result.obligations = value.obligations
            result.havoc_namespaces = value.havoc_namespaces
            result.havoc_imports = value.havoc_imports
            result.guards = {} if self.mapping else self.visit(getattr(value, "guards", {}))
            result.identity_epoch = (
                getattr(value, "identity_epoch", 0) if self.epoch is None else self.epoch
            )
            return result
        if isinstance(value, HeapObject):
            result = object.__new__(HeapObject)
            self.memo[id(value)] = result
            collisions = set()
            result.kind = value.kind
            result.fields = self.value_map(value.fields, self.key, collisions)
            result.default = self.visit(value.default)
            result.maybe_missing = self.visit(value.maybe_missing)
            result.singleton = value.singleton
            result.class_ids = frozenset(self.address(k) for k in value.class_ids)
            result.metadata = self.metadata(value.metadata)
            result.havoc = value.havoc
            if collisions:
                # One abstract key can now stand for multiple concrete entries.
                # Keep useful field values and owner cardinality, but do not
                # turn the quotient field count into an exact iteration size.
                result.metadata["summarized_keys"] = frozenset(
                    (*result.metadata.get("summarized_keys", ()), *collisions)
                )
                result.metadata.pop("length", None)
                result.metadata.pop("length_value", None)
            return result
        if isinstance(value, Frame):
            result = object.__new__(Frame)
            self.memo[id(value)] = result
            for f in fields(value):
                item = getattr(value, f.name)
                if f.name == "closure":
                    item = tuple((name, self.cell_address(cell)) for name, cell in item)
                else:
                    item = self.visit(item)
                object.__setattr__(result, f.name, item)
            return result
        if isinstance(value, FunctionInfo):
            result = object.__new__(FunctionInfo)
            self.memo[id(value)] = result
            for f in fields(value):
                item = getattr(value, f.name)
                if f.name == "identifier":
                    item = self.address(item)
                elif f.name == "closure":
                    item = tuple((name, self.cell_address(cell)) for name, cell in item)
                else:
                    item = self.visit(item)
                setattr(result, f.name, item)
            return result
        if isinstance(value, dict):
            return self.mapped_dict(value)
        if isinstance(value, list):
            result = []
            self.memo[id(value)] = result
            result.extend(self.visit(item) for item in value)
            return result
        if isinstance(value, tuple):
            items = [self.visit(item) for item in value]
            # A tuple reached again through a mutable cycle may already have
            # been installed by the recursive visit, as in copy.deepcopy.
            if id(value) in self.memo:
                return self.memo[id(value)]
            result = tuple(items)
            self.memo[id(value)] = result
            return result
        if isinstance(value, set):
            result = set()
            self.memo[id(value)] = result
            result.update(self.visit(item) for item in value)
            return result
        if isinstance(value, frozenset):
            result = frozenset(self.visit(item) for item in value)
            self.memo[id(value)] = result
            return result
        if is_dataclass(value):
            result = object.__new__(type(value))
            self.memo[id(value)] = result
            for f in fields(value):
                object.__setattr__(result, f.name, self.visit(getattr(value, f.name)))
            return result
        raise TypeError("Unrepresentable semantic payload: " + type(value).__name__)


def detached_payload(value, memo=None):
    return PayloadTransformer(memo=memo).visit(value)


def remap_payload(value, mapping, ops, *, epoch=None, memo=None):
    return PayloadTransformer(mapping, ops, epoch=epoch, memo=memo).visit(value)


def remap_store(store, mapping, ops, *, epoch=None, memo=None):
    return remap_payload(store, mapping, ops, epoch=epoch, memo=memo)


def equivalent(left, right):
    return semantic_payload_equal(left, right)
