"""Dictionary construction and subscription over full abstract values.

The host compiler is inspected, never executed, to recover CPython's grouping
of value evaluation and hash insertion for source dictionary displays.
"""

from __future__ import annotations

import ast
import copy
import dis
import hashlib
import sys
import types
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from itertools import product
from typing import TYPE_CHECKING, Literal

from pythonstan.analysis.contracts.json_data import JsonScalar
from pythonstan.analysis.domains.relations import KeyValueRelation, PartitionToken
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.state.effects import HeapDefaultWrite, HeapWrite
from pythonstan.analysis.state.flow import Flow, NormalExprFlow, require_payload
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.keys import FieldKey
from pythonstan.analysis.state.metadata import ContainerMeta, Metadata
from pythonstan.analysis.state.store import Frame, Store

if TYPE_CHECKING:
    from .transfer import Transfer


type PairBuild = tuple[Literal["pairs"], tuple[int, ...]]
type UnpackBuild = tuple[Literal["unpack"], int]
type DictionaryBuild = PairBuild | UnpackBuild
type BuildState = tuple[Store, dict[FieldKey, Value], Value]


type DictionaryPosition = tuple[int | None, int | None, int | None, int | None]


@dataclass(frozen=True)
class DictionaryInstruction:
    """The compiler metadata this qualified dictionary planner consumes."""

    opname: str
    arg: int | None
    position: DictionaryPosition | None


def _position_coordinate(value: object) -> int | None:
    if value is None or type(value) is int:
        return value
    raise ValueError("dictionary compiler position coordinate is invalid")


def _dictionary_instructions(code: types.CodeType) -> Iterator[DictionaryInstruction]:
    # dis.Instruction.argval is dynamically typed by the stdlib. It is not a
    # semantic input here: validate only the consumed metadata from the actual
    # CPython instruction tuple, without importing that dynamic field inward.
    instructions: Iterable[object] = dis.get_instructions(code)
    for raw in instructions:
        if not isinstance(raw, tuple) or len(raw) != 9:
            raise ValueError("dictionary compiler instruction shape is invalid")
        fields: tuple[object, ...] = raw
        opname, arg, positions = fields[0], fields[2], fields[8]
        if not isinstance(opname, str) or (arg is not None and type(arg) is not int):
            raise ValueError("dictionary compiler instruction metadata is invalid")
        position = None
        if positions is not None:
            if not isinstance(positions, tuple) or len(positions) != 4:
                raise ValueError("dictionary compiler position shape is invalid")
            coordinates: tuple[object, ...] = positions
            position = (
                _position_coordinate(coordinates[0]),
                _position_coordinate(coordinates[1]),
                _position_coordinate(coordinates[2]),
                _position_coordinate(coordinates[3]),
            )
        yield DictionaryInstruction(opname, arg, position)


def _string_index(text: str, index: JsonScalar) -> str:
    if not isinstance(index, int):
        raise TypeError("String indices must be integers")
    return text[index]


def _scalar_contains(item: JsonScalar, container: JsonScalar) -> bool:
    if not isinstance(container, str) or not isinstance(item, str):
        raise TypeError("Unsupported scalar membership operands")
    return item in container


