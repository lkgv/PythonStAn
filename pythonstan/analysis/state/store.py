"""Finite store and explicit completions for the independent abstract engine."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import assert_never

from pythonstan.analysis.contracts.identity import (
    CellId,
    ContextId,
    FrameId,
    ObjectId,
    StateRevision,
    UnitId,
)
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.state.bindings import BindingRef
from pythonstan.analysis.state.dependencies import SummaryDependency
from pythonstan.analysis.state.effects import (
    CellWrite,
    Havoc,
    HeapDefaultWrite,
    HeapDelete,
    HeapWrite,
    MetadataUpdate,
    WriteEffect,
)
from pythonstan.analysis.state.guards import MembershipGuard
from pythonstan.analysis.state.keys import FieldKey
from pythonstan.analysis.state.metadata import Metadata


def semantic_payload_equal(
    left: object, right: object, seen: set[tuple[int, int]] | None = None
) -> bool:
    from .transform import semantic_payload_equal as equal

    return equal(left, right, seen)


@dataclass(init=False)
class HeapObject:
    kind: str
    fields: dict[FieldKey, Value] = field(default_factory=dict)
    default: Value = field(default_factory=Value.bottom)
    maybe_missing: set[FieldKey] = field(default_factory=set)
    singleton: bool = True
    class_ids: frozenset[ObjectId] = frozenset()
    _metadata: Metadata = field(default_factory=Metadata, repr=False)
    havoc: bool = False

    def __init__(
        self,
        kind: str,
        fields: dict[FieldKey, Value] | None = None,
        default: Value | None = None,
        maybe_missing: set[FieldKey] | None = None,
        singleton: bool = True,
        class_ids: frozenset[ObjectId] = frozenset(),
        metadata: Metadata | Mapping[str, object] | None = None,
        havoc: bool = False,
    ) -> None:
        self.kind = kind
        self.fields = {} if fields is None else fields
        self.default = Value.bottom() if default is None else default
        self.maybe_missing = set() if maybe_missing is None else maybe_missing
        self.singleton = singleton
        self.class_ids = class_ids
        self.metadata = Metadata.from_legacy(kind, metadata)
        self.havoc = havoc

    @property
    def metadata(self) -> Metadata:
        return self._metadata

    @metadata.setter
    def metadata(self, value: Metadata | Mapping[str, object]) -> None:
        """The compatibility input boundary always publishes a typed record."""
        self._metadata = Metadata.from_legacy(self.kind, value)

    def copy(self) -> HeapObject:
        from .transform import detached_payload

        return detached_payload(self)

    def join(self, other: HeapObject, ops: ValueOperations) -> HeapObject:
        result = self.copy()
        result._merge_from(other, ops)
        return result

    def _merge_from(self, other: HeapObject, ops: ValueOperations) -> None:
        """Merge into an already detached graph shell without copying its root."""
        result = self
        left_default = self.default
        left_fields = dict(self.fields)
        left_missing = set(self.maybe_missing)
        result.singleton = self.singleton and other.singleton
        # Observable iterator/frame/length/default metadata belongs to the
        # store order. If distinct metadata cannot be represented jointly, the
        # object becomes a declared conservative havoc object; consumers must
        # not reuse the retained representative as an exact execution state.
        summarized_keys = frozenset(
            self.metadata.lookup("summarized_keys", frozenset[FieldKey]())
        ) | frozenset(other.metadata.lookup("summarized_keys", frozenset[FieldKey]()))
        abstract_keys = (
            {"summarized_keys", "length", "length_value"}
            if summarized_keys
            else {"summarized_keys"}
        )
        metadata_changed = not semantic_payload_equal(
            {k: v for k, v in self.metadata.items() if k not in abstract_keys},
            {k: v for k, v in other.metadata.items() if k not in abstract_keys},
        )
        if summarized_keys:
            result.metadata["summarized_keys"] = summarized_keys
            result.metadata.pop("length", None)
            result.metadata.pop("length_value", None)
        incompatible = (
            self.kind != other.kind or self.kind == "class" and self.class_ids != other.class_ids
        )
        result.havoc = self.havoc or other.havoc or metadata_changed or incompatible
        if incompatible:
            result.kind = "summary"
            result.metadata.reclassify("summary")
            result.singleton = False
        result.default = ops.join(left_default, other.default)
        if incompatible:
            result.default = ops.join(result.default, Value.unknown())
        result.class_ids = self.class_ids | other.class_ids
        for key in left_fields.keys() | other.fields.keys():
            result.fields[key] = ops.join(
                left_fields.get(key, left_default), other.fields.get(key, other.default)
            )
            if (
                key not in left_fields
                or key not in other.fields
                or key in left_missing
                or key in other.maybe_missing
            ):
                result.maybe_missing.add(key)


@dataclass
class Store:
    cells: dict[CellId, Value] = field(default_factory=dict)
    heap: dict[ObjectId, HeapObject] = field(default_factory=dict)
    initialized: dict[str, str] = field(default_factory=dict)
    writes: set[WriteEffect] = field(default_factory=set)
    events: tuple[tuple[str, str], ...] = ()
    obligations: frozenset[str] = frozenset()
    havoc_namespaces: frozenset[str] = frozenset()
    havoc_imports: bool = False
    guards: dict[str, MembershipGuard] = field(default_factory=dict)
    identity_epoch: int = 0
    state_revision: StateRevision = StateRevision(0)

    def record_write(self, effect: WriteEffect) -> None:
        self.writes.add(effect)
        self.state_revision = StateRevision(self.state_revision + 1)

    def effective_cell(self, cell: CellId) -> Value:
        if cell in self.cells:
            return self.cells[cell]
        module = cell[len("global:") :].rsplit(":", 1)[0] if cell.startswith("global:") else None
        return (
            Value(top=True, unbound=True)
            if module in self.havoc_namespaces
            else Value(unbound=True)
        )

    def copy(self) -> Store:
        from .transform import detached_payload

        return detached_payload(self)

    def ensure_current(self, ops: ValueOperations) -> Store:
        arena = ops.arena
        if self.identity_epoch != arena.epoch:
            from .transform import remap_store

            mapped = remap_store(self, arena.aliases, ops, epoch=arena.epoch)
            self.cells = mapped.cells
            self.heap = mapped.heap
            self.initialized = mapped.initialized
            self.writes = mapped.writes
            self.events = mapped.events
            self.obligations = mapped.obligations
            self.havoc_namespaces = mapped.havoc_namespaces
            self.havoc_imports = mapped.havoc_imports
            self.guards = mapped.guards
            self.identity_epoch = mapped.identity_epoch
            self.state_revision = StateRevision(self.state_revision + 1)
        return self

    def invalidate_guards(self, owner: ObjectId | None = None) -> None:
        if owner is None:
            self.guards.clear()
        else:
            self.guards = {key: fact for key, fact in self.guards.items() if fact.owner != owner}

    def clear_effects(self) -> Store:
        result = self.copy()
        result.writes = set()
        result.events = ()
        return result

    def write_cell(
        self,
        cell: CellId,
        value: Value,
        *,
        weak: bool = False,
        ops: ValueOperations | None = None,
    ) -> None:
        if ops is not None:
            self.ensure_current(ops)
            value = ops.normalize(value)
            cell = ops.arena.resolve_cell(cell)
        self.guards.pop(cell, None)
        if weak and cell in self.cells:
            if ops is None:
                raise ValueError("A weak cell write requires value operations")
            value = ops.join(self.cells[cell], value)
        self.cells[cell] = value
        self.record_write(CellWrite(cell))

    def read_slot(self, objid: ObjectId, key: FieldKey, ops: ValueOperations) -> tuple[Value, bool]:
        self.ensure_current(ops)
        objid = ops.arena.resolve(objid)
        if ops.arena.aliases:
            from .transform import PayloadTransformer

            key = PayloadTransformer(ops.arena.aliases, ops).key(key)
        obj = self.heap.get(objid)
        if obj is None:
            return Value.unknown(), True
        value = obj.fields.get(key, obj.default)
        if obj.havoc:
            value = ops.join(value, Value.unknown())
        return value, key in obj.maybe_missing or key not in obj.fields

    def write_slot(
        self,
        objid: ObjectId,
        key: FieldKey,
        value: Value,
        ops: ValueOperations,
        *,
        weak: bool = False,
    ) -> None:
        self.ensure_current(ops)
        objid = ops.arena.resolve(objid)
        value = ops.normalize(value)
        if ops.arena.aliases:
            from .transform import PayloadTransformer

            key = PayloadTransformer(ops.arena.aliases, ops).key(key)
        obj = self.heap[objid]
        weak = weak or not obj.singleton or key in obj.metadata.lookup("summarized_keys", ())
        was_missing = key not in obj.fields or key in obj.maybe_missing
        if weak:
            value = ops.join(obj.fields.get(key, obj.default), value)
        obj.fields[key] = value
        if weak and was_missing:
            obj.maybe_missing.add(key)
        elif not weak:
            obj.maybe_missing.discard(key)
        self.invalidate_guards(objid)
        self.record_write(HeapWrite(objid, key))

    def join(self, other: Store, ops: ValueOperations, *, widen: bool = False) -> Store:
        self.ensure_current(ops)
        other.ensure_current(ops)
        from .transform import PayloadMemo, detached_payload

        memo: PayloadMemo = {}
        result = detached_payload(self, memo)
        memo[id(other)] = result
        for owner in self.heap.keys() & other.heap.keys():
            memo[id(other.heap[owner])] = result.heap[owner]
        join = ops.widen if widen else ops.join
        for cell in self.cells.keys() | other.cells.keys():
            a = self.effective_cell(cell)
            b = other.effective_cell(cell)
            result.cells[cell] = join(a, b)
        for key, obj in other.heap.items():
            if key in result.heap:
                result.heap[key]._merge_from(obj, ops)
            else:
                result.heap[key] = detached_payload(obj, memo)
        result.havoc_imports = self.havoc_imports or other.havoc_imports
        for module in self.initialized.keys() | other.initialized.keys():
            left_status = self.initialized.get(module, "maybe" if self.havoc_imports else "unseen")
            right_status = other.initialized.get(
                module, "maybe" if other.havoc_imports else "unseen"
            )
            result.initialized[module] = left_status if left_status == right_status else "maybe"
        result.writes |= other.writes
        result.guards = {
            key: fact
            for key, fact in self.guards.items()
            if key in other.guards and semantic_payload_equal(fact, other.guards[key])
        }
        result.obligations |= other.obligations
        result.havoc_namespaces |= other.havoc_namespaces
        # Events are may-prefix observations, not an order proof.
        result.events = tuple({event: None for event in (*self.events, *other.events)})
        result.state_revision = StateRevision(max(self.state_revision, other.state_revision) + 1)
        return result

    def leq(self, other: Store, ops: ValueOperations) -> bool:
        self.ensure_current(ops)
        other.ensure_current(ops)
        if not self.writes <= other.writes:
            return False
        from .transform import payload_signature

        if not {payload_signature(x) for x in self.events} <= {
            payload_signature(x) for x in other.events
        }:
            return False
        if any(
            key not in self.guards or not semantic_payload_equal(self.guards[key], fact)
            for key, fact in other.guards.items()
        ):
            return False
        # An omitted cell is an observable unbound alternative (or namespace
        # havoc), not Bottom.  Both explicit key sets therefore participate.
        for cell in self.cells.keys() | other.cells.keys():
            if not ops.leq(self.effective_cell(cell), other.effective_cell(cell)):
                return False
        for key, obj in self.heap.items():
            if key not in other.heap:
                return False
            rhs = other.heap[key]
            shape_cover = rhs.kind == "summary" and rhs.havoc
            if obj.kind != rhs.kind and not shape_cover:
                return False
            if not obj.class_ids <= rhs.class_ids and not shape_cover:
                return False
            if obj.havoc and not rhs.havoc:
                return False
            if not obj.singleton and rhs.singleton:
                return False
            if not rhs.havoc:
                left_keys = frozenset(obj.metadata.lookup("summarized_keys", ()))
                right_keys = frozenset(rhs.metadata.lookup("summarized_keys", ()))
                if not left_keys <= right_keys:
                    return False
                ignored = (
                    {"summarized_keys", "length", "length_value"}
                    if right_keys
                    else {"summarized_keys"}
                )
                if not semantic_payload_equal(
                    {k: v for k, v in obj.metadata.items() if k not in ignored},
                    {k: v for k, v in rhs.metadata.items() if k not in ignored},
                ):
                    return False
            if not ops.leq(obj.default, rhs.default):
                return False
            for field in (
                obj.fields.keys() | rhs.fields.keys() | obj.maybe_missing | rhs.maybe_missing
            ):
                if not ops.leq(
                    obj.fields.get(field, obj.default), rhs.fields.get(field, rhs.default)
                ):
                    return False
                # A residual/default value does not establish key presence.
                # read_slot reports untracked keys as possibly absent, while
                # a havoc object also covers absence of a tracked key.
                absent = field not in obj.fields or field in obj.maybe_missing
                covered_absent = rhs.havoc or field not in rhs.fields or field in rhs.maybe_missing
                if absent and not covered_absent:
                    return False
        if self.havoc_imports and not other.havoc_imports:
            return False
        for module in self.initialized.keys() | other.initialized.keys():
            a = self.initialized.get(module, "maybe" if self.havoc_imports else "unseen")
            b = other.initialized.get(module, "maybe" if other.havoc_imports else "unseen")
            if b != "maybe" and a != b:
                return False
        return (
            self.obligations <= other.obligations
            and self.havoc_namespaces <= other.havoc_namespaces
        )

    def remap(self, mapping: Mapping[ObjectId, ObjectId], ops: ValueOperations) -> Store:
        from .transform import remap_store

        return remap_store(self, mapping, ops, epoch=ops.arena.epoch)

    def _apply_heap_footprint(
        self,
        effect: HeapWrite | HeapDelete | HeapDefaultWrite | MetadataUpdate,
        source_store: Store,
        ops: ValueOperations,
    ) -> None:
        """Materialize the callee poststate, including joined delete/write paths."""
        owner = effect.owner
        if owner not in source_store.heap:
            return
        source = source_store.heap[owner]
        target = self.heap[owner]
        from .transform import detached_payload

        target.metadata = detached_payload(source.metadata)
        match effect:
            case HeapDefaultWrite():
                target.default = ops.join(target.default, source.default)
                target.havoc |= source.havoc
            case MetadataUpdate():
                target.havoc |= source.havoc
            case HeapWrite(_, key) | HeapDelete(_, key):
                if key in source.fields:
                    target.fields[key] = source.fields[key]
                    if key in source.maybe_missing:
                        target.maybe_missing.add(key)
                    else:
                        target.maybe_missing.discard(key)
                else:
                    target.fields.pop(key, None)
                    target.maybe_missing.add(key)
            case _:
                assert_never(effect)

    def apply(self, outcome: Flow, ops: ValueOperations) -> Store:
        """Apply only callee writes; preserve caller frame and untouched slots."""
        self.ensure_current(ops)
        outcome.store.ensure_current(ops)
        result = self.copy()
        callee = outcome.store
        if callee.writes or callee.havoc_namespaces or callee.havoc_imports:
            result.invalidate_guards()
        for objid, obj in callee.heap.items():
            if objid not in result.heap:
                result.heap[objid] = obj.copy()
            else:
                # Cardinality and unknown-object covers are abstraction facts,
                # not source writes: losing either can enable an invalid strong
                # update after a reused fresh allocation summary.
                result.heap[objid].singleton &= obj.singleton
                result.heap[objid].class_ids |= obj.class_ids
        # Escaping closures need their allocated cells even if never reassigned.
        for cell, value in callee.cells.items():
            if cell not in result.cells:
                result.cells[cell] = value
        for effect in callee.writes:
            match effect:
                case CellWrite(cell):
                    result.cells[cell] = callee.cells.get(cell, Value(unbound=True))
                case HeapWrite() | HeapDelete() | HeapDefaultWrite() | MetadataUpdate():
                    result._apply_heap_footprint(effect, callee, ops)
                case Havoc(objects, cells, namespaces, imports):
                    for owner in objects:
                        if owner in result.heap:
                            result.heap[owner].havoc = True
                    for cell in cells:
                        result.cells[cell] = callee.effective_cell(cell)
                    result.havoc_namespaces |= namespaces
                    result.havoc_imports |= imports
                case _:
                    assert_never(effect)
        result.writes |= callee.writes
        result.initialized.update(callee.initialized)
        result.havoc_imports |= callee.havoc_imports
        result.obligations |= callee.obligations
        result.havoc_namespaces |= callee.havoc_namespaces
        result.events = tuple({event: None for event in (*result.events, *callee.events)})
        result.state_revision = StateRevision(max(self.state_revision, callee.state_revision) + 1)
        return result


@dataclass(frozen=True)
class Frame:
    module: str
    qualname: str
    context: ContextId
    identity: FrameId
    locals: frozenset[str] = frozenset()
    closure: tuple[tuple[str, CellId], ...] = ()
    globals: frozenset[str] = frozenset()
    nonlocals: frozenset[str] = frozenset()
    is_module: bool = False
    is_class: bool = False

    def binding(self, name: str) -> BindingRef:
        if self.is_module or name in self.globals:
            return BindingRef(name, CellId(f"global:{self.module}:{name}"), "global")
        closure = dict(self.closure)
        if name in self.nonlocals:
            if name in closure:
                return BindingRef(name, closure[name], "nonlocal")
            return BindingRef(
                name, CellId(f"unbound-nonlocal:{self.identity}:{name}"), "unbound_nonlocal"
            )
        if name in self.locals:
            return BindingRef(name, CellId(f"local:{self.identity}:{name}"), "local")
        if name in closure:
            return BindingRef(name, closure[name], "closure")
        return BindingRef(name, CellId(f"global:{self.module}:{name}"), "global")

    def cell(self, name: str) -> CellId:
        return self.binding(name).cell


@dataclass
class FunctionInfo:
    identifier: ObjectId
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
    module: str
    qualname: str
    closure: tuple[tuple[str, CellId], ...]
    defaults: tuple[Value, ...]
    kw_defaults: tuple[tuple[str, Value], ...]
    locals: frozenset[str]
    globals: frozenset[str]
    nonlocals: frozenset[str]
    is_generator: bool = False
    is_async: bool = False


@dataclass
class Unit:
    key: UnitId
    frame: Frame
    body: tuple[ast.stmt, ...]
    entry: Store
    outcomes: list[Flow] = field(default_factory=list)
    consumers: set[UnitId] = field(default_factory=set)
    dependencies: dict[UnitId, SummaryDependency] = field(default_factory=dict)
    revision: StateRevision = StateRevision(0)
    visits: int = 0
    is_module: bool = False


from .flow import Flow as Flow
from .flow import is_flow as is_flow
