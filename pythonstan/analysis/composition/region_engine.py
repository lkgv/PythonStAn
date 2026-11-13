"""Finite ordered Native regions compiled into a monotone inclusion kernel.

Cells and fields select immutable versions. Successful writes update a detached
working boundary immediately; each possible exception snapshots that prefix.
The underlying inclusion facts, constraints, and worklist remain monotone.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Literal

from pythonstan.analysis.composition.oracle import InclusionNode, IncrementalInclusionStore
from pythonstan.analysis.composition.state_adapter import BoundaryObject, FullStateAdapter
from pythonstan.analysis.contracts.identity import CellId, ObjectId
from pythonstan.analysis.contracts.json_data import JsonObject
from pythonstan.analysis.domains.ints import IntLattice
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.state import Frame
from pythonstan.analysis.policies.region import RegionExecutionContext
from pythonstan.analysis.state.continuation import CompletionKind
from pythonstan.analysis.state.effects import CellWrite, HeapWrite
from pythonstan.analysis.state.flow import Flow
from pythonstan.analysis.state.keys import FieldKey
from pythonstan.config import ConfigurationError
from pythonstan.frontend.source import source_constant
from pythonstan.models.builtin_attributes import known_builtin_attribute
from pythonstan.models.builtins import PYTHON312_BUILTIN_NAMES

if TYPE_CHECKING:
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver

    from .plan import EffectiveRegionPlan


_ALLOWED_EXPRESSIONS = (
    ast.Constant,
    ast.Name,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Attribute,
    ast.Subscript,
    ast.BinOp,
    ast.UnaryOp,
)
_ALLOWED_STATEMENTS = (
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Expr,
    ast.Return,
    ast.Pass,
    ast.Global,
    ast.Nonlocal,
)


class _VersionSlot(Enum):
    CELL = "native_cell_version"
    FIELD = "native_field_version"


class _NoNormalPath(Exception):
    """The current straight-line normal path has terminated."""


@dataclass(frozen=True)
class OrderedTargetWrite:
    target: ast.expr
    source: InclusionNode


@dataclass(frozen=True)
class MaterializeUnpack:
    """One unpack layer is fully materialized before its child writes begin."""

    writes: tuple[OrderedTargetWrite, ...]


def validate_native_statement(node: ast.stmt) -> Literal[True]:
    if not isinstance(node, _ALLOWED_STATEMENTS):
        raise ConfigurationError(
            "native-straightline-v1 does not support selected " + type(node).__name__
        )
    for expression in ast.walk(node):
        if isinstance(expression, ast.expr) and not isinstance(expression, _ALLOWED_EXPRESSIONS):
            raise ConfigurationError(
                "native-straightline-v1 does not support selected expression "
                + type(expression).__name__
            )
        if isinstance(expression, ast.Dict) and any(key is None for key in expression.keys):
            raise ConfigurationError("native-straightline-v1 rejects **mapping literals")
    return True


def validate_region_plan(driver: InterproceduralDriver, plan: EffectiveRegionPlan) -> bool:
    for tree in driver.trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.stmt):
                continue
            decision = plan.decisions.get(driver.point(node))
            if decision and decision["engine"] == "native" and decision.get("origin") != "default":
                validate_native_statement(node)
    return True


class NativeRegionEngine:
    def __init__(self) -> None:
        self.adapter = FullStateAdapter()
        self.runs = 0
        self.work = 0
        self.trace: list[JsonObject] = []

    def execute(
        self,
        node: ast.stmt,
        flow: Flow,
        frame: Frame,
        driver: InterproceduralDriver,
        *,
        execution: RegionExecutionContext | None = None,
    ) -> list[Flow]:
        validate_native_statement(node)
        ops = (execution or driver.execution_for(frame)).ops
        if flow.kind != "normal":
            return [flow]
        if isinstance(node, ast.AnnAssign) and (frame.is_module or frame.is_class):
            raise ConfigurationError(
                "native-straightline-v1 does not model runtime annotation namespaces"
            )
        if not self.adapter.check_roundtrip(flow, ops):
            raise RuntimeError("full-store boundary roundtrip lost state")
        boundary = self.adapter.encode(flow)
        kernel = IncrementalInclusionStore(ops)
        temporaries = 0
        versions = 0
        checkpoints = 0
        cell_versions: dict[CellId, InclusionNode] = {}
        field_versions: dict[tuple[ObjectId, FieldKey], FieldKey] = {}
        loads: set[tuple[ObjectId, FieldKey]] = set()
        written_cells: set[CellId] = set()
        written_fields: set[tuple[ObjectId, FieldKey]] = set()
        raised: list[Flow] = []
        completion: CompletionKind = "normal"
        result_node: InclusionNode | None = None
        normal_possible = True

        for cell, value in boundary.cells.items():
            entry: InclusionNode = ("entry", cell)
            cell_versions[cell] = entry
            kernel.add_fact(entry, value, "ai_boundary_cell")
        for objid, obj in boundary.heap.items():
            for key, value in obj.fields.items():
                kernel.add_fact(("field", objid, key), value, "ai_boundary_heap")
            kernel.add_fact(("default", objid), obj.default, "ai_boundary_default")

        def temp() -> tuple[Literal["temporary"], int]:
            nonlocal temporaries
            temporaries += 1
            return ("temporary", temporaries)

        def fresh_field(ref: ObjectId, key: FieldKey) -> FieldKey:
            nonlocal versions
            versions += 1
            selected: FieldKey = (_VersionSlot.FIELD, versions, key)
            field_versions[(ref, key)] = selected
            return selected

        def field_key(ref: ObjectId, key: FieldKey) -> FieldKey:
            return field_versions.get((ref, key), key)

        def settle() -> None:
            remaining = max(0, driver.max_work - driver.work)
            before = kernel.work
            try:
                closed = kernel.solve(remaining)
            finally:
                driver.charge(kernel.work - before, "native_region_work")
            if not closed:
                from pythonstan.analysis.engines.abstract.driver import BudgetStop

                raise BudgetStop("native_region_global_work")

        def known(identifier: InclusionNode) -> Value:
            settle()
            return kernel.value(identifier)

        def failures(names: Iterable[str], *, normal: bool = True) -> None:
            nonlocal checkpoints
            for name in sorted(set(names)):
                if name.startswith(("unknown_", "resource_")):
                    raise ConfigurationError("Native region scalar operation has an open protocol")
                # decode detaches the complete current boundary, not the entry
                # flow, so later target writes cannot mutate this error prefix.
                exceptional = replace(
                    boundary, completion="raise", value=Value.constant(name), exception=name
                )
                raised.append(self.adapter.decode(exceptional))
                driver.exceptions.add(name)
                checkpoints += 1
            if not normal:
                raise _NoNormalPath

        def length_value(obj: BoundaryObject) -> Value:
            retained = obj.metadata.lookup("length_value")
            if retained is not None:
                return retained
            exact = obj.metadata.lookup("length")
            if exact is not None:
                return Value.constant(exact)
            if (
                obj.kind == "dict"
                and not obj.havoc
                and obj.default.is_bottom()
                and not obj.maybe_missing
                and not obj.metadata.lookup("summarized_keys")
            ):
                return Value.constant(len(obj.fields))
            return Value(ints=IntLattice.from_interval(0, None))

        def set_length(obj: BoundaryObject, cardinality: Value) -> None:
            obj.metadata["length_value"] = cardinality
            constants = cardinality.exact_constants()
            if constants is not None and len(constants) == 1 and type(constants[0]) is int:
                obj.metadata["length"] = constants[0]
            else:
                obj.metadata.pop("length", None)

        def dict_write_lengths(valid: list[tuple[ObjectId, FieldKey]]) -> None:
            receivers: dict[ObjectId, set[FieldKey]] = {}
            for receiver, key in valid:
                receivers.setdefault(receiver, set()).add(key)
            for receiver, keys in receivers.items():
                obj = direct(receiver, next(iter(keys)), True)
                if obj.kind != "dict":
                    continue
                previous_length = length_value(obj)
                must = obj.fields.keys() - obj.maybe_missing
                if keys <= must:
                    continue
                increased, _ = ops.scalar_binary(ast.Add(), previous_length, Value.constant(1))
                definite_absent = (
                    keys.isdisjoint(obj.fields)
                    and obj.default.is_bottom()
                    and not obj.metadata.lookup("summarized_keys")
                )
                set_length(
                    obj,
                    increased
                    if obj.singleton and len(receivers) == 1 and definite_absent
                    else ops.join(previous_length, increased),
                )

        def direct(ref: ObjectId, key: FieldKey, write: bool = False) -> BoundaryObject:
            ref = ops.arena.resolve(ref)
            obj = boundary.heap.get(ref)
            if obj is None:
                raise ConfigurationError("Native region boundary has no object declaration")
            if obj.havoc:
                raise ConfigurationError("Native region requires a closed direct-object boundary")
            if obj.kind == "class":
                raise ConfigurationError(
                    "Native direct-field profile excludes class/metaclass/descriptor lookup"
                )
            if obj.kind not in {"dict", "list", "tuple", "instance", "set"}:
                raise ConfigurationError(
                    "Native region does not support " + obj.kind + " object protocol"
                )
            if obj.class_ids:
                visited: set[ObjectId] = set()
                classes = list(obj.class_ids)
                while classes:
                    clsid = classes.pop()
                    if clsid in visited:
                        continue
                    visited.add(clsid)
                    if clsid == "builtin:object":
                        continue
                    cls = boundary.heap.get(clsid)
                    if cls is None or cls.havoc:
                        raise ConfigurationError("Native region class boundary is open")
                    if any(
                        name in cls.fields
                        for name in (
                            "__getattribute__",
                            "__getattr__",
                            "__setattr__",
                            "__getitem__",
                            "__setitem__",
                            "__bool__",
                            "__len__",
                        )
                    ):
                        raise ConfigurationError(
                            "Native region cannot bypass inherited user protocol methods"
                        )
                    if key in cls.fields:
                        raise ConfigurationError(
                            "Native region requires own direct fields rather than class/descriptor lookup"
                        )
                    classes.extend(cls.metadata.lookup("mro", cls.metadata.lookup("bases", ())))
            return obj

        def scalar_part(value: Value) -> Value:
            return replace(value, refs=frozenset(), unbound=False, relations=frozenset())

        def load(expr: ast.Attribute | ast.Subscript) -> InclusionNode:
            target = temp()
            base = known(compile_expression(expr.value))
            keys = (
                [expr.attr]
                if isinstance(expr, ast.Attribute)
                else known(compile_expression(expr.slice)).exact_constants()
            )
            if base.top or base.top_sorts:
                raise ConfigurationError("Native region load needs a closed finite receiver set")
            if keys is None:
                raise ConfigurationError("Native region load requires finite builtin keys")
            errors: set[str] = set()
            scalar = scalar_part(base)
            if not scalar.is_bottom():
                if isinstance(expr, ast.Attribute):
                    constants = scalar.exact_constants()
                    if constants is None or any(
                        known_builtin_attribute(type(value).__name__, expr.attr)
                        for value in constants
                    ):
                        raise ConfigurationError(
                            "Native scalar attribute protocol is outside the direct-field profile"
                        )
                    errors.add("AttributeError")
                else:
                    constants = scalar.exact_constants()
                    if constants is None:
                        raise ConfigurationError(
                            "Native region requires a closed builtin scalar subscript"
                        )
                    for concrete in constants:
                        for key in keys:
                            try:
                                if not isinstance(concrete, str) or not isinstance(key, int):
                                    raise TypeError
                                kernel.add_fact(
                                    target, Value.constant(concrete[key]), "scalar_load"
                                )
                            except (TypeError, IndexError) as error:
                                errors.add(type(error).__name__)
            for key in keys:
                for ref in base.refs:
                    obj = direct(ref, key)
                    if isinstance(expr, ast.Attribute) and obj.kind in {
                        "dict",
                        "list",
                        "tuple",
                        "set",
                    }:
                        if known_builtin_attribute(obj.kind, expr.attr):
                            raise ConfigurationError(
                                "Native builtin attribute protocol is outside the direct-field profile"
                            )
                        errors.add("AttributeError")
                        continue
                    actual = key
                    if isinstance(expr, ast.Subscript):
                        if obj.kind == "set":
                            errors.add("TypeError")
                            continue
                        if obj.kind in {"list", "tuple"}:
                            if not isinstance(actual, int):
                                errors.add("TypeError")
                                continue
                            length = obj.metadata.lookup("length")
                            if length is not None:
                                actual = actual + length if actual < 0 else actual
                                if not 0 <= actual < length:
                                    errors.add("IndexError")
                                    continue
                    specific = temp()
                    kernel.add_fact(specific, Value.ref(ref), "valid_load_receiver")
                    kernel.add_load(specific, field_key(ref, actual), target)
                    if not obj.default.is_bottom():
                        kernel.add_edge(("default", ref), target)
                    if actual not in obj.fields or actual in obj.maybe_missing:
                        errors.add(
                            "AttributeError"
                            if isinstance(expr, ast.Attribute)
                            else "KeyError"
                            if obj.kind == "dict"
                            else "IndexError"
                        )
                    loads.add((ref, actual))
            failures(errors, normal=not known(target).is_bottom())
            return target

        def compile_expression(expr: ast.expr) -> InclusionNode:
            target = temp()
            if isinstance(expr, ast.Constant):
                kernel.add_fact(target, Value.constant(source_constant(expr)), "literal")
            elif isinstance(expr, ast.Name):
                cell = frame.cell(expr.id)
                source = cell_versions.get(cell, ("entry", cell))
                value = known(source)
                if source not in kernel.facts:
                    value = (
                        Value(top=True, unbound=True)
                        if frame.module in boundary.havoc_namespaces and cell.startswith("global:")
                        else Value(unbound=True)
                    )
                    kernel.add_fact(source, value, "namespace_default_bound")
                    cell_versions[cell] = source
                can_fallback = (
                    frame.is_module
                    or expr.id not in frame.locals
                    and expr.id not in dict(frame.closure)
                    and expr.id not in frame.nonlocals
                )
                if value.unbound and can_fallback:
                    if expr.id in {"__name__", "__package__", "__file__"}:
                        module = driver.modules[frame.module]
                        item = (
                            frame.module
                            if expr.id == "__name__"
                            else module.filename
                            if expr.id == "__file__"
                            else frame.module
                            if module.is_package
                            else frame.module.rpartition(".")[0]
                        )
                        value = ops.join(value.without_unbound(), Value.constant(item))
                    elif expr.id in PYTHON312_BUILTIN_NAMES:
                        value = ops.join(
                            value.without_unbound(), Value.ref(ObjectId("builtin:" + expr.id))
                        )
                if value.unbound:
                    failures(
                        ("UnboundLocalError" if expr.id in frame.locals else "NameError",),
                        normal=not value.without_unbound().is_bottom(),
                    )
                kernel.add_fact(target, value.without_unbound(), "bound_name_projection")
                if not kernel.value(source).unbound:
                    kernel.add_edge(source, target)
            elif isinstance(expr, (ast.List, ast.Tuple, ast.Dict)):
                kind = type(expr).__name__.lower()
                original = ObjectId(f"{kind}:{driver.point(expr)}@{'|'.join(frame.context)}")
                objid = ops.arena.resolve(original)
                contribution = BoundaryObject(
                    kind, {}, Value.bottom(), set(), objid == original, frozenset(), {}, False
                )
                if isinstance(expr, ast.Dict):
                    must: set[FieldKey] = set()
                    lower = upper = 0
                    for keyexpr, valueexpr in zip(expr.keys, expr.values, strict=True):
                        if keyexpr is None:
                            raise ConfigurationError("Native region rejects **mapping literals")
                        alternatives = known(compile_expression(keyexpr)).exact_constants()
                        if alternatives is None:
                            raise ConfigurationError("Native literal requires finite builtin keys")
                        keys: set[FieldKey] = set(alternatives)
                        literal_value = known(compile_expression(valueexpr))
                        if not keys:
                            raise _NoNormalPath
                        if not keys <= must:
                            upper += 1
                            if keys.isdisjoint(contribution.fields):
                                lower += 1
                        for literal_key in keys:
                            contribution.fields[literal_key] = (
                                literal_value
                                if len(keys) == 1
                                else ops.join(
                                    contribution.fields.get(literal_key, Value.bottom()),
                                    literal_value,
                                )
                            )
                        if len(keys) == 1:
                            must.update(keys)
                    contribution.maybe_missing.update(contribution.fields.keys() - must)
                    set_length(contribution, Value(ints=IntLattice.from_interval(lower, upper)))
                else:
                    for index, element in enumerate(expr.elts):
                        contribution.fields[index] = known(compile_expression(element))
                    set_length(contribution, Value.constant(len(expr.elts)))
                # Child expressions finish before the new literal becomes visible.
                # They can themselves allocate a canonical identity shared with
                # this literal, so look up the previous owner only now.
                previous_owner = boundary.heap.get(objid)
                if previous_owner is None:
                    obj = contribution
                else:
                    cardinality = ops.join(length_value(previous_owner), length_value(contribution))
                    set_length(previous_owner, cardinality)
                    set_length(contribution, cardinality)
                    obj = self.adapter.merge_literal(previous_owner, contribution, ops)
                boundary.heap[objid] = obj
                boundary.guards = {
                    name: guard for name, guard in boundary.guards.items() if guard.owner != objid
                }
                for literal_key, merged_value in obj.fields.items():
                    selected = fresh_field(objid, literal_key)
                    kernel.add_fact(
                        ("field", objid, selected), merged_value, "allocation_slot_join"
                    )
                    # No outward publication precedes its admitted kernel step.
                    observed = known(("field", objid, selected))
                    driver.publish_oracle_field(objid, literal_key, observed, "native_allocation")
                kernel.add_fact(("default", objid), obj.default, "allocation_default")
                kernel.add_fact(target, Value.ref(objid), "allocation")
            elif isinstance(expr, (ast.Attribute, ast.Subscript)):
                return load(expr)
            elif isinstance(expr, ast.BinOp):
                left = compile_expression(expr.left)
                right = compile_expression(expr.right)
                errors: set[str] = set()

                def operation(*values: Value) -> tuple[Value, Iterable[str]]:
                    if len(values) != 2:
                        raise TypeError("Binary inclusion rule requires two values")
                    a, b = values
                    if a.refs or b.refs or a.top or b.top:
                        raise ConfigurationError(
                            "Native scalar region cannot bypass user operators"
                        )
                    result, failed = ops.scalar_binary(expr.op, a, b)
                    errors.update(failed)
                    return result, failed

                kernel.add_operation((left, right), target, operation)
                failures(errors, normal=not known(target).is_bottom())
            elif isinstance(expr, ast.UnaryOp):
                operand = compile_expression(expr.operand)
                errors = set()

                def unary(*values: Value) -> tuple[Value, Iterable[str]]:
                    if len(values) != 1:
                        raise TypeError("Unary inclusion rule requires one value")
                    value = values[0]
                    if value.refs or value.top:
                        raise ConfigurationError(
                            "Native region cannot bypass object unary protocols"
                        )
                    result, failed = ops.scalar_unary(expr.op, value)
                    errors.update(failed)
                    return result, failed

                kernel.add_operation((operand,), target, unary)
                failures(errors, normal=not known(target).is_bottom())
            else:
                raise ConfigurationError("Unsupported Native region expression")
            known(target)
            return target

        def materialize(target: ast.Tuple | ast.List, source: InclusionNode) -> MaterializeUnpack:
            value = known(source)
            components: list[InclusionNode] = []
            if not value.refs:
                constants = value.exact_constants()
                if constants is None or len(constants) != 1:
                    raise ConfigurationError("Native unpack requires one bounded sequence shape")
                literal = constants[0]
                if not isinstance(literal, str):
                    failures(("TypeError",), normal=False)
                if not isinstance(literal, str):
                    raise AssertionError("terminated scalar unpack")
                if len(literal) != len(target.elts):
                    failures(("ValueError",), normal=False)
                for character in literal:
                    component = temp()
                    kernel.add_fact(component, Value.constant(character), "unpack_builtin_string")
                    components.append(component)
            else:
                if len(value.refs) != 1 or not scalar_part(value).is_bottom():
                    raise ConfigurationError("Native unpack requires one bounded sequence shape")
                ref = next(iter(value.refs))
                obj = direct(ref, 0)
                if obj.kind not in {"list", "tuple"}:
                    raise ConfigurationError("Native unpack requires a direct list or tuple")
                length = obj.metadata.lookup("length")
                if length is None:
                    raise ConfigurationError("Native unpack requires a complete bounded shape")
                if length != len(target.elts):
                    failures(("ValueError",), normal=False)
                for index in range(len(target.elts)):
                    if index not in obj.fields or index in obj.maybe_missing:
                        raise ConfigurationError("Native unpack requires every sequence component")
                    component = temp()
                    receiver = temp()
                    kernel.add_fact(receiver, Value.ref(ref), "unpack_receiver")
                    kernel.add_load(receiver, field_key(ref, index), component)
                    known(component)
                    components.append(component)
            return MaterializeUnpack(
                tuple(
                    OrderedTargetWrite(child, component)
                    for child, component in zip(target.elts, components, strict=True)
                )
            )

        def assign(target: ast.expr, source: InclusionNode) -> None:
            nonlocal versions, checkpoints
            value = known(source)
            if value.is_bottom():
                raise _NoNormalPath
            if isinstance(target, ast.Name):
                cell = frame.cell(target.id)
                versions += 1
                output: InclusionNode = (_VersionSlot.CELL, versions, cell)
                kernel.add_edge(source, output)
                value = known(output).without_unbound()
                cell_versions[cell] = output
                boundary.cells[cell] = value
                boundary.record_write(CellWrite(cell))
                written_cells.add(cell)
                driver.publish_oracle_cell(cell, value, "native_region_output")
                checkpoints += 1
            elif isinstance(target, (ast.Attribute, ast.Subscript)):
                base = known(compile_expression(target.value))
                keys = (
                    [target.attr]
                    if isinstance(target, ast.Attribute)
                    else known(compile_expression(target.slice)).exact_constants()
                )
                if base.top or base.top_sorts:
                    raise ConfigurationError("Native region write requires a closed receiver set")
                if keys is None:
                    raise ConfigurationError("Native region store requires finite builtin keys")
                errors: set[str] = set()
                valid: list[tuple[ObjectId, FieldKey]] = []
                if not scalar_part(base).is_bottom():
                    errors.add(
                        "AttributeError" if isinstance(target, ast.Attribute) else "TypeError"
                    )
                for key in keys:
                    for ref in base.refs:
                        obj = direct(ref, key, True)
                        if isinstance(target, ast.Attribute) and obj.kind in {
                            "dict",
                            "list",
                            "tuple",
                            "set",
                        }:
                            errors.add("AttributeError")
                            continue
                        if isinstance(target, ast.Subscript) and obj.kind not in {"dict", "list"}:
                            errors.add("TypeError")
                            continue
                        actual = key
                        if isinstance(target, ast.Subscript) and obj.kind == "list":
                            if not isinstance(actual, int):
                                errors.add("TypeError")
                                continue
                            length = obj.metadata.lookup("length")
                            if length is not None:
                                actual = actual + length if actual < 0 else actual
                                if not 0 <= actual < length:
                                    errors.add("IndexError")
                                    continue
                            else:
                                errors.add("IndexError")
                        valid.append((ref, actual))
                failures(errors, normal=bool(valid))
                dict_write_lengths(valid)
                for ref, written_key in valid:
                    obj = direct(ref, written_key, True)
                    existed = written_key in obj.fields and written_key not in obj.maybe_missing
                    previous = field_key(ref, written_key)
                    selected = fresh_field(ref, written_key)
                    receiver = temp()
                    kernel.add_fact(receiver, Value.ref(ref), "valid_store_receiver")
                    if not (obj.singleton and len(valid) == 1):
                        kernel.add_edge(("field", ref, previous), ("field", ref, selected))
                        if not obj.default.is_bottom():
                            kernel.add_edge(("default", ref), ("field", ref, selected))
                    kernel.add_store(receiver, selected, source)
                    obj.fields[written_key] = known(("field", ref, selected))
                    if obj.singleton and len(valid) == 1:
                        obj.maybe_missing.discard(written_key)
                    elif not existed:
                        obj.maybe_missing.add(written_key)
                    boundary.guards = {
                        name: guard for name, guard in boundary.guards.items() if guard.owner != ref
                    }
                    boundary.record_write(HeapWrite(ref, written_key))
                    written_fields.add((ref, written_key))
                    driver.publish_oracle_field(
                        ref, written_key, obj.fields[written_key], "native_region_output"
                    )
                    checkpoints += 1
            elif isinstance(target, (ast.Tuple, ast.List)):
                plan = materialize(target, source)
                for child in plan.writes:
                    assign(child.target, child.source)
            else:
                raise ConfigurationError("Unsupported Native assignment target")

        try:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                if node.value is not None:
                    compiled = compile_expression(node.value)
                    targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                    for target in targets:
                        assign(target, compiled)
            elif isinstance(node, ast.AugAssign):
                left = compile_expression(node.target)
                right = compile_expression(node.value)
                result_node = temp()
                errors: set[str] = set()

                def augmented(*values: Value) -> tuple[Value, Iterable[str]]:
                    if len(values) != 2:
                        raise TypeError("Augmented inclusion rule requires two values")
                    a, b = values
                    if a.refs or b.refs or a.top or b.top:
                        raise ConfigurationError("Native region cannot bypass augmented operators")
                    result, failed = ops.scalar_binary(node.op, a, b)
                    errors.update(failed)
                    return result, failed

                kernel.add_operation((left, right), result_node, augmented)
                failures(errors, normal=not known(result_node).is_bottom())
                assign(node.target, result_node)
                result_node = None
            elif isinstance(node, (ast.Expr, ast.Return)):
                if node.value is None:
                    result_node = temp()
                    kernel.add_fact(result_node, Value.constant(None), "implicit_none")
                else:
                    result_node = compile_expression(node.value)
                if isinstance(node, ast.Return):
                    completion = "return"
            settle()
        except _NoNormalPath:
            normal_possible = False

        for ref, key in loads:
            driver.reads.add((ref, str(key), driver.point(node)))
        for cell in written_cells:
            driver.writes.add((frame.qualname, cell, driver.point(node)))
        for ref, key in written_fields:
            driver.writes.add((ref, str(key), driver.point(node)))
        self.runs += 1
        self.work += kernel.work
        trace: JsonObject = {
            "engine": "native",
            "phase": "bounded_region_inclusion",
            "region": driver.point(node),
            "facts": len(kernel.facts),
            "work": kernel.work,
            "input_cells": len(flow.store.cells),
            "input_heap_objects": len(flow.store.heap),
            "ordered_checkpoints": checkpoints,
            "cell_versions": len(cell_versions),
            "field_versions": len(field_versions),
        }
        self.trace.append(trace)
        driver.phase_trace.append(trace)
        driver.increment_stat("native_region_runs")
        if normal_possible:
            boundary.completion = completion
            boundary.value = known(result_node) if result_node is not None else Value.constant(None)
            raised.append(self.adapter.decode(boundary))
        return raised