class DictionaryOperations:
    def __init__(self, transfer: Transfer) -> None:
        self.t = transfer
        self.d = transfer.d
        self.ops = transfer.ops
        self._plans: dict[str, tuple[DictionaryBuild, ...]] = {}

    def _current(self, store: Store) -> None:
        store.ensure_current(self.ops)

    def _normalize(self, value: Value) -> Value:
        return self.ops.normalize(value)

    def plan(self, node: ast.Dict) -> tuple[DictionaryBuild, ...]:
        digest = hashlib.sha256(ast.dump(node, include_attributes=True).encode()).hexdigest()
        if digest in self._plans:
            return self._plans[digest]
        if (sys.version_info.major, sys.version_info.minor) != (3, 12):
            raise ValueError("unqualified dictionary compiler profile")
        copied = copy.deepcopy(node)
        function = ast.FunctionDef(
            name="_dictionary_probe",
            args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Return(value=copied)],
            decorator_list=[],
            type_params=[],
        )
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        compiled = compile(module, "<static-dictionary-shape>", "exec")
        constants: tuple[object, ...] = compiled.co_consts
        code = next(item for item in constants if isinstance(item, types.CodeType))
        position = (node.lineno, node.end_lineno, node.col_offset, node.end_col_offset)
        operations: list[DictionaryBuild] = []
        cursor = 0
        for item in _dictionary_instructions(code):
            positions = item.position
            if positions is None:
                raise ValueError("dictionary compiler instruction has no source position")
            if positions != position:
                continue
            if item.opname in {"BUILD_MAP", "BUILD_CONST_KEY_MAP"} and item.arg:
                count = item.arg
                if cursor + count > len(node.keys) or any(
                    key is None for key in node.keys[cursor : cursor + count]
                ):
                    raise ValueError("dictionary build shape mismatch")
                operations.append(("pairs", tuple(range(cursor, cursor + count))))
                cursor += count
            elif item.opname == "MAP_ADD":
                if cursor >= len(node.keys) or node.keys[cursor] is None:
                    raise ValueError("dictionary map-add shape mismatch")
                operations.append(("pairs", (cursor,)))
                cursor += 1
            elif (
                item.opname == "DICT_UPDATE"
                and cursor < len(node.keys)
                and node.keys[cursor] is None
            ):
                operations.append(("unpack", cursor))
                cursor += 1
        if cursor != len(node.keys):
            raise ValueError("dictionary source/build cursor mismatch")
        self._plans[digest] = tuple(operations)
        self.d.stats["dictionary_build_profile"] = "cpython-" + ".".join(
            map(str, (sys.version_info.major, sys.version_info.minor, sys.version_info.micro))
        )
        return self._plans[digest]

    def build(self, node: ast.Dict, store: Store, frame: Frame) -> list[Flow]:
        try:
            plan = self.plan(node)
        except (ValueError, TypeError, SyntaxError, StopIteration, AttributeError):
            return self.t.unknown(store, node, "dictionary_compiler_profile")
        # Each entry carries the complete constructed map. Residual/default is
        # a separate value, never an invented target string key.
        states: list[BuildState] = [(store.copy(), {}, Value.bottom())]
        errors: list[Flow] = []
        for operation in plan:
            next_states: list[BuildState] = []
            if operation[0] == "unpack":
                for state, fields, default in states:
                    for out in self.t.expr(node.values[operation[1]], state, frame):
                        if out.kind != "normal":
                            errors.append(out)
                            continue
                        candidates: list[BuildState] = []
                        for ref in require_payload(out).refs:
                            obj = out.store.heap.get(ref)
                            if obj and obj.kind in {"dict", "namespace"}:
                                additions = (
                                    self.t.namespaces.bindings(obj, out.store)
                                    if obj.kind == "namespace"
                                    else obj.fields
                                )
                                merged = dict(fields)
                                merged.update(additions)
                                candidates.append(
                                    (out.store.copy(), merged, self.ops.join(default, obj.default))
                                )
                                if obj.havoc:
                                    for unknown in self.t.unknown(
                                        out.store, node, "havoc_dictionary_unpack"
                                    ):
                                        if unknown.kind == "normal":
                                            candidates.append(
                                                (unknown.store, merged, Value.unknown())
                                            )
                                        else:
                                            errors.append(unknown)
                            else:
                                for unknown in self.t.unknown(
                                    out.store, node, "dynamic_dictionary_unpack"
                                ):
                                    if unknown.kind == "normal":
                                        candidates.append(
                                            (
                                                unknown.store,
                                                dict(fields),
                                                self.ops.join(default, Value.unknown()),
                                            )
                                        )
                                    else:
                                        errors.append(unknown)
                        if require_payload(out).top or require_payload(out).top_sorts:
                            for unknown in self.t.unknown(
                                out.store, node, "dynamic_dictionary_unpack"
                            ):
                                if unknown.kind == "normal":
                                    candidates.append(
                                        (unknown.store, dict(fields), Value.unknown())
                                    )
                                else:
                                    errors.append(unknown)
                        if (
                            not require_payload(out).refs
                            and not require_payload(out).top
                            and not require_payload(out).top_sorts
                        ):
                            errors.append(self.t.error(out.store.copy(), "TypeError"))
                        next_states.extend(candidates)
            else:
                evaluation_nodes: list[ast.expr] = []
                for index in operation[1]:
                    key_node = node.keys[index]
                    if key_node is None:
                        raise ValueError("dictionary build shape mismatch")
                    evaluation_nodes.extend((key_node, node.values[index]))
                for state, fields, default in states:
                    evaluated, abrupt = self.t.eval_nodes(evaluation_nodes, state, frame)
                    errors.extend(abrupt)
                    for evaluated_store, values in evaluated:
                        inserted: list[BuildState] = [(evaluated_store, dict(fields), default)]
                        for key, value in zip(values[::2], values[1::2], strict=False):
                            updated: list[BuildState] = []
                            for insertion_store, current, residual in inserted:
                                cases, abrupt = self.t.namespaces.key_cases(
                                    key, insertion_store, frame, node
                                )
                                errors.extend(abrupt)
                                for hashed, constants in cases:
                                    if constants is None:
                                        merged = {
                                            name: self.ops.join(old, value)
                                            for name, old in current.items()
                                        }
                                        updated.append(
                                            (hashed, merged, self.ops.join(residual, value))
                                        )
                                    else:
                                        for concrete in constants:
                                            merged = dict(current)
                                            merged[concrete] = value
                                            updated.append((hashed.copy(), merged, residual))
                            inserted = updated
                        next_states.extend(inserted)
            states = next_states
        result = list(errors)
        for state, fields, default in states:
            metadata = Metadata.from_record(
                "dict", ContainerMeta(length=len(fields) if default.is_bottom() else None)
            )
            fields = {key: self.ops.normalize(value) for key, value in fields.items()}
            default = self.ops.normalize(default)
            value = self.t.alloc(node, state, frame, "dict", fields, metadata)
            for ref in value.refs:
                state.heap[ref].default = default
            result.append(NormalExprFlow(StateHandle.capture(state), value=value))
        return result

    def getitem(
        self, base: Value, key: Value, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        self._current(store)
        base = self._normalize(base)
        key = self._normalize(key)
        result: list[Flow] = []
        if base.top:
            result.extend(self.t.unknown(store, node, "unknown_subscript"))
        for ref in base.refs:
            obj = store.heap.get(ref)
            if obj is None:
                result.extend(self.t.unknown(store, node, "unknown_subscript_receiver"))
                continue
            if obj.kind == "dict_view":
                result.append(self.t.error(store.copy(), "TypeError"))
                continue
            if obj.kind == "namespace":
                result.extend(self.t.namespaces.getitem(ref, key, store.copy(), frame, node))
                continue
            if obj.kind not in {"dict", "list", "tuple", "range", "set"}:
                for method in self.t.getattr(
                    Value.ref(ref), "__getitem__", store.copy(), frame, node
                ):
                    if method.kind == "normal":
                        result.extend(
                            self.t.invoke(
                                require_payload(method), [key], (), method.store, frame, node
                            )
                        )
                    else:
                        result.append(method)
                continue
            if obj.kind == "set":
                result.append(self.t.error(store.copy(), "TypeError"))
                continue
            cases, abrupt = (
                self.t.namespaces.key_cases(key, store.copy(), frame, node)
                if obj.kind == "dict"
                else ([(store.copy(), key.exact_constants())], [])
            )
            result.extend(abrupt)
            for current, constants in cases:
                self._current(current)
                ref = self.ops.arena.resolve(ref)
                target = current.heap.get(ref)
                if target is None:
                    result.extend(self.t.unknown(current, node, "reabstracted_subscript_receiver"))
                    continue
                if obj.kind == "dict" and target.default.is_bottom() and not target.havoc:
                    keys = list(target.fields) if constants is None else list(constants)
                    eligible = [
                        concrete
                        for concrete in keys
                        if not target.fields.get(concrete, Value.bottom()).is_bottom()
                    ]
                    cap = max(0, self.d.abstract_integer("max_value_partitions", 4))
                    requested = bool(self.d.abstract.get("enable_value_partition", False))
                    partitioned = requested and len(eligible) <= cap
                    handlers = {
                        handler
                        for concrete in keys
                        for handler in target.fields.get(concrete, Value.bottom()).refs
                    }
                    if partitioned:
                        for concrete in eligible:
                            value = target.fields[concrete]
                            state = current.copy()
                            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Name):
                                state.cells[frame.cell(node.slice.id)] = Value.constant(concrete)
                            partition_tokens = KeyValueRelation(
                                frozenset(
                                    PartitionToken.make(
                                        self.d.point(node),
                                        concrete,
                                        handler,
                                        True,
                                        display_name=self.d.functions[handler].qualname
                                        if handler in self.d.functions
                                        else None,
                                    )
                                    for handler in value.refs
                                )
                            ).coarsen(cap)
                            result.append(
                                NormalExprFlow(
                                    StateHandle.capture(state),
                                    value=replace(
                                        value, relations=value.relations | partition_tokens.tokens
                                    ),
                                )
                            )
                        self.d.increment_stat("value_partition_branches", len(eligible))
                    else:
                        self.d.charge(len(keys) * len(handlers), "relation_product_work")
                        value = self.ops.join_all(
                            target.fields.get(concrete, Value.bottom()) for concrete in keys
                        )
                        tokens = frozenset(
                            PartitionToken.make(
                                self.d.point(node),
                                concrete,
                                handler,
                                False,
                                display_name=self.d.functions[handler].qualname
                                if handler in self.d.functions
                                else None,
                            )
                            for concrete in keys
                            for handler in handlers
                        )
                        if not value.is_bottom():
                            result.append(
                                NormalExprFlow(
                                    StateHandle.capture(current.copy()),
                                    value=replace(value, relations=value.relations | tokens),
                                )
                            )
                        if requested:
                            self.d.increment_stat("value_partition_collapses")
                    if constants is None or any(
                        concrete not in target.fields or concrete in target.maybe_missing
                        for concrete in keys
                    ):
                        result.append(self.t.error(current.copy(), "KeyError"))
                    self.d.increment_stat(
                        "value_partition_reads" if partitioned else "unpartitioned_key_reads"
                    )
                    continue
                if constants is None:
                    value = self.ops.join_all((*target.fields.values(), target.default))
                    if target.havoc:
                        value = self.ops.join(value, Value.unknown())
                    if not value.is_bottom():
                        result.append(
                            NormalExprFlow(StateHandle.capture(current.copy()), value=value)
                        )
                    result.append(
                        self.t.error(
                            current.copy(), "KeyError" if obj.kind == "dict" else "IndexError"
                        )
                    )
                else:
                    for concrete in constants:
                        actual = concrete
                        if obj.kind != "dict":
                            if not isinstance(actual, int) or type(actual) not in {int, bool}:
                                result.append(self.t.error(current.copy(), "TypeError"))
                                continue
                            if actual < 0 and "length" in target.metadata:
                                actual += target.metadata["length"]
                        value, missing = current.read_slot(ref, actual, self.ops)
                        if not value.is_bottom():
                            result.append(
                                NormalExprFlow(StateHandle.capture(current.copy()), value=value)
                            )
                        if missing:
                            result.append(
                                self.t.error(
                                    current.copy(),
                                    "KeyError" if obj.kind == "dict" else "IndexError",
                                )
                            )
                        self.d.reads.add((ref, repr(actual), self.d.point(node)))
        if not base.refs and not base.top:
            constants = key.exact_constants()
            strings = base.strings.get_consts()
            if strings is not None and constants is not None:
                values = []
                for text, index in product(strings, constants):
                    try:
                        values.append(Value.constant(_string_index(text, index)))
                    except (IndexError, TypeError) as exc:
                        result.append(self.t.error(store.copy(), type(exc).__name__))
                if values:
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(store.copy()), value=self.ops.join_all(values)
                        )
                    )
            elif base.top_sorts or not base.strings.is_bottom():
                result.extend(self.t.unknown(store, node, "unmodeled_scalar_subscript"))
            elif not base.is_bottom():
                result.append(self.t.error(store.copy(), "TypeError"))
        return result

    def setitem(
        self,
        base: Value,
        key: Value,
        value: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        self._current(store)
        base = self._normalize(base)
        key = self._normalize(key)
        value = self._normalize(value)
        result: list[Flow] = []
        if base.top:
            result.extend(self.t.unknown(store, node, "unknown_subscript_write"))
        for ref in base.refs:
            current = store.copy()
            obj = current.heap.get(ref)
            if obj is None:
                result.extend(self.t.unknown(current, node, "unknown_subscript_receiver"))
                continue
            if obj.kind == "dict_view":
                result.append(self.t.error(current, "TypeError"))
                continue
            if obj.kind == "namespace":
                result.extend(self.t.namespaces.setitem(ref, key, value, current, frame, node))
                continue
            if obj.kind not in {"dict", "list"}:
                for method in self.t.getattr(Value.ref(ref), "__setitem__", current, frame, node):
                    if method.kind == "normal":
                        result.extend(
                            self.t.invoke(
                                require_payload(method), [key, value], (), method.store, frame, node
                            )
                        )
                    else:
                        result.append(method)
                continue
            cases, abrupt = (
                self.t.namespaces.key_cases(key, current, frame, node)
                if obj.kind == "dict"
                else ([(current, key.exact_constants())], [])
            )
            result.extend(abrupt)
            for state, constants in cases:
                self._current(state)
                ref = self.ops.arena.resolve(ref)
                if ref not in state.heap:
                    result.extend(self.t.unknown(state, node, "reabstracted_subscript_receiver"))
                    continue
                if constants is None:
                    state.invalidate_guards(ref)
                    target = state.heap[ref]
                    target.default = self.ops.join(target.default, value)
                    for field in target.fields:
                        target.fields[field] = self.ops.join(target.fields[field], value)
                        state.record_write(HeapWrite(ref, field))
                    state.record_write(HeapDefaultWrite(ref))
                    target.metadata.pop("length", None)
                    if obj.kind == "list":
                        result.append(self.t.error(state.copy(), "TypeError"))
                        result.append(self.t.error(state.copy(), "IndexError"))
                    for field, fieldvalue in target.fields.items():
                        self.d.publish_oracle_field(ref, field, fieldvalue, "ai_unknown_item_write")
                    self.d.writes.add((ref, "*", self.d.point(node)))
                    result.append(self.t.normal(state))
                    continue
                for concrete in constants:
                    branch = state.copy()
                    target = branch.heap[ref]
                    actual = concrete
                    if obj.kind == "list":
                        if not isinstance(actual, int) or type(actual) not in {int, bool}:
                            result.append(self.t.error(branch, "TypeError"))
                            continue
                        length = target.metadata.lookup("length")
                        if length is not None:
                            if actual < 0:
                                actual += length
                            if not 0 <= actual < length:
                                result.append(self.t.error(branch, "IndexError"))
                                continue
                        else:
                            result.append(self.t.error(branch.copy(), "IndexError"))
                    branch.write_slot(ref, actual, value, self.ops)
                    if obj.kind == "dict":
                        if (
                            target.singleton
                            and not target.maybe_missing
                            and target.default.is_bottom()
                        ):
                            target.metadata["length"] = len(target.fields)
                        else:
                            target.metadata.pop("length", None)
                    self.d.publish_oracle_field(ref, actual, target.fields[actual], "ai_item_write")
                    self.d.writes.add((ref, repr(actual), self.d.point(node)))
                    result.append(self.t.normal(branch))
        return result

    def contains(
        self, base: Value, key: Value, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        result: list[Flow] = []
        for ref in base.refs:
            obj = store.heap.get(ref)
            if obj is None:
                result.extend(self.t.unknown(store, node, "unknown_membership_receiver"))
                continue
            if obj.kind == "dict_view":
                result.extend(self.t.dictionary_views.contains(obj, key, store.copy(), frame, node))
                continue
            if obj.kind == "namespace":
                result.extend(self.t.namespaces.contains(ref, key, store.copy(), frame, node))
                continue
            if obj.kind == "dict":
                cases, abrupt = self.t.namespaces.key_cases(key, store.copy(), frame, node)
                result.extend(abrupt)
                for state, constants in cases:
                    self._current(state)
                    ref = self.ops.arena.resolve(ref)
                    if ref not in state.heap:
                        result.extend(
                            self.t.unknown(state, node, "reabstracted_membership_receiver")
                        )
                        continue
                    target = state.heap[ref]
                    truths = set()
                    if constants is None:
                        truths = {False, True}
                    else:
                        for concrete in constants:
                            if concrete in target.fields:
                                truths.add(True)
                            if (
                                concrete not in target.fields
                                or concrete in target.maybe_missing
                                or not target.default.is_bottom()
                                or target.havoc
                            ):
                                truths.add(False)
                            if target.havoc:
                                truths.add(True)
                    if truths:
                        result.append(
                            NormalExprFlow(
                                StateHandle.capture(state), value=Value(bools=frozenset(truths))
                            )
                        )
                continue
            methods = self.ops.join_all(
                self.t.lookup_class(cls, "__contains__", store) for cls in obj.class_ids
            )
            if not methods.is_bottom():
                for called in self.t.attributes._invoke_bound(
                    methods, Value.ref(ref), [key], (), store.copy(), frame, node
                ):
                    if called.kind == "normal":
                        result.extend(
                            self.t.truth_flows(require_payload(called), called.store, frame, node)
                        )
                    else:
                        result.append(called)
            elif obj.kind in {"list", "tuple", "range", "set"}:
                if (
                    key.refs
                    or key.top
                    or any(value.refs or value.top for value in obj.fields.values())
                ):
                    result.extend(
                        self.t.unknown(store, node, "unmodeled_element_equality_membership")
                    )
                    continue
                constants = key.exact_constants()
                truths = set()
                for value in obj.fields.values():
                    compared, _ = self.ops.scalar_binary(ast.Eq(), key, value)
                    truths.update(self.ops.truth(compared))
                if not obj.fields:
                    truths.add(False)
                if obj.havoc or not obj.default.is_bottom() or constants is None:
                    truths.update((True, False))
                # Multiple nonmatching elements do not establish a whole-sequence
                # false when one element can match; the may union is covering.
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(store.copy()), value=Value(bools=frozenset(truths))
                    )
                )
            else:
                result.extend(self.t.unknown(store, node, "unmodeled_iteration_membership"))
        scalar = replace(base, refs=frozenset())
        if not scalar.is_bottom():
            a = key.exact_constants()
            b = scalar.exact_constants()
            truths = set()
            if a is not None and b is not None:
                for item, container in product(a, b):
                    try:
                        truths.add(_scalar_contains(item, container))
                    except TypeError:
                        result.append(self.t.error(store.copy(), "TypeError"))
                if truths:
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(store.copy()), value=Value(bools=frozenset(truths))
                        )
                    )
            else:
                result.extend(self.t.unknown(store, node, "unknown_scalar_membership"))
        return result
