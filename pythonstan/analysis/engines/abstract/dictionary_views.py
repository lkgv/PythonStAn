"""Live builtin dictionary views and their finite ordered iterators.

A view stores its owner, never a snapshot of its elements. Iterator keys are
captured only when iteration starts, so size changes raise at the next step.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from typing import TYPE_CHECKING, TypeGuard

from pythonstan.analysis.contracts.identity import ObjectId
from pythonstan.analysis.contracts.json_data import JsonScalar
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.state.effects import MetadataAspect, MetadataUpdate
from pythonstan.analysis.state.flow import Flow, NormalExprFlow
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.keys import FieldKey
from pythonstan.analysis.state.metadata import (
    DictionaryIteratorMeta,
    DictViewKind,
    DictViewMeta,
    Metadata,
)
from pythonstan.analysis.state.store import Frame, HeapObject, Store

if TYPE_CHECKING:
    from .transfer import Transfer


def _scalar_key(key: FieldKey) -> TypeGuard[JsonScalar]:
    """Admit exactly the historical finite builtin key representation."""
    return type(key) in {str, int, bool, float, type(None)}


class DictionaryViews:
    def __init__(self, transfer: Transfer) -> None:
        self.t = transfer
        self.d = transfer.d
        self.ops = transfer.ops

    def create(
        self,
        owner: ObjectId,
        kind: DictViewKind,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> Value:
        return self.t.alloc(
            node,
            store,
            frame,
            "dict_view",
            metadata=Metadata.from_record("dict_view", DictViewMeta(Value.ref(owner), kind)),
        )

    def length(
        self,
        view: HeapObject,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        record = view.metadata.record
        if not isinstance(record, DictViewMeta) or record.owner is None:
            raise KeyError("owner")
        return self.t.length_builtin([record.owner], (), store, frame, node)

    def iterator(
        self,
        owner: Value,
        kind: DictViewKind,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        result: list[Flow] = []
        for ref in owner.refs:
            current = store.copy()
            obj = current.heap.get(ref)
            if obj is not None and obj.metadata.lookup("summarized_keys"):
                result.extend(
                    self.t.unknown(
                        current, node, "summarized_dictionary_key_iteration", havoc=False
                    )
                )
                continue
            if (
                obj is None
                or obj.kind != "dict"
                or obj.havoc
                or not obj.singleton
                or obj.maybe_missing
                or not obj.default.is_bottom()
            ):
                result.extend(self.t.unknown(current, node, "abstract_dictionary_view_iteration"))
                continue
            metadata = Metadata.from_record(
                "iterator",
                DictionaryIteratorMeta(
                    container=Value.ref(ref),
                    dictionary_iteration=kind,
                    dictionary_keys=tuple(obj.fields),
                    index=0,
                ),
            )
            allocated = self.t.alloc(node, current, frame, "iterator", metadata=metadata)
            result.append(NormalExprFlow(StateHandle.capture(current), allocated))
        if owner.top or owner.top_sorts:
            result.extend(self.t.unknown(store, node, "unknown_dictionary_view_owner"))
        return result

    def next(
        self,
        iterator: ObjectId,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        item = store.heap[iterator]
        index = item.metadata["index"]
        kind = item.metadata["dictionary_iteration"]
        result: list[Flow] = []
        if item.metadata.lookup("dictionary_exhausted"):
            return [self.t.continuations.stop(store, frame, node)]
        for owner in item.metadata["container"].refs:
            current = store.copy()
            source = current.heap.get(owner)
            if source is not None and source.metadata.lookup("summarized_keys"):
                result.extend(
                    self.t.unknown(
                        current, node, "summarized_dictionary_key_iteration", havoc=False
                    )
                )
                continue
            if (
                source is None
                or source.havoc
                or not source.singleton
                or source.maybe_missing
                or not source.default.is_bottom()
            ):
                result.extend(self.t.unknown(current, node, "abstract_dictionary_iterator_state"))
                continue
            keys = tuple(source.fields)
            initial = item.metadata["dictionary_keys"]
            if len(keys) != len(initial):
                result.append(self.t.error(current, "RuntimeError"))
                continue
            if keys != initial:
                result.extend(self.t.unknown(current, node, "same_size_dictionary_key_replacement"))
                continue
            if index >= len(keys):
                current.heap[iterator].metadata["dictionary_exhausted"] = True
                current.record_write(MetadataUpdate(iterator, MetadataAspect.ITERATOR_STATE))
                result.append(self.t.continuations.stop(current, frame, node))
                continue
            key = keys[index]
            if not _scalar_key(key):
                result.extend(self.t.unknown(current, node, "dictionary_view_key_representation"))
                continue
            answer = Value.constant(key) if kind == "keys" else source.fields[key]
            if kind == "items":
                synthetic = ast.Constant(value=None)
                if node is not None:
                    ast.copy_location(synthetic, node)
                self.d.register_generated(
                    synthetic,
                    self.d.point(node) + "#dictionary-item:" + iterator + ":" + str(index),
                )
                answer = self.t.alloc(
                    synthetic,
                    current,
                    frame,
                    "tuple",
                    {0: Value.constant(key), 1: answer},
                    {"length": 2},
                )
            current.heap[iterator].metadata["index"] = index + 1
            current.record_write(MetadataUpdate(iterator, MetadataAspect.ITERATOR_STATE))
            result.append(NormalExprFlow(StateHandle.capture(current), value=answer))
        return result

    def contains(
        self,
        view: HeapObject,
        key: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        if view.havoc:
            return self.t.unknown(store, node, "havoc_dictionary_view_membership")
        record = view.metadata.record
        if not isinstance(record, DictViewMeta) or record.owner is None:
            raise KeyError("owner")
        if record.view_kind is None:
            raise KeyError("view_kind")
        owner = record.owner
        kind = record.view_kind
        if kind == "keys":
            return self.t.dictionaries.contains(owner, key, store, frame, node)
        result: list[Flow] = []
        for ref in owner.refs:
            source = store.heap.get(ref)
            if (
                source is None
                or source.havoc
                or source.maybe_missing
                or not source.default.is_bottom()
            ):
                result.extend(self.t.unknown(store, node, "abstract_dictionary_view_membership"))
                continue
            needles: list[tuple[Value | None, Value]] = []
            if kind == "values":
                needles = [(None, key)]
            else:
                for candidate in key.refs:
                    pair = store.heap.get(candidate)
                    if (
                        pair
                        and pair.kind == "tuple"
                        and not pair.havoc
                        and pair.metadata.lookup("length") == 2
                    ):
                        needles.append(
                            (pair.fields.get(0, pair.default), pair.fields.get(1, pair.default))
                        )
                    elif (
                        pair
                        and pair.kind == "tuple"
                        and type(pair.metadata.lookup("length")) is int
                    ):
                        result.append(
                            NormalExprFlow(
                                StateHandle.capture(store.copy()), value=Value.constant(False)
                            )
                        )
                    else:
                        result.extend(
                            self.t.unknown(store, node, "dictionary_items_equality_protocol")
                        )
                scalar = replace(key, refs=frozenset())
                if scalar.top or scalar.top_sorts:
                    result.extend(self.t.unknown(store, node, "dictionary_items_equality_protocol"))
                elif not scalar.is_bottom():
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(store.copy()), value=Value.constant(False)
                        )
                    )
            for wanted_key, wanted_value in needles:
                constants = wanted_value.exact_constants()
                keys = wanted_key.exact_constants() if wanted_key is not None else [None]
                values: list[list[JsonScalar]] = []
                unknown_values = False
                for value in source.fields.values():
                    choices = value.exact_constants()
                    if choices is None:
                        unknown_values = True
                        break
                    values.append(choices)
                if constants is None or keys is None or unknown_values:
                    result.extend(self.t.unknown(store, node, "dictionary_view_equality_protocol"))
                    continue
                truths = set()
                for target_key in keys:
                    for target_value in constants:
                        comparisons = [
                            (
                                wanted_key is None or concrete_key == target_key,
                                [v == target_value for v in choices],
                            )
                            for concrete_key, choices in zip(source.fields, values, strict=False)
                        ]
                        if any(
                            matches_key and any(matches) for matches_key, matches in comparisons
                        ):
                            truths.add(True)
                        if all(
                            not matches_key or not matches or not all(matches)
                            for matches_key, matches in comparisons
                        ):
                            truths.add(False)
                if truths:
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(store.copy()), value=Value(bools=frozenset(truths))
                        )
                    )
        return result
