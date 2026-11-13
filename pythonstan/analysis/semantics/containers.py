"""Effectful constructor builders; no target Python operation executes in host."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.state.flow import Flow, require_payload
from pythonstan.analysis.state.keys import FieldKey
from pythonstan.analysis.state.store import Frame, Store

if TYPE_CHECKING:
    from pythonstan.analysis.engines.abstract.transfer import Transfer


@dataclass(frozen=True)
class _Builder:
    store: Store
    fields: dict[FieldKey, Value] = field(default_factory=dict)
    default: Value = field(default_factory=Value.bottom)
    missing: frozenset[FieldKey] = frozenset()
    exact: bool = True


@dataclass(frozen=True)
class _Pending:
    builder: _Builder
    iterator: Value


@dataclass(frozen=True)
class ContainerBuilder:
    """A path-local internal builder, never a synthetic target-language call."""

    kind: str
    state: _Builder

    @property
    def store(self) -> Store:
        return self.state.store


class ContainerConstruction:
    def __init__(self, transfer: Transfer) -> None:
        self.t = transfer
        self.ops = transfer.ops

    def start_builder(self, kind: str, store: Store) -> ContainerBuilder:
        if kind not in {"list", "set", "dict"}:
            raise ValueError("Unsupported internal container builder")
        return ContainerBuilder(kind, _Builder(store))

    def rebind_builder(self, builder: ContainerBuilder, store: Store) -> ContainerBuilder:
        return replace(builder, state=replace(builder.state, store=store))

    def append_builder(
        self,
        builder: ContainerBuilder,
        value: Value,
        frame: Frame,
        node: ast.AST,
        *,
        key: Value | None = None,
    ) -> tuple[list[ContainerBuilder], list[Flow]]:
        if builder.kind == "list":
            fields = dict(builder.state.fields)
            fields[len(fields)] = value
            return [replace(builder, state=replace(builder.state, fields=fields))], []
        inserted, errors = self._insert(
            builder.state,
            value if key is None else key,
            value,
            frame,
            node,
            overwrite=builder.kind == "dict",
        )
        return [replace(builder, state=state) for state in inserted], errors

    def residual_builder(
        self, builder: ContainerBuilder, node: ast.AST, reason: str
    ) -> tuple[list[ContainerBuilder], list[Flow]]:
        states, errors = self._residual(builder.state, node, reason)
        return [replace(builder, state=state) for state in states], errors

    def finish_builder(self, builder: ContainerBuilder, frame: Frame, node: ast.AST) -> Flow:
        self.t.sync(builder.store)
        fields = {key: self.ops.normalize(value) for key, value in builder.state.fields.items()}
        if builder.kind == "set":
            fields = dict(enumerate(fields.values()))
        allocated = self.t.alloc(
            node,
            builder.store,
            frame,
            builder.kind,
            fields,
            {"length": len(fields)} if builder.state.exact else {},
        )
        for reference in allocated.refs:
            obj = builder.store.heap[reference]
            obj.default = self.ops.join(obj.default, builder.state.default)
            obj.maybe_missing |= builder.state.missing
        if not builder.state.exact:
            allocated = self.ops.join(allocated, Value.unknown())
        return self.t.normal(builder.store, allocated)

    def _insert(
        self,
        builder: _Builder,
        key: Value,
        value: Value,
        frame: Frame,
        node: ast.AST | None,
        *,
        overwrite: bool = True,
    ) -> tuple[list[_Builder], list[Flow]]:
        cases, errors = self.t.namespaces.key_cases(key, builder.store, frame, node)
        result: list[_Builder] = []
        for current, constants in cases:
            if constants is None:
                result.append(
                    replace(
                        builder,
                        store=current,
                        fields={
                            name: self.ops.join(old, value) for name, old in builder.fields.items()
                        },
                        default=self.ops.join(builder.default, value),
                        exact=False,
                    )
                )
            else:
                for constant in constants:
                    fields = dict(builder.fields)
                    if overwrite or constant not in fields:
                        fields[constant] = value
                    result.append(
                        replace(
                            builder,
                            store=current.copy(),
                            fields=fields,
                            missing=builder.missing - {constant},
                        )
                    )
        return result, errors

    def _residual(
        self, builder: _Builder, node: ast.AST | None, reason: str
    ) -> tuple[list[_Builder], list[Flow]]:
        result: list[_Builder] = []
        errors: list[Flow] = []
        for opaque in self.t.unknown(builder.store, node, reason):
            if opaque.kind == "normal":
                result.append(
                    replace(
                        builder,
                        store=opaque.store,
                        fields={key: Value.unknown() for key in builder.fields},
                        default=Value.unknown(),
                        exact=False,
                    )
                )
            else:
                errors.append(opaque)
        return result, errors

    def _consume(
        self, value: Value, builder: _Builder, frame: Frame, node: ast.AST | None, *, pairs: bool
    ) -> tuple[list[_Builder], list[Flow]]:
        completed: list[_Builder] = []
        errors: list[Flow] = []
        pending: list[_Pending] = []
        engine = self.t.continuations
        for acquired in engine.get_iterator(
            value,
            builder.store,
            frame,
            node,
            role="mapping-outer" if pairs else "set-outer",
            publish=False,
        ):
            if acquired.kind == "normal":
                pending.append(
                    _Pending(replace(builder, store=acquired.store), require_payload(acquired))
                )
            else:
                errors.append(acquired)
        for index in range(max(1, self.t.execution.integer("max_stmt_visits", 100))):
            following: list[_Pending] = []
            advance_site = engine.protocol_item_site(
                node, index, role="mapping:next" if pairs else "set:next"
            )
            for item in pending:
                self.t.d.charge(1, "iteration_work")
                for advanced in engine.next_iterator(
                    item.iterator, item.builder.store, frame, advance_site, publish=False
                ):
                    if engine.is_stop(advanced):
                        completed.append(replace(item.builder, store=advanced.store))
                    elif advanced.kind != "normal":
                        errors.append(advanced)
                    elif pairs:
                        pair_site = engine.protocol_item_site(node, index, role="mapping-pair")
                        collections, failed = engine.collect_values(
                            require_payload(advanced),
                            advanced.store,
                            frame,
                            pair_site,
                            role="mapping-pair",
                            hint_mode="iterator",
                            publish_advances=False,
                        )
                        errors.extend(failed)
                        for collection in collections:
                            current = replace(item.builder, store=collection.store)
                            if not collection.complete:
                                residual, failed = self._residual(
                                    current, node, "mapping_pair_shape"
                                )
                                completed.extend(residual)
                                errors.extend(failed)
                                errors.append(self.t.error(collection.store.copy(), "ValueError"))
                            elif len(collection.values) != 2:
                                errors.append(self.t.error(collection.store, "ValueError"))
                            else:
                                inserted, failed = self._insert(
                                    current, collection.values[0], collection.values[1], frame, node
                                )
                                errors.extend(failed)
                                following.extend(
                                    _Pending(candidate, item.iterator) for candidate in inserted
                                )
                    else:
                        inserted, failed = self._insert(
                            replace(item.builder, store=advanced.store),
                            require_payload(advanced),
                            require_payload(advanced),
                            frame,
                            node,
                            overwrite=False,
                        )
                        errors.extend(failed)
                        following.extend(
                            _Pending(candidate, item.iterator) for candidate in inserted
                        )
            pending = following
            if not pending:
                return completed, errors
            if len(pending) > self.t.execution.max_paths:
                break
        for item in pending:
            residual, failed = self._residual(item.builder, node, "container_iteration_budget")
            completed.extend(residual)
            errors.extend(failed)
        return completed, errors

    def _mapping_keys(
        self, source: Value, keys: Value, builder: _Builder, frame: Frame, node: ast.AST | None
    ) -> tuple[list[_Builder], list[Flow]]:
        collections, errors = self.t.continuations.collect_values(
            keys,
            builder.store,
            frame,
            node,
            role="mapping-keys",
            hint_mode="iterator",
            publish_advances=False,
        )
        completed: list[_Builder] = []
        for collection in collections:
            if not collection.complete:
                residual, failed = self._residual(
                    replace(builder, store=collection.store), node, "mapping_keys_shape"
                )
                completed.extend(residual)
                errors.extend(failed)
                continue
            pending = [replace(builder, store=collection.store)]
            for position, key in enumerate(collection.values):
                following: list[_Builder] = []
                site = self.t.continuations.protocol_item_site(
                    node, position, role="mapping-getitem"
                )
                for current in pending:
                    for fetched in self.t.getitem(source, key, current.store, frame, site):
                        if fetched.kind != "normal":
                            errors.append(fetched)
                        else:
                            inserted, failed = self._insert(
                                replace(current, store=fetched.store),
                                key,
                                require_payload(fetched),
                                frame,
                                site,
                            )
                            following.extend(inserted)
                            errors.extend(failed)
                pending = following
            completed.extend(pending)
        return completed, errors

    def _mapping(
        self, value: Value, builder: _Builder, frame: Frame, node: ast.AST | None
    ) -> tuple[list[_Builder], list[Flow]]:
        result: list[_Builder] = []
        errors: list[Flow] = []
        for facet in self.ops.facets(self.ops.normalize(value)):
            if facet.kind != "reference":
                copied, failed = self._consume(
                    facet.value,
                    replace(builder, store=builder.store.copy()),
                    frame,
                    node,
                    pairs=True,
                )
                result.extend(copied)
                errors.extend(failed)
                continue
            for reference in facet.value.refs:
                source = Value.ref(reference)
                obj = builder.store.heap.get(reference)
                if obj is not None and obj.kind == "dict" and not obj.havoc:
                    fields = {**builder.fields, **obj.fields}
                    fast_copy = replace(
                        builder,
                        store=builder.store.copy(),
                        fields=fields,
                        default=self.ops.join(builder.default, obj.default),
                        missing=builder.missing | obj.maybe_missing,
                        exact=builder.exact
                        and obj.default.is_bottom()
                        and not obj.metadata.lookup("summarized_keys"),
                    )
                    result.append(fast_copy)
                    continue
                for keys in self.t.getattr(source, "keys", builder.store.copy(), frame, node):
                    if keys.kind == "raise" and keys.exception == "AttributeError":
                        copied, failed = self._consume(
                            source, replace(builder, store=keys.store), frame, node, pairs=True
                        )
                        result.extend(copied)
                        errors.extend(failed)
                    elif keys.kind != "normal":
                        errors.append(keys)
                    else:
                        for called in self.t.invoke(
                            require_payload(keys), [], (), keys.store, frame, node
                        ):
                            if called.kind != "normal":
                                errors.append(called)
                            else:
                                copied, failed = self._mapping_keys(
                                    source,
                                    require_payload(called),
                                    replace(builder, store=called.store),
                                    frame,
                                    node,
                                )
                                result.extend(copied)
                                errors.extend(failed)
        return result, errors

    def construct(
        self,
        name: str,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        if len(args) > 1 or name != "dict" and keywords:
            return [self.t.error(store, "TypeError")]
        pending = [_Builder(store.copy())]
        errors: list[Flow] = []
        if args:
            pending, errors = (
                self._mapping(args[0], pending[0], frame, node)
                if name == "dict"
                else self._consume(args[0], pending[0], frame, node, pairs=False)
            )
        for key, value in keywords:
            following: list[_Builder] = []
            for current in pending:
                inserted, failed = self._insert(current, Value.constant(key), value, frame, node)
                following.extend(inserted)
                errors.extend(failed)
            pending = following
        for builder in pending:
            fields: dict[FieldKey, Value] = (
                builder.fields if name == "dict" else dict(enumerate(builder.fields.values()))
            )
            allocated = self.t.alloc(
                node,
                builder.store,
                frame,
                name,
                fields,
                {"length": len(fields)} if builder.exact else {},
            )
            for reference in allocated.refs:
                obj = builder.store.heap[reference]
                obj.default = self.ops.join(obj.default, builder.default)
                obj.maybe_missing |= builder.missing
            if not builder.exact:
                allocated = self.ops.join(allocated, Value.unknown())
            errors.append(self.t.normal(builder.store, allocated))
        return self.t.d.compact(errors, execution=self.t.execution)
