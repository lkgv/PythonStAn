"""Python semantic operations on explicit stores and completion alternatives.

Only builtin scalar operators are evaluated by the host. Source functions,
imports, descriptors and cleanup run through the abstract driver and store.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import product
from typing import TYPE_CHECKING, assert_never

from pythonstan.analysis.contracts.identity import CellId, ContinuationId, FrameId, ObjectId
from pythonstan.analysis.contracts.json_data import JsonScalar
from pythonstan.analysis.contracts.private_slots import (
    function_annotations_slot,
    keyword_default_slot,
    positional_default_slot,
)
from pythonstan.analysis.domains.values import Value, ValueOperations
from pythonstan.analysis.semantics.callplan import (
    KeywordGroup,
    KeywordMapping,
    call_plan,
)
from pythonstan.analysis.semantics.names import classify_bindings
from pythonstan.analysis.semantics.scalar_strings import (
    UnsupportedStringMethod,
    apply_string_method,
)
from pythonstan.analysis.semantics.scalars import (
    scalar_aggregate,
    scalar_conversion,
    scalar_range,
    scalar_sort,
)
from pythonstan.analysis.state.continuation import (
    AssignResume,
    Continuation,
    ContinuationCommand,
    ExceptionContext,
    ExceptionRestoreRecord,
    ExprResume,
    OpaqueResume,
    ReturnResume,
    WhileResume,
)
from pythonstan.analysis.state.effects import Havoc, HeapDefaultWrite, HeapDelete
from pythonstan.analysis.state.flow import (
    BreakFlow,
    ContinueFlow,
    ExceptionValue,
    Flow,
    FlowTail,
    NormalExprFlow,
    RaiseFlow,
    SuspendFlow,
    as_statement,
    flow_payload,
    make_completion,
    replace_flow,
    require_payload,
)
from pythonstan.analysis.state.guards import MembershipGuard
from pythonstan.analysis.state.handles import StateHandle
from pythonstan.analysis.state.keys import FieldKey
from pythonstan.analysis.state.metadata import Metadata
from pythonstan.frontend.private_names import source_annotation_text, source_declared_name
from pythonstan.frontend.source import source_constant
from pythonstan.models.builtins import ABSTRACT_BUILTINS as _BUILTINS
from pythonstan.models.builtins import PYTHON312_BUILTIN_NAMES
from pythonstan.models.exceptions import (
    EXCEPTION_NAMES,
    builtin_exception_ancestors,
    require_builtin_exception_name,
)

from .state import Frame, FunctionInfo, HeapObject, Store

if TYPE_CHECKING:
    from pythonstan.analysis.policies.region import RegionExecutionContext

    from .driver import InterproceduralDriver

_BUILTINS = _BUILTINS | PYTHON312_BUILTIN_NAMES


def _constant_options(values: Sequence[Value]) -> list[list[JsonScalar]] | None:
    options: list[list[JsonScalar]] = []
    for value in values:
        constants = value.exact_constants()
        if constants is None:
            return None
        options.append(constants)
    return options


def _single_constants(values: Sequence[Value]) -> list[JsonScalar] | None:
    constants: list[JsonScalar] = []
    for value in values:
        alternatives = value.exact_constants()
        if alternatives is None or len(alternatives) != 1:
            return None
        constants.append(alternatives[0])
    return constants


def _first_keyword_constants(keywords: Sequence[tuple[str, Value]]) -> dict[str, JsonScalar]:
    result: dict[str, JsonScalar] = {}
    for key, value in keywords:
        constants = value.exact_constants()
        if constants is None:
            raise TypeError("Abstract scalar keyword has no finite projection")
        result[key] = constants[0]
    return result


@dataclass(frozen=True, slots=True)
class _NameLocation:
    target: ast.Name


@dataclass(frozen=True, slots=True)
class _AttributeLocation:
    receiver: Value
    name: str


@dataclass(frozen=True, slots=True)
class _ItemLocation:
    container: Value
    key: Value


type _AugmentedLocation = _NameLocation | _AttributeLocation | _ItemLocation


@dataclass(frozen=True, slots=True)
class _CallPath:
    store: Store
    arguments: tuple[Value, ...] = ()
    keywords: tuple[tuple[FieldKey, Value], ...] = ()
    uncertain: bool = False
    deferred_star: tuple[Value, ast.expr] | None = None


type _BooleanPath = tuple[Flow, frozenset[bool] | None]


def _scalar_length(value: JsonScalar) -> int:
    if isinstance(value, str):
        return len(value)
    raise TypeError("Scalar has no length")


class Transfer:
    def __init__(
        self, driver: InterproceduralDriver, execution: RegionExecutionContext | None = None
    ) -> None:
        self.d: InterproceduralDriver = driver
        self.execution = execution or driver.default_execution
        self.ops: ValueOperations = self.execution.ops
        from .attributes import AttributeOperations
        from .continuations import ContinuationEngine
        from .dictionaries import DictionaryOperations
        from .dictionary_views import DictionaryViews
        from .namespaces import NamespaceOperations

        self.attributes = AttributeOperations(self)
        self.continuations = ContinuationEngine(self)
        self.namespaces = NamespaceOperations(self)
        self.dictionaries = DictionaryOperations(self)
        self.dictionary_views = DictionaryViews(self)

    def sync(self, store: Store) -> Store:
        store.ensure_current(self.ops)
        return store

    def setup_annotations(
        self, body: Iterable[ContinuationCommand], flows: Sequence[Flow], frame: Frame
    ) -> None:
        if not (frame.is_module or frame.is_class):
            return
        commands = tuple(body)
        marker = CellId("$annotation_setup:" + frame.identity)

        def contains(nodes: Iterable[ContinuationCommand | ast.AST]) -> bool:
            for item in nodes:
                if isinstance(item, ast.AnnAssign):
                    return True
                if isinstance(
                    item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
                ):
                    continue
                if isinstance(item, ast.AST) and contains(ast.iter_child_nodes(item)):
                    return True
            return False

        if not contains(commands):
            return
        for flow in flows:
            if marker in flow.store.cells:
                continue
            flow.store.cells[marker] = Value.constant(True)
            cell = frame.cell("__annotations__")
            if cell not in flow.store.cells or flow.store.cells[cell].unbound:
                value = self.alloc(
                    commands[0] if commands and isinstance(commands[0], ast.AST) else None,
                    flow.store,
                    frame,
                    "dict",
                    {},
                    {"length": 0},
                )
                flow.store.write_cell(cell, value)

    def has_suspension(self, node: ast.AST | None) -> bool:
        def visit(item: ast.AST) -> bool:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return False
            return isinstance(item, (ast.Yield, ast.YieldFrom)) or any(
                visit(child) for child in ast.iter_child_nodes(item)
            )

        return isinstance(node, ast.AST) and visit(node)

    def normal(self, store: Store, value: Value | None = None) -> NormalExprFlow:
        return NormalExprFlow(
            StateHandle.capture(store), Value.constant(None) if value is None else value
        )

    def error(self, store: Store, name: str) -> RaiseFlow:
        self.d.exceptions.add(name)
        return RaiseFlow(StateHandle.capture(store), ExceptionValue(Value.constant(name), name))

    def unknown(
        self, store: Store, node: ast.AST | None, reason: str, *, havoc: bool = True
    ) -> list[Flow]:
        self.sync(store)
        result = store.copy()
        self.d.open(reason, result, node)
        if havoc:
            if hasattr(result, "guards"):
                result.guards.clear()
            for objid, obj in result.heap.items():
                obj.havoc = True
                result.record_write(HeapDefaultWrite(objid))
            captured: set[CellId] = set()
            for obj in result.heap.values():
                captured.update(cell for _, cell in obj.metadata.lookup("closure", ()))
                suspended_frame = obj.metadata.lookup("frame")
                if suspended_frame is not None:
                    captured.update(cell for _, cell in suspended_frame.closure)
            havoced_cells: set[CellId] = set()
            for cell in list(result.cells):
                if cell.startswith("global:") or cell in captured or "local:" not in cell:
                    result.write_cell(cell, Value(top=True, unbound=True))
                    havoced_cells.add(cell)
            result.havoc_namespaces |= frozenset(self.d.modules)
            result.initialized = dict.fromkeys(result.initialized, "maybe")
            result.havoc_imports = True
            result.record_write(
                Havoc(
                    objects=frozenset(result.heap),
                    cells=frozenset(havoced_cells),
                    namespaces=result.havoc_namespaces,
                    imports=True,
                )
            )
            self.d.writes.add(("unknown", "reachable_heap_and_globals", self.d.point(node)))
            self.d.calls.add(("<unknown>", "<callback>", self.d.point(node)))
        self.d.exceptions.add("BaseException")
        return [
            NormalExprFlow(StateHandle.capture(result), Value.unknown()),
            RaiseFlow(
                StateHandle.capture(result.copy()), ExceptionValue(Value.unknown(), "AnyException")
            ),
        ]

    def block(
        self, body: Iterable[ContinuationCommand], flows: list[Flow], frame: Frame
    ) -> list[Flow]:
        return self.continuations.block(body, flows, frame)

    def read_name(self, name: str, store: Store, frame: Frame, node: ast.AST | None) -> list[Flow]:
        self.sync(store)
        binding = frame.binding(name)
        cell = binding.cell
        if frame.is_class and "local:" + frame.identity + ":" + name in store.cells:
            cell = CellId("local:" + frame.identity + ":" + name)
        self.d.reads.add((frame.qualname, name, self.d.point(node)))
        value = store.effective_cell(cell)
        if frame.is_class and binding.scope == "local" and value.unbound:
            # LOAD_NAME falls through the class namespace to module globals.
            # DELETE_NAME itself never consults this fallback.
            fallback = store.effective_cell(CellId(f"global:{frame.module}:{name}"))
            value = self.ops.join(value.without_unbound(), fallback)
        if self.d.oracle is not None and (
            value.top or value.is_bottom() or cell not in store.cells and value.unbound
        ):
            candidate = self.d.oracle.candidates(cell, self.d.current)
            if not candidate.values.is_bottom():
                value = self.ops.join(value, candidate.values)
                self.d.increment_stat("oracle_values_consumed", 1)
        if (
            cell not in store.cells
            and frame.module not in store.havoc_namespaces
            and value.unbound
            and name in ("__name__", "__package__", "__file__", "__doc__")
            and (
                frame.is_module
                or frame.is_class
                or name not in frame.locals
                and name not in dict(frame.closure)
                and name not in frame.nonlocals
            )
        ):
            source = self.d.modules[frame.module]
            metadata = {
                "__name__": frame.module,
                "__file__": source.filename,
                "__package__": frame.module
                if source.is_package
                else frame.module.rpartition(".")[0],
                "__doc__": ast.get_docstring(self.d.trees[frame.module], clean=False),
            }
            value = self.ops.join(value.without_unbound(), Value.constant(metadata[name]))
        elif (
            value.unbound
            and name in _BUILTINS
            and (
                frame.is_module
                or frame.is_class
                or name not in frame.locals
                and name not in dict(frame.closure)
                and name not in frame.nonlocals
            )
        ):
            if name in {"__name__", "__package__", "__debug__"}:
                builtin = Value.constant(
                    {"__name__": "builtins", "__package__": "", "__debug__": True}[name]
                )
            elif name == "__doc__":
                self.d.open("builtin_module_metadata:__doc__", store, node)
                builtin = Value(strings=self.ops.string.top())
            elif name in {"__loader__", "__spec__"}:
                self.d.open("builtin_module_metadata:" + name, store, node)
                builtin = Value.unknown()
            else:
                builtin = Value.ref(ObjectId("builtin:" + name))
            value = self.ops.join(value.without_unbound(), builtin)
        if self.d.observer is not None:
            self.d.emit(
                "name_read",
                point=self.d.point(node),
                unit=self.d.current,
                cell=cell,
                value=value.export(),
            )
        out: list[Flow] = []
        if not value.without_unbound().is_bottom():
            out.append(NormalExprFlow(StateHandle.capture(store.copy()), value.without_unbound()))
        if value.unbound:
            out.append(
                self.error(
                    store.copy(),
                    "UnboundLocalError"
                    if binding.scope == "local" and not frame.is_class
                    else "NameError",
                )
            )
        return out

    def write_name(
        self, name: str, value: Value, store: Store, frame: Frame, node: ast.AST | None
    ) -> None:
        binding = frame.binding(name)
        cell = binding.cell
        store.write_cell(cell, value)
        self.d.publish_oracle_cell(cell, value, "ai_name_write")
        self.d.writes.add((frame.qualname, name, self.d.point(node)))
        if self.d.observer is not None:
            self.d.emit(
                "name_write",
                point=self.d.point(node),
                unit=self.d.current,
                cell=cell,
                value=value.export(),
            )

    def alloc(
        self,
        node: ast.AST | None,
        store: Store,
        frame: Frame,
        kind: str,
        fields: dict[FieldKey, Value] | None = None,
        metadata: Metadata | Mapping[str, object] | None = None,
        class_ids: Iterable[ObjectId] = (),
        *,
        role: str | None = None,
    ) -> Value:
        self.sync(store)
        point = self.d.point(node)
        allocation_point = point if role is None else point + "#" + role
        original = ObjectId(f"{kind}:{allocation_point}@{'|'.join(frame.context)}")
        objid = self.d.allocate_identity(original)
        old = store.heap.get(objid)
        obj = HeapObject(
            kind,
            fields or {},
            singleton=old is None and objid == original,
            class_ids=frozenset(class_ids),
            metadata=metadata or {},
        )
        if old is not None:
            obj = old.join(obj, self.ops)
            obj.singleton = False
        if old is not None:
            store.invalidate_guards(objid)
        store.heap[objid] = obj
        for key, value in obj.fields.items():
            self.d.publish_oracle_field(objid, key, value, "ai_allocation")
        self.d.increment_stat("allocations", 1)
        return Value.ref(objid)

    def eval_nodes(
        self, nodes: Iterable[ast.expr], store: Store, frame: Frame
    ) -> tuple[list[tuple[Store, tuple[Value, ...]]], list[Flow]]:
        states: list[tuple[Store, tuple[Value, ...]]] = [(store, ())]
        errors: list[Flow] = []
        for node in nodes:
            next_states: list[tuple[Store, tuple[Value, ...]]] = []
            for current, values in states:
                for flow in self.expr(node, current, frame, publish=False):
                    if flow.kind == "normal":
                        next_states.append((flow.store, (*values, require_payload(flow))))
                    else:
                        errors.append(flow)
            states = next_states
        return states, errors

    def expr(
        self, node: ast.expr | None, store: Store, frame: Frame, *, publish: bool = True
    ) -> list[Flow]:
        result = self._expr_impl(node, store, frame)
        return self.d.compact(result, execution=self.execution) if publish else result

    def _expr_impl(self, node: ast.expr | None, store: Store, frame: Frame) -> list[Flow]:
        result: list[Flow]
        self.sync(store)
        self.d.tick(node, execution=self.execution)
        if (
            node is not None
            and not isinstance(node, (ast.Yield, ast.YieldFrom, ast.Lambda))
            and self.has_suspension(node)
        ):
            result = self.unknown(store, node, "nested_suspension_expression")
            result.extend(
                SuspendFlow(
                    StateHandle.capture(out.store.copy()),
                    Value.unknown(),
                    FlowTail(
                        continuation=Continuation(
                            (OpaqueResume("nested_suspension_expression"),),
                            ContinuationId(f"{self.d.current}:{self.d.point(node)}:opaque"),
                            self.d.point(node),
                        )
                    ),
                )
                for out in tuple(result)
                if out.kind == "normal"
            )
            return result
        if node is None:
            return [self.normal(store.copy())]
        if isinstance(node, ast.Constant):
            return [
                NormalExprFlow(
                    StateHandle.capture(store.copy()), Value.constant(source_constant(node))
                )
            ]
        if isinstance(node, ast.Name):
            return self.read_name(node.id, store, frame, node)
        if isinstance(node, ast.NamedExpr):
            result = []
            for flow in self.expr(node.value, store, frame, publish=False):
                if flow.kind == "normal":
                    self.assign(node.target, require_payload(flow), flow.store, frame, node)
                result.append(flow)
            return result
        if isinstance(node, ast.Lambda):
            return self.define_function(node, store, frame, "<lambda>")
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            states, errors = self.eval_nodes(node.elts, store, frame)
            kind = type(node).__name__.lower()
            result = list(errors)
            for current, values in states:
                if kind == "set":
                    known: dict[FieldKey, Value] = {}
                    uncertain = False
                    for value in values:
                        constants = value.exact_constants()
                        if constants is None or len(constants) != 1:
                            uncertain = True
                            break
                        known.setdefault(constants[0], value)
                    if uncertain:
                        result.extend(self.unknown(current, node, "set_key_protocol"))
                        continue
                    values = tuple(known.values())
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(current),
                        self.alloc(
                            node,
                            current,
                            frame,
                            kind,
                            dict(enumerate(values)),
                            {"length": len(values)},
                        ),
                    )
                )
            return result
        if isinstance(node, ast.Dict):
            return self.dictionaries.build(node, store, frame)
        if isinstance(node, ast.Attribute):
            result = []
            for flow in self.expr(node.value, store, frame, publish=False):
                if flow.kind != "normal":
                    result.append(flow)
                else:
                    result.extend(
                        self.getattr(require_payload(flow), node.attr, flow.store, frame, node)
                    )
            return result
        if isinstance(node, ast.Subscript):
            states, errors = self.eval_nodes((node.value, node.slice), store, frame)
            result = list(errors)
            for s, (base, key) in states:
                result.extend(self.getitem(base, key, s, frame, node))
            return result
        if isinstance(node, ast.Slice):
            states, errors = self.eval_nodes(
                tuple(x for x in (node.lower, node.upper, node.step) if x is not None), store, frame
            )
            return errors + [
                NormalExprFlow(
                    StateHandle.capture(s),
                    self.alloc(node, s, frame, "slice", dict(enumerate(vals))),
                )
                for s, vals in states
            ]
        if isinstance(node, ast.Call):
            return self.call(node, store, frame, publish=False)
        if isinstance(node, ast.BinOp):
            states, errors = self.eval_nodes((node.left, node.right), store, frame)
            result = list(errors)
            for s, (a, b) in states:
                val, exc = self.ops.scalar_binary(node.op, a, b)
                if not val.is_bottom():
                    result.append(NormalExprFlow(StateHandle.capture(s.copy()), val))
                for name in exc:
                    if name.startswith(("unknown_", "resource_")):
                        result.extend(self.unknown(s, node, name))
                    else:
                        result.append(self.error(s.copy(), name))
            return result
        if isinstance(node, ast.UnaryOp):
            result = []
            if isinstance(node.op, ast.Not):
                for flow in self.expr(node.operand, store, frame, publish=False):
                    if flow.kind != "normal":
                        result.append(flow)
                        continue
                    for tested in self.truth_flows(
                        require_payload(flow), flow.store, frame, node, publish=False
                    ):
                        result.append(
                            replace_flow(
                                tested,
                                value=Value(
                                    bools=frozenset(not x for x in require_payload(tested).bools)
                                ),
                            )
                            if tested.kind == "normal"
                            else tested
                        )
                return result
            for flow in self.expr(node.operand, store, frame, publish=False):
                if flow.kind != "normal":
                    result.append(flow)
                    continue
                value, unary_errors = self.ops.scalar_unary(node.op, require_payload(flow))
                if not value.is_bottom():
                    result.append(NormalExprFlow(StateHandle.capture(flow.store.copy()), value))
                for name in unary_errors:
                    if name.startswith(("unknown_", "resource_")):
                        result.extend(self.unknown(flow.store, node, name))
                    else:
                        result.append(self.error(flow.store.copy(), name))
            return result
        if isinstance(node, ast.Compare):
            return self.compare_expr(node, store, frame)
        if isinstance(node, ast.BoolOp):
            return [flow for flow, _ in self.boolean_paths(node, store, frame)]
        if isinstance(node, ast.IfExp):
            result = []
            for flow in self.condition_flows(node.test, store, frame, publish=False):
                if flow.kind != "normal":
                    result.append(flow)
                    continue
                for tested in self.truth_flows(
                    require_payload(flow), flow.store, frame, node, publish=False
                ):
                    if tested.kind != "normal":
                        result.append(tested)
                        continue
                    for truth, body in ((True, node.body), (False, node.orelse)):
                        if truth in require_payload(tested).bools:
                            refined = self.refine_test(node.test, tested.store.copy(), frame, truth)
                            if refined is not None:
                                result.extend(self.expr(body, refined, frame, publish=False))
            return result
        if isinstance(node, ast.JoinedStr):
            return self.formatted_string(node, store, frame)
        if isinstance(node, ast.YieldFrom):
            return self.continuations.delegate_start(node, store, frame)
        if isinstance(node, ast.Yield):
            return [
                replace_flow(
                    flow,
                    kind="suspend",
                    continuation=Continuation(
                        (),
                        ContinuationId(f"{self.d.current}:{self.d.point(node)}:yield"),
                        self.d.point(node),
                    ),
                )
                if flow.kind == "normal"
                else flow
                for flow in self.expr(node.value, store, frame, publish=False)
            ]
        if isinstance(node, ast.Await):
            result = []
            for flow in self.expr(node.value, store, frame, publish=False):
                if flow.kind != "normal":
                    result.append(flow)
                else:
                    result.extend(
                        self.resume(require_payload(flow), flow.store, frame, node, "await")
                    )
            return result
        if isinstance(node, ast.GeneratorExp):
            return self.continuations.generator_expression(node, store, frame)
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp)):
            return self.comprehension(node, store, frame)
        return self.unknown(store, node, "unsupported_expression:" + type(node).__name__)

    def formatted_string(self, node: ast.JoinedStr, store: Store, frame: Frame) -> list[Flow]:
        states: list[tuple[Store, Value]] = [(store.copy(), Value.constant(""))]
        errors: list[Flow] = []
        for part in node.values:
            following: list[tuple[Store, Value]] = []
            for current, prefix in states:
                expression = part.value if isinstance(part, ast.FormattedValue) else part
                for value in self.expr(expression, current, frame, publish=False):
                    if value.kind != "normal":
                        errors.append(value)
                        continue
                    if isinstance(part, ast.FormattedValue):
                        formats = (
                            self.expr(part.format_spec, value.store, frame, publish=False)
                            if part.format_spec is not None
                            else [
                                NormalExprFlow(StateHandle.capture(value.store), Value.constant(""))
                            ]
                        )
                    else:
                        formats = [
                            NormalExprFlow(StateHandle.capture(value.store), Value.constant(""))
                        ]
                    for spec in formats:
                        if spec.kind != "normal":
                            errors.append(spec)
                            continue
                        if (
                            require_payload(value).refs
                            or require_payload(value).top
                            or require_payload(value).top_sorts
                            - {
                                "int",
                                "str",
                                "float",
                                "bool",
                                "none",
                                "bytes",
                                "complex",
                                "ellipsis",
                                "NotImplementedType",
                            }
                        ):
                            for opaque in self.unknown(
                                spec.store, node, "unmodeled_format_protocol"
                            ):
                                if opaque.kind == "normal":
                                    following.append(
                                        (opaque.store, Value(strings=self.ops.string.top()))
                                    )
                                else:
                                    errors.append(opaque)
                            continue
                        prefixes = prefix.exact_constants()
                        constants = require_payload(value).exact_constants()
                        specs = require_payload(spec).exact_constants()
                        if prefixes is None or constants is None or specs is None:
                            self.d.open("abstract_scalar_format", spec.store, node)
                            following.append((spec.store, Value(strings=self.ops.string.top())))
                            # Known builtin scalar formatting is effect-free, but an
                            # abstract format/operand can reject the requested spec.
                            if isinstance(part, ast.FormattedValue):
                                errors.append(self.error(spec.store.copy(), "ValueError"))
                            continue
                        count = len(prefixes) * len(constants) * len(specs)
                        if count > self.ops.policy.max_scalar_pairs:
                            self.d.open("scalar_format_product_budget", spec.store, node)
                            following.append((spec.store, Value(strings=self.ops.string.top())))
                            continue
                        self.d.charge(count, "scalar_format_work")
                        for before, constant, format_spec in product(prefixes, constants, specs):
                            if not isinstance(format_spec, str) or not isinstance(before, str):
                                errors.append(self.error(spec.store.copy(), "TypeError"))
                                continue
                            import re

                            widths = [
                                match.group(0) for match in re.finditer(r"[0-9]+", format_spec)
                            ]
                            if (
                                len(format_spec) > 4096
                                or any(len(width) > 5 or int(width) > 100000 for width in widths)
                                or len(before) > 100000
                                or type(constant) is str
                                and len(constant) > 100000
                            ):
                                self.d.open("scalar_format_size_budget", spec.store, node)
                                following.append(
                                    (spec.store.copy(), Value(strings=self.ops.string.top()))
                                )
                                continue
                            try:
                                if isinstance(part, ast.FormattedValue):
                                    if part.conversion == 115:
                                        operand: JsonScalar = str(constant)
                                    elif part.conversion == 114:
                                        operand = repr(constant)
                                    elif part.conversion == 97:
                                        operand = ascii(constant)
                                    else:
                                        operand = constant
                                    text = format(operand, format_spec)
                                else:
                                    if not isinstance(constant, str):
                                        raise TypeError("A literal f-string part must be text")
                                    text = constant
                                if len(before) + len(text) > 100000:
                                    self.d.open("scalar_format_size_budget", spec.store, node)
                                    answer = Value(strings=self.ops.string.top())
                                else:
                                    answer = Value.constant(before + text)
                                following.append((spec.store.copy(), answer))
                            except (TypeError, ValueError, OverflowError) as error:
                                errors.append(self.error(spec.store.copy(), type(error).__name__))
            states = following
        return errors + [
            NormalExprFlow(StateHandle.capture(current), value) for current, value in states
        ]

    def compare_expr(self, node: ast.Compare, store: Store, frame: Frame) -> list[Flow]:
        result: list[Flow]
        result = []

        def step(position: int, left: Value, current: Store) -> None:
            for right in self.expr(node.comparators[position], current, frame, publish=False):
                if right.kind != "normal":
                    result.append(right)
                    continue
                op = node.ops[position]
                if isinstance(op, (ast.In, ast.NotIn)):
                    tested = self.dictionaries.contains(
                        require_payload(right), left, right.store, frame, node
                    )
                    if isinstance(op, ast.NotIn):
                        tested = [
                            replace_flow(
                                out,
                                value=Value(
                                    bools=frozenset(not x for x in require_payload(out).bools)
                                ),
                            )
                            if out.kind == "normal"
                            else out
                            for out in tested
                        ]
                    if (
                        len(node.ops) == 1
                        and len(require_payload(right).refs) == 1
                        and not (require_payload(right).top or require_payload(right).top_sorts)
                    ):
                        owner = next(iter(require_payload(right).refs))
                        obj = right.store.heap.get(owner)
                        keys = left.exact_constants()
                        if (
                            obj
                            and obj.kind == "dict"
                            and obj.singleton
                            and not obj.havoc
                            and obj.default.is_bottom()
                            and keys is not None
                            and len(keys) == 1
                            and type(keys[0]) is str
                        ):
                            for out in tested:
                                if out.kind == "normal":
                                    out.store.guards["point:" + self.d.point(node)] = (
                                        MembershipGuard(
                                            owner,
                                            keys[0],
                                            not isinstance(op, ast.NotIn),
                                        )
                                    )
                elif (
                    isinstance(op, (ast.Is, ast.IsNot))
                    and left.refs
                    and require_payload(right).refs
                    and not (
                        left.top
                        or left.top_sorts
                        or require_payload(right).top
                        or require_payload(right).top_sorts
                    )
                ):
                    overlap = left.refs & require_payload(right).refs
                    equal = bool(overlap)
                    different = bool(
                        left.refs - require_payload(right).refs
                        or require_payload(right).refs - left.refs
                    ) or any(
                        not right.store.heap.get(r, HeapObject("object", singleton=False)).singleton
                        for r in overlap
                        if not r.startswith("builtin:")
                    )
                    truths = ({True} if equal else set()) | (
                        {False} if different or not equal else set()
                    )
                    if isinstance(op, ast.IsNot):
                        truths = {not value for value in truths}
                    tested = [
                        NormalExprFlow(
                            StateHandle.capture(right.store), Value(bools=frozenset(truths))
                        )
                    ]
                elif (left.refs or require_payload(right).refs) and not isinstance(
                    op, (ast.Is, ast.IsNot)
                ):
                    tested = [
                        replace_flow(out, value=Value(bools=frozenset((False, True))))
                        if out.kind == "normal"
                        else out
                        for out in self.unknown(right.store, node, "unmodeled_comparison_protocol")
                    ]
                else:
                    value, errors = self.ops.scalar_binary(op, left, require_payload(right))
                    tested = []
                    if not value.is_bottom():
                        tested.append(
                            NormalExprFlow(StateHandle.capture(right.store.copy()), value)
                        )
                    for error in errors:
                        if error.startswith(("unknown_", "resource_")):
                            tested.extend(self.unknown(right.store, node, error))
                        else:
                            tested.append(self.error(right.store.copy(), error))
                for out in tested:
                    if out.kind != "normal":
                        result.append(out)
                        continue
                    for truth in self.truth_flows(
                        require_payload(out), out.store, frame, node, publish=False
                    ):
                        if truth.kind != "normal":
                            result.append(truth)
                            continue
                        if False in require_payload(truth).bools:
                            result.append(
                                NormalExprFlow(
                                    StateHandle.capture(truth.store.copy()), Value.constant(False)
                                )
                            )
                        if True in require_payload(truth).bools:
                            if position + 1 == len(node.ops):
                                result.append(
                                    NormalExprFlow(
                                        StateHandle.capture(truth.store.copy()),
                                        Value.constant(True),
                                    )
                                )
                            else:
                                step(position + 1, require_payload(right), truth.store.copy())

        for left in self.expr(node.left, store, frame, publish=False):
            if left.kind != "normal":
                result.append(left)
            else:
                step(0, require_payload(left), left.store)
        return self.d.compact(result, execution=self.execution)

    def boolean_paths(
        self, node: ast.expr, store: Store, frame: Frame, *, conditional: bool = False
    ) -> list[_BooleanPath]:
        if not isinstance(node, ast.BoolOp):
            return [(flow, None) for flow in self.expr(node, store, frame, publish=False)]
        finished: list[_BooleanPath] = []
        continuing = (
            self.boolean_paths(node.values[0], store, frame, conditional=True)
            if conditional
            else [(flow, None) for flow in self.expr(node.values[0], store, frame, publish=False)]
        )
        proceed = isinstance(node.op, ast.And)
        for position, operand in enumerate(node.values[1:]):
            following: list[_BooleanPath] = []
            for flow, known in continuing:
                if flow.kind != "normal":
                    finished.append((flow, None))
                    continue
                tests = (
                    [NormalExprFlow(StateHandle.capture(flow.store), Value(bools=known))]
                    if known is not None
                    else self.truth_flows(
                        require_payload(flow),
                        flow.store,
                        frame,
                        node.values[position],
                        publish=False,
                    )
                )
                for tested in tests:
                    if tested.kind != "normal":
                        finished.append((tested, None))
                        continue
                    if (not proceed) in require_payload(tested).bools:
                        finished.append(
                            (
                                NormalExprFlow(
                                    StateHandle.capture(tested.store.copy()), require_payload(flow)
                                ),
                                frozenset({not proceed}),
                            )
                        )
                    if proceed in require_payload(tested).bools:
                        following.extend(
                            self.boolean_paths(
                                operand, tested.store.copy(), frame, conditional=True
                            )
                            if conditional
                            else [
                                (out, None)
                                for out in self.expr(
                                    operand, tested.store.copy(), frame, publish=False
                                )
                            ]
                        )
            continuing = following
        return finished + continuing

    def condition_flows(
        self, node: ast.expr, store: Store, frame: Frame, *, publish: bool = True
    ) -> list[Flow]:
        result = self._condition_flows_impl(node, store, frame)
        return self.d.compact(result, execution=self.execution) if publish else result

    def _condition_flows_impl(self, node: ast.expr, store: Store, frame: Frame) -> list[Flow]:
        result: list[Flow] = []
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return [
                replace_flow(
                    flow, value=Value(bools=frozenset(not x for x in require_payload(flow).bools))
                )
                if flow.kind == "normal"
                else flow
                for flow in self.condition_flows(node.operand, store, frame, publish=False)
            ]
        for flow, known in self.boolean_paths(node, store, frame, conditional=True):
            if flow.kind != "normal":
                result.append(flow)
            elif known is not None:
                result.append(replace_flow(flow, value=Value(bools=known)))
            else:
                result.extend(
                    self.truth_flows(
                        require_payload(flow),
                        flow.store,
                        frame,
                        node.values[-1] if isinstance(node, ast.BoolOp) else node,
                        publish=False,
                    )
                )
        return result

    def truth(self, value: Value, store: Store) -> frozenset[bool]:
        truths = set(self.ops.truth(replace(value, refs=frozenset())))
        for ref in value.refs:
            obj = store.heap.get(ref)
            if (
                obj
                and obj.kind in ("list", "tuple", "dict", "set", "range")
                and "length" in obj.metadata
                and obj.default.is_bottom()
                and not obj.havoc
            ):
                truths.add(bool(obj.metadata["length"]))
            else:
                truths.add(True)
        return frozenset(truths)

    def truth_flows(
        self,
        value: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        *,
        publish: bool = True,
    ) -> list[Flow]:
        result = self._truth_flows_impl(value, store, frame, node)
        return self.d.compact(result, execution=self.execution) if publish else result

    def _truth_flows_impl(
        self, value: Value, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        result: list[Flow]
        """Execute Python truth protocols with their normal/exceptional effects."""
        result = []
        known_sorts = {
            "int",
            "str",
            "float",
            "bool",
            "none",
            "bytes",
            "complex",
            "ellipsis",
            "NotImplementedType",
        }
        simple = replace(
            value, refs=frozenset(), top=False, top_sorts=value.top_sorts & known_sorts
        )
        truths = self.ops.truth(simple)
        if truths:
            result.append(NormalExprFlow(StateHandle.capture(store.copy()), Value(bools=truths)))
        if value.top or value.top_sorts - known_sorts:
            for flow in self.unknown(store, node, "unknown_truth_protocol"):
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(flow.store), Value(bools=frozenset((False, True)))
                    )
                    if flow.kind == "normal"
                    else flow
                )
        for ref in value.refs:
            obj = store.heap.get(ref)
            if obj and obj.kind == "dict_view":
                for flow in self.dictionary_views.length(obj, store.copy(), frame, node):
                    result.append(
                        replace_flow(flow, value=Value(bools=self.ops.truth(require_payload(flow))))
                        if flow.kind == "normal"
                        else flow
                    )
                continue
            if obj and obj.kind == "instance":
                hook = self.ops.join_all(
                    self.lookup_class(cls, "__bool__", store) for cls in obj.class_ids
                )
                length = False
                if hook.is_bottom():
                    hook = self.ops.join_all(
                        self.lookup_class(cls, "__len__", store) for cls in obj.class_ids
                    )
                    length = True
                if not hook.is_bottom():
                    current = store.copy()
                    bound = self.bound(hook, Value.ref(ref), current, frame, node)
                    for returned in self.invoke(bound, [], (), current, frame, node, publish=False):
                        if returned.kind != "normal":
                            result.append(returned)
                            continue
                        answer = require_payload(returned)
                        if not length:
                            boolean = answer.bools | (
                                frozenset((False, True))
                                if answer.top or "bool" in answer.top_sorts
                                else frozenset()
                            )
                            if boolean:
                                result.append(
                                    NormalExprFlow(
                                        StateHandle.capture(returned.store.copy()),
                                        Value(bools=boolean),
                                    )
                                )
                            remainder = replace(
                                answer,
                                bools=frozenset(),
                                top=False,
                                top_sorts=answer.top_sorts - {"bool"},
                            )
                            if answer.top or not remainder.is_bottom():
                                result.append(self.error(returned.store.copy(), "TypeError"))
                        else:
                            integers = answer.ints
                            if answer.bools:
                                integers = self.ops.integer.join(
                                    integers,
                                    self.ops.join_all(
                                        Value.constant(int(x)) for x in answer.bools
                                    ).ints,
                                )
                            if answer.top or "int" in answer.top_sorts:
                                integers = self.ops.integer.top()
                            if not integers.is_bottom:
                                if integers.lo is None or integers.lo < 0:
                                    result.append(self.error(returned.store.copy(), "ValueError"))
                                allowed = integers.refine_ge(0)
                                if not allowed.is_bottom:
                                    boolean = self.ops.truth(Value(ints=allowed))
                                    if boolean:
                                        result.append(
                                            NormalExprFlow(
                                                StateHandle.capture(returned.store.copy()),
                                                Value(bools=boolean),
                                            )
                                        )
                            remainder = replace(
                                answer,
                                ints=self.ops.integer.bottom(),
                                bools=frozenset(),
                                top=False,
                                top_sorts=answer.top_sorts - {"int", "bool"},
                            )
                            if answer.top or not remainder.is_bottom():
                                result.append(self.error(returned.store.copy(), "TypeError"))
                    continue
            if obj and obj.kind in {"list", "tuple", "dict", "set", "range"}:
                object_length = obj.metadata.lookup("length")
                booleans = (
                    frozenset((bool(object_length),))
                    if type(object_length) is int and not obj.havoc and obj.default.is_bottom()
                    else frozenset((False, True))
                )
                result.append(
                    NormalExprFlow(StateHandle.capture(store.copy()), Value(bools=booleans))
                )
            elif obj is None and not ref.startswith("builtin:"):
                for flow in self.unknown(store, node, "unknown_truth_receiver"):
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(flow.store), Value(bools=frozenset((False, True)))
                        )
                        if flow.kind == "normal"
                        else flow
                    )
            else:
                result.append(
                    NormalExprFlow(StateHandle.capture(store.copy()), Value.constant(True))
                )
        return self.d.compact(result, execution=self.execution)

    def refine_test(self, node: ast.expr, store: Store, frame: Frame, truth: bool) -> Store | None:
        if not self.execution.abstract.get("refine_booleans", True):
            return store
        guard = store.guards.get(
            frame.cell(node.id) if isinstance(node, ast.Name) else "point:" + self.d.point(node)
        )
        if guard is not None:
            owner, key, polarity = guard
            obj = store.heap.get(owner)
            if (
                obj
                and obj.singleton
                and obj.kind == "dict"
                and not obj.havoc
                and obj.default.is_bottom()
            ):
                present = truth == polarity
                if present:
                    if key not in obj.fields:
                        return None
                    obj.maybe_missing.discard(key)
                else:
                    if key in obj.fields and key not in obj.maybe_missing:
                        return None
                    obj.fields.pop(key, None)
                    obj.maybe_missing.add(key)
        if (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.left, ast.Name)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Constant)
        ):
            cell = frame.cell(node.left.id)
            old = store.cells.get(cell, Value(unbound=True))
            new = self.ops.refine(old, node.ops[0], source_constant(node.comparators[0]), truth)
            if new.is_bottom():
                return None
            store.cells[cell] = new
        elif isinstance(node, ast.Name):
            cell = frame.cell(node.id)
            old = store.cells.get(cell, Value(unbound=True))
            constants = old.exact_constants()
            if constants is not None:
                new = self.ops.join_all(Value.constant(x) for x in constants if bool(x) == truth)
                if new.is_bottom():
                    return None
                store.cells[cell] = new
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self.refine_test(node.operand, store, frame, not truth)
        return store

    def assign(
        self, target: ast.expr, value: Value, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        result: list[Flow]
        if isinstance(target, ast.Name):
            self.write_name(target.id, value, store, frame, node)
            return [self.normal(store)]
        if isinstance(target, (ast.Tuple, ast.List)):
            star = next((i for i, x in enumerate(target.elts) if isinstance(x, ast.Starred)), None)
            collections, result = self.continuations.unpack_values(
                value, len(target.elts), star, store, frame, target
            )
            for collection in collections:
                if not collection.complete:
                    pending: list[Flow] = [self.normal(collection.store)]
                    for element in target.elts:
                        pending = [
                            out
                            for flow in pending
                            for out in (
                                self.assign(
                                    element.value if isinstance(element, ast.Starred) else element,
                                    Value.unknown(),
                                    flow.store,
                                    frame,
                                    node,
                                )
                                if flow.kind == "normal"
                                else [flow]
                            )
                        ]
                    result.extend(pending)
                    continue
                sequence = list(collection.values)
                if star is None and len(sequence) != len(target.elts):
                    result.append(self.error(store.copy(), "ValueError"))
                    continue
                if star is not None and len(sequence) < len(target.elts) - 1:
                    result.append(self.error(store.copy(), "ValueError"))
                    continue
                s = collection.store.copy()
                pairs: list[tuple[ast.expr, Value]] = []
                if star is None:
                    pairs = list(zip(target.elts, sequence, strict=False))
                else:
                    tail = len(target.elts) - star - 1
                    middle = sequence[star : len(sequence) - tail if tail else None]
                    rest_value = self.alloc(
                        target,
                        s,
                        frame,
                        "list",
                        dict(enumerate(middle)),
                        {"length": len(middle)},
                    )
                    starred_target = target.elts[star]
                    assert isinstance(starred_target, ast.Starred)
                    pairs = (
                        list(zip(target.elts[:star], sequence[:star], strict=False))
                        + [(starred_target.value, rest_value)]
                        + list(
                            zip(
                                target.elts[star + 1 :],
                                sequence[len(sequence) - tail :] if tail else [],
                                strict=False,
                            )
                        )
                    )
                flows: list[Flow] = [self.normal(s)]
                for item, v in pairs:
                    flows = [
                        out
                        for flow in flows
                        for out in (
                            self.assign(item, v, flow.store, frame, node)
                            if flow.kind == "normal"
                            else [flow]
                        )
                    ]
                result.extend(flows)
            return result
        if isinstance(target, ast.Attribute):
            result = []
            for flow in self.expr(target.value, store, frame, publish=False):
                if flow.kind != "normal":
                    result.append(flow)
                else:
                    result.extend(
                        self.setattr(
                            require_payload(flow), target.attr, value, flow.store, frame, node
                        )
                    )
            return result
        if isinstance(target, ast.Subscript):
            states, errors = self.eval_nodes((target.value, target.slice), store, frame)
            result = list(errors)
            for s, (base, key) in states:
                result.extend(self.setitem(base, key, value, s, frame, node))
            return result
        return self.unknown(store, node, "unsupported_assignment:" + type(target).__name__)

    def statement(
        self, node: ast.stmt, store: Store, frame: Frame, *, publish: bool = True
    ) -> list[Flow]:
        result = [as_statement(flow) for flow in self._statement_impl(node, store, frame)]
        return self.d.compact(result, execution=self.execution) if publish else result

    def _statement_impl(self, node: ast.stmt, store: Store, frame: Frame) -> list[Flow]:
        result: list[Flow]
        self.d.tick(node, execution=self.execution)
        if isinstance(node, ast.Expr):
            return [
                self.continuations.wrap(f, ExprResume(f.continuation))
                if f.kind == "suspend"
                else replace_flow(f, value=Value.constant(None))
                if f.kind == "normal"
                else f
                for f in self.expr(node.value, store, frame, publish=False)
            ]
        if isinstance(node, ast.AnnAssign):
            return self.annotation_statement(node, store, frame)
        if isinstance(node, ast.Assign):
            targets = node.targets
            result = []
            for flow in self.expr(node.value, store, frame, publish=False):
                if flow.kind != "normal":
                    if flow.kind == "suspend":
                        flow = self.continuations.wrap(
                            flow, AssignResume(tuple(targets), flow.continuation)
                        )
                    result.append(flow)
                    continue
                pending: list[Flow] = [flow]
                for target in targets:
                    saved_guard = (
                        flow.store.guards.get(
                            frame.cell(node.value.id)
                            if isinstance(node.value, ast.Name)
                            else "point:" + self.d.point(node.value)
                        )
                        if hasattr(flow.store, "guards")
                        else None
                    )
                    pending = [
                        out
                        for f in pending
                        for out in (
                            self.assign(target, require_payload(flow), f.store, frame, node)
                            if f.kind == "normal"
                            else [f]
                        )
                    ]
                    if isinstance(target, ast.Name) and saved_guard is not None:
                        for out in pending:
                            if out.kind == "normal":
                                out.store.guards[frame.cell(target.id)] = saved_guard
                    incremental = self.d.incremental_oracle
                    if incremental is not None:
                        if isinstance(target, ast.Name) and isinstance(node.value, ast.Name):
                            incremental.register_copy(
                                frame.cell(node.value.id), frame.cell(target.id)
                            )
                        elif (
                            isinstance(target, ast.Name)
                            and isinstance(node.value, ast.Subscript)
                            and isinstance(node.value.value, ast.Name)
                            and isinstance(node.value.slice, ast.Constant)
                        ):
                            incremental.register_load(
                                frame.cell(node.value.value.id),
                                source_constant(node.value.slice),
                                frame.cell(target.id),
                            )
                result.extend(pending)
            return result
        if isinstance(node, ast.AugAssign):
            return self.augmented_assignment(node, store, frame)
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.type_params
        ):
            result = self.unknown(store, node, "pep695_type_parameters")
            for out in result:
                if out.kind == "normal":
                    self.write_name(node.name, Value.unknown(), out.store, frame, node)
            return result
        if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
            result = self.unknown(store, node, "pep695_type_alias")
            for out in result:
                if out.kind == "normal" and isinstance(node.name, ast.Name):
                    self.write_name(node.name.id, Value.unknown(), out.store, frame, node)
            return result
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result = []
            for flow in self.define_function(node, store, frame, node.name):
                if flow.kind == "normal":
                    self.write_name(node.name, require_payload(flow), flow.store, frame, node)
                result.append(self.normal(flow.store) if flow.kind == "normal" else flow)
            return result
        if isinstance(node, ast.ClassDef):
            return self.define_class(node, store, frame)
        if isinstance(node, ast.Return):
            return [
                self.continuations.wrap(f, ReturnResume(f.continuation))
                if f.kind == "suspend"
                else replace_flow(f, kind="return")
                if f.kind == "normal"
                else f
                for f in self.expr(node.value, store, frame, publish=False)
            ]
        if isinstance(node, ast.Raise):
            if node.cause is not None:
                return [
                    RaiseFlow(
                        StateHandle.capture(flow.store),
                        ExceptionValue(Value.unknown(), "AnyException"),
                    )
                    if flow.kind == "normal"
                    else flow
                    for flow in self.unknown(store, node, "exception_cause_protocol")
                ]
            if node.exc is None:
                value = store.cells.get(CellId("$active_exception"), Value.bottom())
                type_value = store.cells.get(CellId("$active_exception_type"), Value.bottom())
                kinds = type_value.exact_constants()
                if value.is_bottom():
                    return [self.error(store.copy(), "RuntimeError")]
                if kinds is None or value.unbound or type_value.unbound:
                    self.d.open("abstract_active_exception", store, node)
                    return [
                        RaiseFlow(
                            StateHandle.capture(store.copy()),
                            ExceptionValue(value.without_unbound(), "AnyException"),
                        ),
                        self.error(store.copy(), "RuntimeError"),
                    ]
                if not kinds:
                    return [self.error(store.copy(), "RuntimeError")]
                return [
                    RaiseFlow(
                        StateHandle.capture(store.copy()),
                        ExceptionValue(value, kind if isinstance(kind, str) else "AnyException"),
                    )
                    for kind in kinds
                ]
            result = []
            for flow in self.expr(node.exc, store, frame, publish=False):
                if flow.kind != "normal":
                    result.append(flow)
                else:
                    result.extend(self.raise_value(require_payload(flow), flow.store, frame, node))
            return result
        if isinstance(node, ast.If):
            result = []
            for flow in self.condition_flows(node.test, store, frame, publish=False):
                if flow.kind != "normal":
                    result.append(flow)
                    continue
                for tested in self.truth_flows(
                    require_payload(flow), flow.store, frame, node, publish=False
                ):
                    if tested.kind != "normal":
                        result.append(tested)
                        continue
                    for truth, body in ((True, node.body), (False, node.orelse)):
                        if truth in require_payload(tested).bools:
                            current = self.refine_test(node.test, tested.store.copy(), frame, truth)
                            if current is not None:
                                result.extend(self.block(body, [self.normal(current)], frame))
            return result
        if isinstance(node, ast.While):
            return self.while_loop(node, store, frame)
        if isinstance(node, (ast.For, ast.AsyncFor)):
            return self.for_loop(node, store, frame)
        if isinstance(node, ast.Break):
            return [BreakFlow(StateHandle.capture(store.copy()))]
        if isinstance(node, ast.Continue):
            return [ContinueFlow(StateHandle.capture(store.copy()))]
        if isinstance(node, ast.Try):
            return self.try_statement(node, store, frame)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            return self.with_statement(node, store, frame)
        if isinstance(node, ast.Assert):
            result = []
            for flow in self.condition_flows(node.test, store, frame, publish=False):
                if flow.kind != "normal":
                    result.append(flow)
                    continue
                for tested in self.truth_flows(
                    require_payload(flow), flow.store, frame, node, publish=False
                ):
                    if tested.kind != "normal":
                        result.append(tested)
                        continue
                    if True in require_payload(tested).bools:
                        current = self.refine_test(node.test, tested.store.copy(), frame, True)
                        if current is not None:
                            result.append(self.normal(current))
                    if False in require_payload(tested).bools:
                        messages = (
                            self.expr(node.msg, tested.store.copy(), frame, publish=False)
                            if node.msg is not None
                            else [self.normal(tested.store.copy())]
                        )
                        for message in messages:
                            result.append(
                                self.error(message.store, "AssertionError")
                                if message.kind == "normal"
                                else message
                            )
            return result
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return self.import_statement(node, store, frame)
        if isinstance(node, ast.Delete):
            result = [self.normal(store.copy())]
            for target in node.targets:
                result = [
                    successor
                    for flow in result
                    for successor in (
                        self.delete_target(target, flow.store, frame, node)
                        if flow.kind == "normal"
                        else [flow]
                    )
                ]
            return result
        if isinstance(node, (ast.Pass, ast.Global, ast.Nonlocal)):
            return [self.normal(store.copy())]
        return self.unknown(store, node, "unsupported_statement:" + type(node).__name__)

    def delete_target(
        self, target: ast.expr, store: Store, frame: Frame, node: ast.AST
    ) -> list[Flow]:
        """Delete the selected binding without invoking read-name fallbacks."""
        self.sync(store)
        if isinstance(target, ast.Name):
            binding = frame.binding(target.id)
            value = store.effective_cell(binding.cell)
            result: list[Flow] = []
            if not value.without_unbound().is_bottom():
                deleted = store.copy()
                self.write_name(target.id, Value(unbound=True), deleted, frame, node)
                result.append(self.normal(deleted))
            if value.unbound:
                exception = (
                    "UnboundLocalError"
                    if binding.scope == "local" and not frame.is_class
                    else "NameError"
                )
                result.append(self.error(store.copy(), exception))
            return result
        if isinstance(target, (ast.Tuple, ast.List)):
            result = [self.normal(store.copy())]
            for element in target.elts:
                result = [
                    successor
                    for flow in result
                    for successor in (
                        self.delete_target(element, flow.store, frame, node)
                        if flow.kind == "normal"
                        else [flow]
                    )
                ]
            return result
        if isinstance(target, ast.Attribute):
            return [
                successor
                for base in self.expr(target.value, store, frame, publish=False)
                for successor in (
                    self.delattr(require_payload(base), target.attr, base.store, frame, node)
                    if base.kind == "normal"
                    else [base]
                )
            ]
        return self.unknown(store, node, "unsupported_delete")

    def augmented_assignment(self, node: ast.AugAssign, store: Store, frame: Frame) -> list[Flow]:
        result: list[Flow]
        locations: list[tuple[Store, Value, _AugmentedLocation]] = []
        result = []
        target = node.target
        if isinstance(target, ast.Name):
            for out in self.read_name(target.id, store, frame, node):
                if out.kind == "normal":
                    locations.append((out.store, require_payload(out), _NameLocation(target)))
                else:
                    result.append(out)
        elif isinstance(target, ast.Attribute):
            for base_flow in self.expr(target.value, store, frame, publish=False):
                if base_flow.kind != "normal":
                    result.append(base_flow)
                    continue
                for out in self.getattr(
                    require_payload(base_flow), target.attr, base_flow.store, frame, node
                ):
                    if out.kind == "normal":
                        locations.append(
                            (
                                out.store,
                                require_payload(out),
                                _AttributeLocation(require_payload(base_flow), target.attr),
                            )
                        )
                    else:
                        result.append(out)
        elif isinstance(target, ast.Subscript):
            evaluated, errors = self.eval_nodes((target.value, target.slice), store, frame)
            result.extend(errors)
            for current, (base, key) in evaluated:
                for out in self.getitem(base, key, current, frame, node):
                    if out.kind == "normal":
                        locations.append(
                            (out.store, require_payload(out), _ItemLocation(base, key))
                        )
                    else:
                        result.append(out)
        else:
            assert_never(target)

        def write(current: Store, value: Value, location: _AugmentedLocation) -> list[Flow]:
            match location:
                case _NameLocation(target):
                    return self.assign(target, value, current, frame, node)
                case _AttributeLocation(receiver, name):
                    return self.setattr(receiver, name, value, current, frame, node)
                case _ItemLocation(container, key):
                    return self.setitem(container, key, value, current, frame, node)
                case _:
                    assert_never(location)

        for current, old, location in locations:
            for right in self.expr(node.value, current, frame, publish=False):
                if right.kind != "normal":
                    result.append(right)
                    continue
                value, scalar_errors = self.ops.scalar_binary(node.op, old, require_payload(right))
                if not value.is_bottom():
                    result.extend(write(right.store.copy(), value, location))
                for error in scalar_errors:
                    if error.startswith(("unknown_", "resource_")):
                        for opaque in self.unknown(right.store, node, error):
                            if opaque.kind == "normal":
                                result.extend(write(opaque.store, Value.unknown(), location))
                            else:
                                result.append(opaque)
                    else:
                        result.append(self.error(right.store.copy(), error))
        return result

    def annotation_statement(self, node: ast.AnnAssign, store: Store, frame: Frame) -> list[Flow]:
        result: list[Flow]
        initial: list[Flow]
        annotations: list[Flow]
        result = []
        if node.value is not None:
            initial = []
            for evaluated in self.expr(node.value, store, frame, publish=False):
                if evaluated.kind == "suspend":
                    initial.append(
                        self.continuations.wrap(
                            evaluated, AssignResume((node.target,), evaluated.continuation)
                        )
                    )
                elif evaluated.kind != "normal":
                    initial.append(evaluated)
                else:
                    initial.extend(
                        self.assign(
                            node.target, require_payload(evaluated), evaluated.store, frame, node
                        )
                    )
        elif isinstance(node.target, ast.Name):
            initial = [self.normal(store.copy())]
        elif isinstance(node.target, (ast.Attribute, ast.Subscript)):
            initial = self.expr(node.target.value, store, frame, publish=False)
        else:
            assert_never(node.target)
        for current in initial:
            if current.kind != "normal":
                result.append(current)
                continue
            if not (frame.is_module or frame.is_class):
                result.append(self.normal(current.store))
                continue
            if frame.module in self.d.future_annotations:
                annotations = (
                    [
                        NormalExprFlow(
                            StateHandle.capture(current.store),
                            Value.constant(
                                source_annotation_text(node.annotation)
                                or ast.unparse(node.annotation)
                            ),
                        )
                    ]
                    if isinstance(node.target, ast.Name) and node.simple
                    else [self.normal(current.store)]
                )
            else:
                annotations = self.expr(node.annotation, current.store, frame, publish=False)
            for annotation in annotations:
                if annotation.kind != "normal":
                    result.append(annotation)
                    continue
                if isinstance(node.target, ast.Name) and node.simple:
                    for namespace in self.read_name(
                        "__annotations__", annotation.store, frame, node
                    ):
                        if namespace.kind != "normal":
                            result.append(namespace)
                        else:
                            result.extend(
                                self.setitem(
                                    require_payload(namespace),
                                    Value.constant(node.target.id),
                                    require_payload(annotation),
                                    namespace.store,
                                    frame,
                                    node,
                                )
                            )
                else:
                    result.append(self.normal(annotation.store))
        return result

    def define_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
        store: Store,
        frame: Frame,
        name: str,
    ) -> list[Flow]:
        result: list[Flow]
        decorators = (
            node.decorator_list if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else ()
        )
        decstates, errors = self.eval_nodes(decorators, store, frame)
        result = list(errors)
        for ds, decvalues in decstates:
            annotations = [
                (arg.arg, arg.annotation)
                for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                if arg.annotation is not None
            ]
            if node.args.vararg and node.args.vararg.annotation is not None:
                annotations.append((node.args.vararg.arg, node.args.vararg.annotation))
            if node.args.kwarg and node.args.kwarg.annotation is not None:
                annotations.append((node.args.kwarg.arg, node.args.kwarg.annotation))
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.returns is not None
            ):
                annotations.append(("return", node.returns))
            defaults_count = len(node.args.defaults) + sum(
                x is not None for x in node.args.kw_defaults
            )
            values = [*node.args.defaults, *[x for x in node.args.kw_defaults if x is not None]]
            if frame.module not in self.d.future_annotations:
                values.extend(annotation for _, annotation in annotations)
            states, ex = self.eval_nodes(values, ds, frame)
            result.extend(ex)
            for s, defaults in states:
                count = len(node.args.defaults)
                kw_defaults = []
                index = count
                for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False):
                    if default is not None:
                        kw_defaults.append((arg.arg, defaults[index]))
                        index += 1
                locals_, globals_, nonlocals_, has_yield = classify_bindings(node)
                identifier = ObjectId(f"function:{self.d.point(node)}@{'|'.join(frame.context)}")
                closure = dict(frame.closure)
                if not frame.is_module and not frame.is_class:
                    closure.update((n, frame.cell(n)) for n in frame.locals)
                if frame.is_class:
                    classcell = CellId(f"classcell:{frame.identity}")
                    s.cells[classcell] = Value.ref(ObjectId(frame.identity))
                    closure["__class__"] = classcell
                display_name = source_declared_name(node) or name
                qualname = (
                    (frame.module + "." + display_name)
                    if frame.is_module
                    else frame.qualname + "." + display_name
                )
                info = FunctionInfo(
                    identifier,
                    node,
                    frame.module,
                    qualname,
                    tuple(sorted(closure.items())),
                    tuple(defaults[:count]),
                    tuple(kw_defaults),
                    locals_,
                    globals_,
                    nonlocals_,
                    has_yield,
                    isinstance(node, ast.AsyncFunctionDef),
                )
                self.d.functions[identifier] = info
                function_fields: dict[FieldKey, Value] = {
                    positional_default_slot(i): value for i, value in enumerate(info.defaults)
                }
                function_fields.update(
                    (keyword_default_slot(key), value) for key, value in info.kw_defaults
                )
                annotation_fields: dict[FieldKey, Value] = (
                    {
                        key: Value.constant(
                            source_annotation_text(annotation) or ast.unparse(annotation)
                        )
                        for key, annotation in annotations
                    }
                    if frame.module in self.d.future_annotations
                    else {
                        key: value
                        for (key, _), value in zip(
                            annotations, defaults[defaults_count:], strict=False
                        )
                    }
                )
                function_fields[function_annotations_slot()] = self.alloc(
                    node, s, frame, "dict", annotation_fields, {"length": len(annotation_fields)}
                )
                python_qualname = (
                    display_name
                    if frame.is_module
                    else frame.qualname.removeprefix(frame.module + ".")
                    + ("." if frame.is_class else ".<locals>.")
                    + display_name
                )
                created = HeapObject(
                    "function",
                    function_fields,
                    metadata={
                        "qualname": qualname,
                        "python_qualname": python_qualname,
                        "name": display_name,
                        "module": frame.module,
                        "default_count": len(info.defaults),
                        "closure": info.closure,
                    },
                )
                if identifier in s.heap:
                    created = s.heap[identifier].join(created, self.ops)
                    created.singleton = False
                s.heap[identifier] = created
                flows: list[Flow] = [NormalExprFlow(StateHandle.capture(s), Value.ref(identifier))]
                for decorator in reversed(decvalues):
                    flows = [
                        out
                        for flow in flows
                        for out in (
                            self.invoke(
                                decorator,
                                [require_payload(flow)],
                                (),
                                flow.store,
                                frame,
                                node,
                                publish=False,
                            )
                            if flow.kind == "normal"
                            else [flow]
                        )
                    ]
                result.extend(flows)
        return result

    def exception_class_tags(
        self, identifier: ObjectId, store: Store, seen: set[ObjectId] | None = None
    ) -> set[str]:
        seen = set() if seen is None else seen
        if identifier in seen:
            return set()
        seen.add(identifier)
        if identifier.startswith("builtin:"):
            return {
                "builtin:" + require_builtin_exception_name(name)
                for name in builtin_exception_ancestors(identifier.split(":", 1)[1])
            }
        obj = store.heap.get(identifier)
        if obj is None or obj.kind != "class":
            return set()
        if obj.havoc:
            self.d.open("havoc_exception_class", store)
            return set()
        tags: set[str] = {identifier}
        for base in obj.metadata.lookup("bases", ()):
            tags |= self.exception_class_tags(base, store, seen)
        return tags

    def raised_tags(self, flow: Flow) -> set[str] | None:
        if flow.exception == "AnyException":
            return None
        result = set()
        for ref in require_payload(flow).refs:
            obj = flow.store.heap.get(ref)
            if obj:
                if obj.havoc or any(
                    flow.store.heap.get(cls) and flow.store.heap[cls].havoc for cls in obj.class_ids
                ):
                    return None
                for cls in obj.class_ids:
                    result |= self.exception_class_tags(cls, flow.store)
                for name in obj.metadata.lookup("exception_types", ()):
                    if name.startswith(("builtin:", "class:")):
                        result.add(name)
                    else:
                        result.update(
                            "builtin:" + require_builtin_exception_name(x)
                            for x in builtin_exception_ancestors(name)
                        )
        if not result:
            result.update(
                "builtin:" + require_builtin_exception_name(x)
                for x in builtin_exception_ancestors(flow.exception)
            )
        return result

    def handler_tags(self, value: Value, store: Store) -> tuple[set[str], bool, bool]:
        tags = set()
        uncertain = value.top or bool(value.top_sorts)
        invalid = bool(
            value.bools
            or value.none
            or value.floats
            or not value.ints.is_bottom
            or not value.strings.is_bottom()
        )
        for ref in value.refs:
            if ref.startswith("builtin:") and ref.split(":", 1)[1] in EXCEPTION_NAMES:
                tags.add("builtin:" + require_builtin_exception_name(ref.split(":", 1)[1]))
                continue
            obj = store.heap.get(ref)
            if obj and obj.havoc:
                uncertain = True
                continue
            if (
                obj
                and obj.kind == "class"
                and "builtin:BaseException" in self.exception_class_tags(ref, store)
            ):
                tags.add(ref)
            elif obj and obj.kind == "tuple":
                for item in obj.fields.values():
                    nested, unknown, bad = self.handler_tags(item, store)
                    tags |= nested
                    uncertain |= unknown
                    invalid |= bad
                if not obj.default.is_bottom():
                    uncertain = True
            else:
                invalid = True
        return tags, uncertain, invalid

    def raise_value(
        self,
        value: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        constructed: bool = False,
    ) -> list[Flow]:
        result: list[Flow]
        result = []
        if value.top or value.top_sorts:
            for flow in self.unknown(store, node, "unknown_raised_object"):
                if flow.kind == "normal":
                    result.append(
                        RaiseFlow(
                            StateHandle.capture(flow.store),
                            ExceptionValue(Value.unknown(), "AnyException"),
                        )
                    )
                else:
                    result.append(flow)
        for ref in value.refs:
            obj = store.heap.get(ref)
            if obj and (
                obj.havoc
                or any(store.heap.get(cls) and store.heap[cls].havoc for cls in obj.class_ids)
            ):
                for opaque in self.unknown(store, node, "havoc_raised_class_relation"):
                    result.append(
                        replace_flow(
                            opaque, kind="raise", value=Value.unknown(), exception="AnyException"
                        )
                    )
                continue
            is_class = (
                (ref.startswith("builtin:") and ref.split(":", 1)[1] in EXCEPTION_NAMES)
                or obj
                and obj.kind == "class"
            )
            if is_class:
                if constructed or "builtin:BaseException" not in self.exception_class_tags(
                    ref, store
                ):
                    result.append(self.error(store.copy(), "TypeError"))
                    continue
                for made in self.invoke(
                    Value.ref(ref), [], (), store.copy(), frame, node, publish=False
                ):
                    if made.kind == "normal":
                        result.extend(
                            self.raise_value(require_payload(made), made.store, frame, node, True)
                        )
                    else:
                        result.append(made)
                continue
            if obj:
                tags = (
                    set().union(*(self.exception_class_tags(cls, store) for cls in obj.class_ids))
                    if obj.class_ids
                    else set()
                )
                if obj.kind == "exception" or "builtin:BaseException" in tags:
                    name = obj.metadata.lookup("exception_name")
                    if name is None:
                        name = next(
                            (
                                store.heap[cls].metadata.lookup("name", "BaseException")
                                for cls in obj.class_ids
                                if cls in store.heap
                            ),
                            "BaseException",
                        )
                    self.d.exceptions.add(name)
                    result.append(
                        RaiseFlow(
                            StateHandle.capture(store.copy()), ExceptionValue(Value.ref(ref), name)
                        )
                    )
                    continue
            result.append(self.error(store.copy(), "TypeError"))
        scalars = replace(value, refs=frozenset(), top=False, top_sorts=frozenset())
        if not scalars.is_bottom():
            result.append(self.error(store.copy(), "TypeError"))
        return result

    def exception_context(self, store: Store) -> ExceptionContext:
        return ExceptionContext(
            store.cells.get(CellId("$active_exception"), Value.bottom()),
            store.cells.get(CellId("$active_exception_type"), Value.bottom()),
            store.cells.get(CellId("$active_exception_owner"), Value.bottom()),
        )

    def install_exception_context(self, store: Store, context: ExceptionContext) -> None:
        bindings = (
            (CellId("$active_exception"), context.value),
            (CellId("$active_exception_type"), context.type_value),
            (CellId("$active_exception_owner"), context.owner),
        )
        for name, value in bindings:
            value = self.ops.normalize(value)
            if value.is_bottom():
                store.cells.pop(name, None)
            else:
                store.cells[name] = value

    def _active_exception(
        self, store: Store, raised: Flow, owner: FrameId | None = None
    ) -> ExceptionRestoreRecord:
        previous = ExceptionRestoreRecord(self.exception_context(store), owner)
        store.cells[CellId("$active_exception")] = require_payload(raised)
        store.cells[CellId("$active_exception_type")] = Value.constant(
            raised.exception or "AnyException"
        )
        store.cells[CellId("$active_exception_owner")] = (
            Value.constant(owner) if owner is not None else Value.bottom()
        )
        return previous

    def _restore_exception(self, store: Store, previous: ExceptionRestoreRecord) -> None:
        context = previous.previous
        stack = self.continuations.exception_contexts
        if previous.restoring_frame is not None and stack:
            resume_context = stack[-1]
            owner = previous.previous.owner.exact_constants()
            # A previous context outside this generator is a dynamic caller
            # link, not a snapshot of the caller that happened to prime it.
            if previous.restoring_frame == resume_context.generator_frame and owner != [
                resume_context.generator_frame
            ]:
                context = resume_context.caller_exception
        self.install_exception_context(store, context)

    def try_statement(self, node: ast.Try | ast.TryStar, store: Store, frame: Frame) -> list[Flow]:
        return self.continuations.try_statement(node, store, frame)

    def while_loop(self, node: ast.While, store: Store, frame: Frame) -> list[Flow]:
        initial = store.copy()
        invariant = initial.copy()
        delay = self.execution.integer("widen_delay", 2)
        limit = self.execution.integer("max_stmt_visits", 100)

        def step(head: Store) -> tuple[list[Store], list[Flow], list[Flow]]:
            back: list[Store] = []
            exits: list[Flow] = []
            escapes: list[Flow] = []
            for tested in self.condition_flows(node.test, head.copy(), frame, publish=False):
                if tested.kind != "normal":
                    escapes.append(tested)
                    continue
                for truth_flow in self.truth_flows(
                    require_payload(tested), tested.store, frame, node, publish=False
                ):
                    if truth_flow.kind != "normal":
                        escapes.append(truth_flow)
                        continue
                    if False in require_payload(truth_flow).bools:
                        s = self.refine_test(node.test, truth_flow.store.copy(), frame, False)
                        if s is not None:
                            exits.extend(self.block(node.orelse, [self.normal(s)], frame))
                    if True in require_payload(truth_flow).bools:
                        s = self.refine_test(node.test, truth_flow.store.copy(), frame, True)
                        if s is not None:
                            for out in self.block(node.body, [self.normal(s)], frame):
                                if out.kind in ("normal", "continue"):
                                    back.append(out.store)
                                elif out.kind == "break":
                                    exits.append(self.normal(out.store))
                                elif out.kind == "suspend":
                                    escapes.append(
                                        self.continuations.wrap(
                                            out, WhileResume(node, out.continuation)
                                        )
                                    )
                                else:
                                    escapes.append(out)
            return back, exits, escapes

        stable = False
        exits: list[Flow] = []
        escapes: list[Flow] = []
        for iteration in range(limit):
            self.d.increment_stat("loop_iterations", 1)
            back, exits, escapes = step(invariant)
            candidate = initial.copy()
            for s in back:
                candidate = candidate.join(s, self.ops)
            if candidate.leq(invariant, self.ops):
                stable = True
                break
            invariant = invariant.join(candidate, self.ops, widen=iteration >= delay)
        if not stable:
            self.d.open("loop_budget", invariant, node)
            return self.d.compact(
                exits + escapes + [self.normal(self.havoc_loop_names(node, invariant, frame))],
                execution=self.execution,
            )
        if self.execution.abstract.get("enable_narrowing", True):
            for _ in range(self.execution.integer("narrowing_iterations", 2)):
                back, _, _ = step(invariant)
                candidate = self.loop_narrowing_candidate(initial, back)
                if not candidate.leq(invariant, self.ops):
                    break
                checkback, _, _ = step(candidate)
                check = initial.copy()
                for s in checkback:
                    check = check.join(s, self.ops)
                if check.leq(candidate, self.ops):
                    invariant = candidate
                    self.d.increment_stat("narrowing_checks_passed", 1)
                else:
                    self.d.increment_stat("narrowing_checks_failed", 1)
                    break
        _, exits, escapes = step(invariant)
        return self.d.compact(exits + escapes, execution=self.execution)

    def loop_narrowing_candidate(self, initial: Store, back_states: Sequence[Store]) -> Store:
        candidate = initial.copy()
        for state in back_states:
            candidate = candidate.join(state, self.ops)
        return candidate

    def havoc_loop_names(self, node: ast.AST, store: Store, frame: Frame) -> Store:
        result = store.copy()
        for item in ast.walk(node):
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                result.write_cell(frame.cell(item.id), Value.unknown())
        return result

    def iterable_values(self, value: Value, store: Store) -> tuple[list[list[Value]], bool]:
        sequences = []
        unknown = value.top or bool(value.top_sorts)
        for ref in value.refs:
            obj = store.heap.get(ref)
            if obj and obj.havoc:
                unknown = True
                continue
            if obj and obj.kind in ("list", "tuple", "set", "range") and "length" in obj.metadata:
                sequences.append(
                    [obj.fields.get(i, obj.default) for i in range(obj.metadata["length"])]
                )
            elif obj and obj.kind == "dict" and obj.default.is_bottom() and not obj.havoc:
                if obj.metadata.lookup("summarized_keys"):
                    unknown = True
                else:
                    sequences.append([Value.constant(k) for k in obj.fields])
            else:
                unknown = True
        strs = value.strings.get_consts()
        if strs is not None:
            for s in strs:
                sequences.append([Value.constant(c) for c in s])
        elif not value.strings.is_bottom():
            unknown = True
        return sequences, unknown

    def for_loop(self, node: ast.For | ast.AsyncFor, store: Store, frame: Frame) -> list[Flow]:
        return self.continuations.for_loop(node, store, frame)

    def expand_star(
        self, path: _CallPath, value: Value, frame: Frame, node: ast.AST
    ) -> tuple[list[_CallPath], list[Flow]]:
        paths: list[_CallPath] = []
        errors: list[Flow] = []
        # collect executes iterator protocols, preserving every exceptional prefix.
        for collected in self.continuations.collect(value, path.store, frame, node, "tuple"):
            if collected.kind != "normal":
                errors.append(collected)
                continue
            sequences, broad = self.iterable_values(require_payload(collected), collected.store)
            for sequence in sequences:
                paths.append(
                    replace(
                        path,
                        store=collected.store.copy(),
                        arguments=path.arguments + tuple(sequence),
                        deferred_star=None,
                    )
                )
            if broad:
                self.d.open("unknown_star_arguments", collected.store, node)
                paths.append(
                    replace(path, store=collected.store, uncertain=True, deferred_star=None)
                )
        return paths, errors

    def merge_keyword_pairs(
        self, path: _CallPath, pairs: Sequence[tuple[FieldKey, Value]]
    ) -> tuple[list[_CallPath], list[Flow]]:
        keywords = list(path.keywords)
        present = {name for name, _ in keywords}
        for name, value in pairs:
            if name in present:
                return [], [self.error(path.store.copy(), "TypeError")]
            present.add(name)
            keywords.append((name, value))
        return [replace(path, keywords=tuple(keywords))], []

    def expand_keyword_mapping(
        self, path: _CallPath, value: Value, frame: Frame, node: ast.AST
    ) -> tuple[list[_CallPath], list[Flow]]:
        paths: list[_CallPath] = []
        errors: list[Flow] = []
        value = self.ops.normalize(value)
        if value.top or value.top_sorts:
            for opaque in self.unknown(path.store.copy(), node, "unknown_keyword_mapping"):
                if opaque.kind == "normal":
                    paths.append(replace(path, store=opaque.store, uncertain=True))
                    errors.append(self.error(opaque.store.copy(), "TypeError"))
                else:
                    errors.append(opaque)
        scalar = replace(value, refs=frozenset(), top=False, top_sorts=frozenset())
        if not scalar.is_bottom():
            errors.append(self.error(path.store.copy(), "TypeError"))
        for reference in value.refs:
            obj = path.store.heap.get(reference)
            if obj is not None and obj.kind == "dict" and not obj.havoc:
                merged = [replace(path, store=path.store.copy())]
                for field_key, item_value in obj.fields.items():
                    dict_following: list[_CallPath] = []
                    for item in merged:
                        present, failed = self.merge_keyword_pairs(item, ((field_key, item_value),))
                        dict_following.extend(present)
                        errors.extend(failed)
                        if field_key in obj.maybe_missing:
                            dict_following.append(replace(item, store=item.store.copy()))
                    merged = dict_following
                if not obj.default.is_bottom() or obj.metadata.lookup("summarized_keys"):
                    self.d.open("unknown_keyword_arguments", path.store, node)
                    merged = [replace(item, uncertain=True) for item in merged]
                    errors.append(self.error(path.store.copy(), "TypeError"))
                paths.extend(merged)
                continue
            # The general mapping protocol obtains keys, exhausts that iterable,
            # then checks each duplicate before fetching its corresponding value.
            mapping = Value.ref(reference)
            for keys in self.getattr(mapping, "keys", path.store.copy(), frame, node):
                if keys.kind != "normal":
                    errors.append(
                        self.error(keys.store, "TypeError")
                        if keys.kind == "raise" and keys.exception == "AttributeError"
                        else keys
                    )
                    continue
                for called in self.invoke(
                    require_payload(keys), (), (), keys.store, frame, node, publish=False
                ):
                    if called.kind != "normal":
                        errors.append(called)
                        continue
                    collections, failed = self.continuations.collect_values(
                        require_payload(called),
                        called.store,
                        frame,
                        node,
                        role="keyword-keys",
                        hint_mode="iterator",
                        publish_advances=False,
                    )
                    errors.extend(failed)
                    for collected in collections:
                        if not collected.complete:
                            self.d.open("unknown_keyword_keys", collected.store, node)
                            paths.append(
                                replace(path, store=collected.store.copy(), uncertain=True)
                            )
                            errors.append(self.error(collected.store.copy(), "TypeError"))
                            continue
                        pending = [replace(path, store=collected.store.copy())]
                        for position, key in enumerate(collected.values):
                            following: list[_CallPath] = []
                            site = self.continuations.protocol_item_site(
                                node, position, role="keyword-getitem"
                            )
                            for item in pending:
                                constants = key.exact_constants()
                                if constants is None:
                                    for opaque in self.unknown(
                                        item.store, node, "unknown_keyword_key"
                                    ):
                                        if opaque.kind == "normal":
                                            following.append(
                                                replace(item, store=opaque.store, uncertain=True)
                                            )
                                        else:
                                            errors.append(opaque)
                                    continue
                                for constant in constants:
                                    if constant in {name for name, _ in item.keywords}:
                                        errors.append(self.error(item.store.copy(), "TypeError"))
                                        continue
                                    for fetched in self.getitem(
                                        mapping,
                                        Value.constant(constant),
                                        item.store.copy(),
                                        frame,
                                        site,
                                    ):
                                        if fetched.kind != "normal":
                                            errors.append(fetched)
                                            continue
                                        merged, failed = self.merge_keyword_pairs(
                                            replace(item, store=fetched.store),
                                            ((constant, require_payload(fetched)),),
                                        )
                                        following.extend(merged)
                                        errors.extend(failed)
                            pending = following
                        paths.extend(pending)
        return paths, errors

    def call(
        self, node: ast.Call, store: Store, frame: Frame, *, publish: bool = True
    ) -> list[Flow]:
        result = self._call_impl(node, store, frame)
        return self.d.compact(result, execution=self.execution) if publish else result

    def _call_impl(self, node: ast.Call, store: Store, frame: Frame) -> list[Flow]:
        result: list[Flow] = []
        for called in self.expr(node.func, store, frame, publish=False):
            if called.kind != "normal":
                result.append(called)
                continue
            paths = [_CallPath(called.store)]
            for step in call_plan(node):
                following: list[_CallPath] = []
                for path in paths:
                    if isinstance(step, KeywordGroup):
                        states, errors = self.eval_nodes(
                            tuple(keyword.value for keyword in step.keywords), path.store, frame
                        )
                        result.extend(errors)
                        for group_store, values in states:
                            pairs: list[tuple[FieldKey, Value]] = []
                            for keyword, value in zip(step.keywords, values, strict=True):
                                assert keyword.arg is not None
                                pairs.append((keyword.arg, value))
                            merged, errors = self.merge_keyword_pairs(
                                replace(path, store=group_store), pairs
                            )
                            following.extend(merged)
                            result.extend(errors)
                        continue
                    for evaluated in self.expr(step.expression, path.store, frame, publish=False):
                        if evaluated.kind != "normal":
                            result.append(evaluated)
                            continue
                        current = replace(path, store=evaluated.store)
                        value = require_payload(evaluated)
                        if isinstance(step, KeywordMapping):
                            expanded, errors = self.expand_keyword_mapping(
                                current, value, frame, step.expression
                            )
                            following.extend(expanded)
                            result.extend(errors)
                        elif step.deferred:
                            following.append(
                                replace(current, deferred_star=(value, step.expression))
                            )
                        elif step.starred:
                            expanded, errors = self.expand_star(
                                current, value, frame, step.expression
                            )
                            following.extend(expanded)
                            result.extend(errors)
                        else:
                            following.append(replace(current, arguments=path.arguments + (value,)))
                paths = following
            expanded_paths: list[_CallPath] = []
            for path in paths:
                if path.deferred_star is None:
                    expanded_paths.append(path)
                else:
                    expanded, errors = self.expand_star(
                        path, path.deferred_star[0], frame, path.deferred_star[1]
                    )
                    expanded_paths.extend(expanded)
                    result.extend(errors)
            for path in expanded_paths:
                if any(not isinstance(name, str) for name, _ in path.keywords):
                    result.append(self.error(path.store, "TypeError"))
                    continue
                keywords = tuple(
                    (name, value) for name, value in path.keywords if isinstance(name, str)
                )
                callee = self.ops.normalize(require_payload(called))
                if path.uncertain:
                    for opaque in self.unknown(path.store, node, "unknown_argument_shape"):
                        if opaque.kind != "normal":
                            result.append(opaque)
                            continue
                        result.append(self.error(opaque.store.copy(), "TypeError"))
                        for identifier in callee.refs:
                            info = self.d.functions.get(identifier)
                            if info:
                                expected = [
                                    x.arg
                                    for x in (*info.node.args.posonlyargs, *info.node.args.args)
                                ]
                                filled = list(path.arguments) + [
                                    Value.unknown() for _ in expected[len(path.arguments) :]
                                ]
                                kw = list(keywords) + [
                                    (x.arg, Value.unknown())
                                    for x in info.node.args.kwonlyargs
                                    if x.arg not in dict(keywords)
                                ]
                                result.extend(
                                    self.d.request_function(
                                        identifier,
                                        filled,
                                        kw,
                                        opaque.store.copy(),
                                        frame,
                                        node,
                                        execution=self.execution,
                                    )
                                )
                            else:
                                result.extend(
                                    self.unknown(opaque.store, node, "unknown_argument_callee")
                                )
                else:
                    result.extend(
                        self.invoke(
                            callee, path.arguments, keywords, path.store, frame, node, publish=False
                        )
                    )
        return self.d.compact(result, execution=self.execution)

    def invoke(
        self,
        callee: Value,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        *,
        publish: bool = True,
    ) -> list[Flow]:
        result = self._invoke_impl(callee, args, keywords, store, frame, node)
        return self.d.compact(result, execution=self.execution) if publish else result

    def _invoke_impl(
        self,
        callee: Value,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        result: list[Flow]
        self.sync(store)
        if hasattr(self.ops, "normalize"):
            callee = self.ops.normalize(callee)
            args = [self.ops.normalize(value) for value in args]
            keywords = tuple((name, self.ops.normalize(value)) for name, value in keywords)
        result = []
        if callee.top or callee.top_sorts:
            result.extend(self.unknown(store, node, "unknown_call"))
        for identifier in sorted(callee.refs):
            if identifier in self.d.functions:
                returned = self.d.request_function(
                    identifier, args, keywords, store.copy(), frame, node, execution=self.execution
                )
                tokens = frozenset(
                    token
                    for token in callee.relations
                    if token.handler in {identifier, self.d.functions[identifier].qualname}
                )
                result.extend(
                    replace_flow(
                        flow,
                        value=replace(
                            require_payload(flow),
                            relations=require_payload(flow).relations | tokens,
                        ),
                    )
                    for flow in returned
                )
                continue
            if identifier.startswith("builtin:"):
                name = identifier.split(":", 1)[1]
                self.d.calls.add((frame.qualname, "builtins." + name, self.d.point(node)))
                result.extend(
                    self.builtin(name, args, keywords, store.copy(), frame, node, publish=False)
                )
                continue
            obj = store.heap.get(identifier)
            if obj is None:
                result.extend(self.unknown(store, node, "unresolved_callable:" + identifier))
                continue
            if obj.havoc:
                result.extend(self.unknown(store, node, "havoc_callable_state:" + obj.kind))
                continue
            if obj.kind == "bound_method":
                target = obj.metadata["callable"]
                receiver = obj.metadata["receiver"]
                result.extend(
                    self.invoke(
                        target,
                        [receiver, *args],
                        keywords,
                        store.copy(),
                        frame,
                        node,
                        publish=False,
                    )
                )
            elif obj.kind == "builtin_method":
                result.extend(
                    self.builtin_method(
                        obj.metadata["name"],
                        obj.metadata["receiver"],
                        args,
                        keywords,
                        store.copy(),
                        frame,
                        node,
                    )
                )
            elif obj.kind == "class":
                result.extend(self.construct(identifier, args, keywords, store.copy(), frame, node))
            elif obj.kind == "staticmethod":
                result.extend(
                    self.invoke(
                        obj.fields["callable"],
                        args,
                        keywords,
                        store.copy(),
                        frame,
                        node,
                        publish=False,
                    )
                )
            elif obj.kind == "instance":
                for call in self.continuations.special(
                    Value.ref(identifier), "__call__", store.copy(), frame, node, "TypeError"
                ):
                    if call.kind == "normal":
                        result.extend(
                            self.invoke(
                                require_payload(call),
                                args,
                                keywords,
                                call.store,
                                frame,
                                node,
                                publish=False,
                            )
                        )
                    else:
                        result.append(call)
            else:
                result.append(self.error(store.copy(), "TypeError"))
        if not callee.top and not callee.top_sorts and not callee.refs and not callee.is_bottom():
            result.append(self.error(store.copy(), "TypeError"))
        return result

    def lookup_class(
        self, clsid: ObjectId, name: str, store: Store, seen: set[ObjectId] | None = None
    ) -> Value:
        return self.attributes.lookup_class(clsid, name, store, seen)

    def bound(
        self, value: Value, receiver: Value, store: Store, frame: Frame, node: ast.AST | None
    ) -> Value:
        return self.attributes.bound(value, receiver, store, frame, node)

    def getattr(
        self, base: Value, name: str, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        return self.attributes.getattr(base, name, store, frame, node)

    def setattr(
        self, base: Value, name: str, value: Value, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        return self.attributes.setattr(base, name, value, store, frame, node)

    def delattr(
        self, base: Value, name: str, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        return self.attributes.delattr(base, name, store, frame, node)

    def getitem(
        self, base: Value, key: Value, store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        return self.dictionaries.getitem(base, key, store, frame, node)

    def setitem(
        self,
        base: Value,
        key: Value,
        value: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        return self.dictionaries.setitem(base, key, value, store, frame, node)

    def define_class(self, node: ast.ClassDef, store: Store, frame: Frame) -> list[Flow]:
        return self.attributes.define_class(node, store, frame)

    def c3(self, bases: Sequence[ObjectId], store: Store) -> tuple[ObjectId, ...] | None:
        return self.attributes.c3(bases, store)

    def construct(
        self,
        clsid: ObjectId,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        return self.attributes.construct(clsid, args, keywords, store, frame, node)

    def import_statement(
        self, node: ast.Import | ast.ImportFrom, store: Store, frame: Frame
    ) -> list[Flow]:
        result: list[Flow]
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            future_state = store.copy()
            for alias in node.names:
                self.write_name(
                    alias.asname or alias.name,
                    Value.ref(ObjectId("builtin:__future__." + alias.name)),
                    future_state,
                    frame,
                    node,
                )
            return [self.normal(future_state)]
        result = [self.normal(store.copy())]
        if isinstance(node, ast.Import):
            for alias in node.names:
                next_result: list[Flow] = []
                for flow in result:
                    if flow.kind != "normal":
                        next_result.append(flow)
                        continue
                    for imported in self.d.request_module(
                        alias.name, flow.store, self.d.current, node, execution=self.execution
                    ):
                        if imported.kind == "normal":
                            target = alias.asname or alias.name.split(".")[0]
                            if alias.asname is None and "." in alias.name:
                                parent = alias.name.split(".")[0]
                                value = self.d.register_module(
                                    parent, imported.store, execution=self.execution
                                )
                            else:
                                value = require_payload(imported)
                            self.write_name(target, value, imported.store, frame, node)
                            next_result.append(self.normal(imported.store))
                        else:
                            next_result.append(imported)
                result = next_result
            return result
        package = (
            frame.module
            if self.d.modules[frame.module].is_package
            else frame.module.rpartition(".")[0]
        )
        if node.level:
            pieces = package.split(".") if package else []
            if node.level > len(pieces) + 1:
                return [self.error(store.copy(), "ImportError")]
            prefix = ".".join(pieces[: len(pieces) - node.level + 1])
            module = ".".join(x for x in (prefix, node.module or "") if x)
        else:
            module = node.module or ""
        imported_flows = self.d.request_module(
            module, store, self.d.current, node, execution=self.execution
        )
        result = []
        for flow in imported_flows:
            if flow.kind != "normal":
                result.append(flow)
                continue
            paths: list[Flow] = [flow]
            for alias in node.names:
                next_paths: list[Flow] = []
                for p in paths:
                    if p.kind != "normal":
                        next_paths.append(p)
                        continue
                    if alias.name == "*":
                        prefix = f"global:{module}:"
                        for cell, value in list(p.store.cells.items()):
                            if cell.startswith(prefix) and not cell[len(prefix) :].startswith("_"):
                                self.write_name(cell[len(prefix) :], value, p.store, frame, node)
                        next_paths.append(p)
                        continue
                    value = p.store.cells.get(
                        CellId(f"global:{module}:{alias.name}"), Value.bottom()
                    )
                    submodule = module + "." + alias.name
                    if value.is_bottom() and submodule in self.d.modules:
                        candidates = self.d.request_module(
                            submodule, p.store, self.d.current, node, execution=self.execution
                        )
                        for sub in candidates:
                            if sub.kind == "normal":
                                self.write_name(
                                    alias.asname or alias.name,
                                    require_payload(sub),
                                    sub.store,
                                    frame,
                                    node,
                                )
                                next_paths.append(self.normal(sub.store))
                            else:
                                next_paths.append(sub)
                    elif value.is_bottom() and module not in self.d.modules:
                        self.d.open("external_import:" + module + "." + alias.name, p.store, node)
                        self.write_name(
                            alias.asname or alias.name, Value.unknown(), p.store, frame, node
                        )
                        next_paths.append(self.normal(p.store))
                    elif value.is_bottom():
                        next_paths.append(self.error(p.store.copy(), "ImportError"))
                    else:
                        self.write_name(alias.asname or alias.name, value, p.store, frame, node)
                        next_paths.append(self.normal(p.store))
                paths = next_paths
            result.extend(paths)
        return result

    def length_builtin(
        self,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        result: list[Flow]
        if len(args) != 1 or keywords:
            return [self.error(store, "TypeError")]
        value = args[0]
        result = []
        scalar = replace(value, refs=frozenset(), top=False, top_sorts=frozenset())
        constants = scalar.exact_constants()
        if constants is not None:
            for constant in constants:
                try:
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(store.copy()),
                            Value.constant(_scalar_length(constant)),
                        )
                    )
                except TypeError:
                    result.append(self.error(store.copy(), "TypeError"))
        elif not scalar.is_bottom():
            if not scalar.strings.is_bottom():
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(store.copy()), Value(ints=self.ops.integer.top())
                    )
                )
            if not scalar.ints.is_bottom or scalar.floats or scalar.bools or scalar.none:
                result.append(self.error(store.copy(), "TypeError"))
        if value.top or value.top_sorts:
            result.extend(
                replace_flow(out, value=Value(ints=self.ops.integer.top()))
                if out.kind == "normal"
                else out
                for out in self.unknown(store, node, "unknown_length_protocol")
            )
        for ref in value.refs:
            obj = store.heap.get(ref)
            if obj is None or obj.havoc:
                result.extend(
                    replace_flow(out, value=Value(ints=self.ops.integer.top()))
                    if out.kind == "normal"
                    else out
                    for out in self.unknown(store, node, "unknown_length_receiver")
                )
                continue
            if obj.kind == "dict_view":
                result.extend(self.dictionary_views.length(obj, store.copy(), frame, node))
                continue
            if obj.kind in {"list", "tuple", "dict", "set", "range"}:
                if obj.kind == "dict" and obj.metadata.lookup("summarized_keys"):
                    self.d.open("summarized_dictionary_key_cardinality", store, node)
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(store.copy()), Value(ints=self.ops.integer.top())
                        )
                    )
                    continue
                if isinstance(obj.metadata.lookup("length_value"), Value):
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(store.copy()), obj.metadata["length_value"]
                        )
                    )
                    continue
                if obj.kind == "dict":
                    if obj.default.is_bottom() and not obj.maybe_missing:
                        answer = Value.constant(len(obj.fields))
                    else:
                        answer = Value(ints=self.ops.integer.top())
                elif type(obj.metadata.lookup("length")) is int:
                    answer = Value.constant(obj.metadata["length"])
                else:
                    answer = Value(ints=self.ops.integer.top())
                result.append(NormalExprFlow(StateHandle.capture(store.copy()), answer))
                continue
            if obj.kind == "namespace":
                self.d.open("namespace_length_abstraction", store, node)
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(store.copy()), Value(ints=self.ops.integer.top())
                    )
                )
                continue
            if obj.kind != "instance":
                result.append(self.error(store.copy(), "TypeError"))
                continue
            for method in self.continuations.special(
                Value.ref(ref), "__len__", store.copy(), frame, node, "TypeError"
            ):
                if method.kind != "normal":
                    result.append(method)
                    continue
                for returned in self.invoke(
                    require_payload(method), [], (), method.store, frame, node, publish=False
                ):
                    if returned.kind != "normal":
                        result.append(returned)
                        continue
                    answer = require_payload(returned)
                    integers = answer.ints
                    if answer.bools:
                        integers = self.ops.integer.join(
                            integers,
                            self.ops.join_all(Value.constant(int(x)) for x in answer.bools).ints,
                        )
                    if answer.top or "int" in answer.top_sorts:
                        integers = self.ops.integer.top()
                    if not integers.is_bottom:
                        if integers.lo is None or integers.lo < 0:
                            result.append(self.error(returned.store.copy(), "ValueError"))
                        allowed = integers.refine_ge(0)
                        if not allowed.is_bottom:
                            result.append(
                                NormalExprFlow(
                                    StateHandle.capture(returned.store.copy()), Value(ints=allowed)
                                )
                            )
                    remainder = replace(
                        answer,
                        ints=self.ops.integer.bottom(),
                        bools=frozenset(),
                        top=False,
                        top_sorts=answer.top_sorts - {"int"},
                    )
                    if answer.refs:
                        result.extend(
                            replace_flow(out, value=Value(ints=self.ops.integer.top()))
                            if out.kind == "normal"
                            else out
                            for out in self.unknown(
                                returned.store, node, "unmodeled_length_index_protocol"
                            )
                        )
                    elif not remainder.is_bottom() or answer.top:
                        result.append(self.error(returned.store.copy(), "TypeError"))
        return self.d.compact(result, execution=self.execution)

    def conversion_protocol(
        self,
        name: str,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        result: list[Flow]
        result = []
        source = args[0]

        def abstract_result(current: Store, reason: str, havoc: bool = True) -> list[Flow]:
            sort = "str" if name in {"str", "repr"} else "float" if name == "float" else "int"
            return [
                replace_flow(
                    out,
                    value=Value.unknown()
                    if name == "abs"
                    else Value(strings=self.ops.string.top())
                    if sort == "str"
                    else Value(top_sorts=frozenset((sort,))),
                )
                if out.kind == "normal"
                else out
                for out in self.unknown(current, node, reason, havoc=havoc)
            ]

        if source.top or source.top_sorts:
            result.extend(abstract_result(store, "unknown_conversion_protocol:" + name))
        for ref in source.refs:
            obj = store.heap.get(ref)
            if obj is None or obj.havoc or obj.kind != "instance" or len(args) != 1 or keywords:
                result.extend(abstract_result(store, "unmodeled_conversion_protocol:" + name))
                continue
            names = {
                "str": ("__str__", "__repr__"),
                "repr": ("__repr__",),
                "int": ("__int__", "__index__"),
                "float": ("__float__", "__index__"),
                "abs": ("__abs__",),
            }[name]
            selected = None
            hook = Value.bottom()
            for protocol in names:
                hook = self.ops.join_all(
                    self.lookup_class(cls, protocol, store) for cls in obj.class_ids
                )
                if not hook.is_bottom():
                    selected = protocol
                    break
            if selected is None:
                if name in {"str", "repr"}:
                    self.d.open("default_object_representation", store, node)
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(store.copy()), Value(strings=self.ops.string.top())
                        )
                    )
                else:
                    result.append(self.error(store.copy(), "TypeError"))
                continue
            for method in self.attributes._descriptor_get(
                hook, Value.ref(ref), Value(refs=obj.class_ids), store.copy(), frame, node
            ):
                if method.kind != "normal":
                    result.append(method)
                    continue
                for returned in self.invoke(
                    require_payload(method), [], (), method.store, frame, node, publish=False
                ):
                    if returned.kind != "normal" or name == "abs":
                        result.append(returned)
                        continue
                    answer = require_payload(returned)
                    if name in {"str", "repr"}:
                        valid = Value(strings=answer.strings)
                        if answer.top or "str" in answer.top_sorts:
                            valid = self.ops.join(valid, Value(strings=self.ops.string.top()))
                        bad = replace(
                            answer,
                            strings=self.ops.string.bottom(),
                            top=False,
                            top_sorts=answer.top_sorts - {"str"},
                        )
                    elif name == "float" and selected == "__float__":
                        valid = Value(floats=answer.floats, top_sorts=answer.top_sorts & {"float"})
                        if answer.top:
                            valid = self.ops.join(valid, Value(top_sorts=frozenset(("float",))))
                        bad = replace(
                            answer,
                            floats=frozenset(),
                            top=False,
                            top_sorts=answer.top_sorts - {"float"},
                        )
                    else:
                        integers = self.ops.integer.join(
                            answer.ints,
                            self.ops.join_all(Value.constant(int(x)) for x in answer.bools).ints,
                        )
                        if answer.top or "int" in answer.top_sorts:
                            integers = self.ops.integer.top()
                        if name == "float":
                            constants = integers.get_consts()
                            valid = Value.bottom()
                            if constants is None:
                                valid = Value(top_sorts=frozenset(("float",)))
                            else:
                                for integer in constants:
                                    try:
                                        valid = self.ops.join(valid, Value.constant(float(integer)))
                                    except OverflowError:
                                        result.append(
                                            self.error(returned.store.copy(), "OverflowError")
                                        )
                        else:
                            valid = Value(ints=integers)
                        bad = replace(
                            answer,
                            ints=self.ops.integer.bottom(),
                            bools=frozenset(),
                            top=False,
                            top_sorts=answer.top_sorts - {"int"},
                        )
                    if not valid.is_bottom():
                        result.append(
                            NormalExprFlow(StateHandle.capture(returned.store.copy()), valid)
                        )
                    if answer.refs:
                        result.extend(
                            abstract_result(
                                returned.store, "conversion_subclass_validation", havoc=False
                            )
                        )
                    if not bad.is_bottom() or answer.top:
                        result.append(self.error(returned.store.copy(), "TypeError"))
        return self.d.compact(result, execution=self.execution)

    def builtin(
        self,
        name: str,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        *,
        publish: bool = True,
    ) -> list[Flow]:
        result = self._builtin_impl(name, args, keywords, store, frame, node)
        return self.d.compact(result, execution=self.execution) if publish else result

    def _builtin_impl(
        self,
        name: str,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        result: list[Flow]
        self.sync(store)
        if name in {"globals", "locals", "vars"}:
            return self.namespaces.builtin(name, args, keywords, store, frame, node)
        if name in {"eval", "exec", "__import__"}:
            return self.unknown(store, node, "dynamic_code:" + name)
        if name in EXCEPTION_NAMES:
            ancestors = builtin_exception_ancestors(name)
            if ancestors & {"OSError", "SyntaxError"} or name in {
                "UnicodeDecodeError",
                "UnicodeEncodeError",
                "UnicodeTranslateError",
                "ExceptionGroup",
                "BaseExceptionGroup",
            }:
                return self.unknown(store, node, "complex_exception_constructor:" + name)
            allowed = {
                "AttributeError": {"name", "obj"},
                "NameError": {"name"},
                "ImportError": {"name", "path"},
                "ModuleNotFoundError": {"name", "path"},
            }
            if any(key not in allowed.get(name, set()) for key, _ in keywords):
                return [self.error(store, "TypeError")]
            fields: dict[FieldKey, Value] = dict(keywords)
            argtuple = self.alloc(
                node,
                store,
                frame,
                "tuple",
                dict(enumerate(args)),
                {"length": len(args)},
                role="exception-arguments",
            )
            fields["args"] = argtuple
            if name == "StopIteration":
                fields["value"] = args[0] if args else Value.constant(None)
            return [
                NormalExprFlow(
                    StateHandle.capture(store),
                    self.alloc(
                        node,
                        store,
                        frame,
                        "exception",
                        fields,
                        {
                            "exception_name": require_builtin_exception_name(name),
                            "exception_types": tuple(builtin_exception_ancestors(name)),
                        },
                    ),
                )
            ]
        if name == "print":
            self.d.events.append(("call", "print"))
            flows: list[Flow] = [self.normal(store)]
            if any(key not in {"sep", "end", "file", "flush"} for key, _ in keywords):
                return [self.error(store, "TypeError")]
            for key, value in keywords:
                if key not in {"sep", "end"}:
                    continue
                constants = value.exact_constants()
                if constants is None:
                    return [
                        replace_flow(out, value=Value.constant(None))
                        if out.kind == "normal"
                        else out
                        for out in self.unknown(
                            store, node, "abstract_print_separator", havoc=False
                        )
                    ]
                if any(item is not None and type(item) is not str for item in constants):
                    if all(item is not None and type(item) is not str for item in constants):
                        return [self.error(store, "TypeError")]
                    flows.append(self.error(store.copy(), "TypeError"))
            if any(
                key == "file"
                and value.exact_constants() != [None]
                or key == "flush"
                and (value.top or value.refs)
                for key, value in keywords
            ):
                return [
                    replace_flow(out, value=Value.constant(None)) if out.kind == "normal" else out
                    for out in self.unknown(store, node, "print_stream_protocol")
                ]
            for argument in args:
                if not (argument.top or argument.refs):
                    continue
                flows = [
                    out
                    for flow in flows
                    for out in (
                        self.conversion_protocol("str", [argument], (), flow.store, frame, node)
                        if flow.kind == "normal"
                        else [flow]
                    )
                ]
            return [self.normal(flow.store) if flow.kind == "normal" else flow for flow in flows]
        if name in {"staticmethod", "classmethod"}:
            if len(args) != 1 or keywords:
                return [self.error(store, "TypeError")]
            return [
                NormalExprFlow(
                    StateHandle.capture(store),
                    self.alloc(node, store, frame, name, {"callable": args[0]}),
                )
            ]
        if name == "property":
            names = ("fget", "fset", "fdel", "doc")
            if len(args) > 4 or any(
                key not in names or key in names[: len(args)] for key, _ in keywords
            ):
                return [self.error(store, "TypeError")]
            fields = dict(zip(names, args, strict=False))
            fields.update(dict(keywords))
            return [
                NormalExprFlow(
                    StateHandle.capture(store), self.alloc(node, store, frame, "property", fields)
                )
            ]
        if name == "object":
            return (
                [self.error(store, "TypeError")]
                if args or keywords
                else [
                    NormalExprFlow(
                        StateHandle.capture(store), self.alloc(node, store, frame, "instance")
                    )
                ]
            )
        if name in {"list", "tuple", "set", "dict"}:
            if name in {"dict", "set"}:
                from pythonstan.analysis.semantics.containers import ContainerConstruction

                return ContainerConstruction(self).construct(
                    name, args, keywords, store, frame, node
                )
            if len(args) > 1:
                return [self.error(store, "TypeError")]
            if keywords:
                return [self.error(store, "TypeError")]
            if not args:
                return [
                    NormalExprFlow(
                        StateHandle.capture(store),
                        self.alloc(node, store, frame, name, {}, {"length": 0}),
                    )
                ]
            result = []
            operand = args[0]
            if name == "tuple":
                existing = frozenset(
                    reference
                    for reference in operand.refs
                    if reference in store.heap and store.heap[reference].kind == "tuple"
                )
                if existing:
                    result.append(self.normal(store.copy(), Value(refs=existing)))
                    operand = replace(operand, refs=operand.refs - existing)
            if not operand.is_bottom():
                result.extend(self.continuations.collect(operand, store, frame, node, name))
            return result
        if name == "range":
            if keywords:
                return [self.error(store, "TypeError")]
            range_options = _constant_options(args)
            result = []
            if not args or len(args) > 3:
                return [self.error(store, "TypeError")]
            if range_options is not None:
                for range_arguments in product(*range_options):
                    try:
                        seq = scalar_range(range_arguments)
                        if len(seq) > self.execution.integer("max_range_size", 1000):
                            return self.unknown(
                                store, node, "range_enumeration_budget", havoc=False
                            )
                        s = store.copy()
                        result.append(
                            NormalExprFlow(
                                StateHandle.capture(s),
                                self.alloc(
                                    node,
                                    s,
                                    frame,
                                    "range",
                                    {i: Value.constant(x) for i, x in enumerate(seq)},
                                    {"length": len(seq)},
                                ),
                            )
                        )
                    except (TypeError, ValueError, OverflowError) as exc:
                        result.append(self.error(store.copy(), type(exc).__name__))
                return result
            return self.unknown(
                store, node, "dynamic_range", havoc=any(value.top or value.refs for value in args)
            )
        if name == "len":
            return self.length_builtin(args, keywords, store, frame, node)
        if name in {"str", "int", "bool", "float", "repr", "abs"}:
            if not args:
                if name in {"repr", "abs"}:
                    return [self.error(store, "TypeError")]
                if keywords:
                    return self.unknown(store, node, "keyword_only_scalar_conversion")
                return [
                    NormalExprFlow(
                        StateHandle.capture(store),
                        Value.constant({"str": "", "int": 0, "bool": False, "float": 0.0}[name]),
                    )
                ]
            if name == "bool":
                return (
                    self.truth_flows(args[0], store, frame, node, publish=False)
                    if len(args) == 1 and not keywords
                    else [self.error(store, "TypeError")]
                )
            if (
                args[0].refs
                or args[0].top
                or args[0].top_sorts
                - {
                    "int",
                    "str",
                    "float",
                    "bool",
                    "none",
                    "bytes",
                    "complex",
                    "ellipsis",
                    "NotImplementedType",
                }
            ):
                return self.conversion_protocol(name, args, keywords, store, frame, node)
            if (
                name == "int"
                and len(args) == 1
                and not keywords
                and not args[0].ints.is_bottom
                and replace(args[0], ints=self.ops.integer.bottom()).is_bottom()
            ):
                return [NormalExprFlow(StateHandle.capture(store), args[0])]
            if (
                name == "float"
                and len(args) == 1
                and not keywords
                and args[0].top_sorts == frozenset(("float",))
                and replace(args[0], top_sorts=frozenset()).is_bottom()
            ):
                return [NormalExprFlow(StateHandle.capture(store), args[0])]
            constants = args[0].exact_constants()
            converted_values: list[Value] = []
            result = []
            if constants is not None:
                arguments = _constant_options(args)
                keyword_options = _constant_options([value for _, value in keywords])
                if arguments is None or keyword_options is None:
                    return self.unknown(store, node, "dynamic_conversion_arguments")
                groups = arguments + keyword_options
                size = 1
                for group in groups:
                    size *= len(group)
                if size > self.ops.policy.max_scalar_pairs:
                    return self.unknown(store, node, "conversion_enumeration_budget", havoc=False)
                self.d.charge(size, "scalar_conversion_work")
                for combo in product(*groups):
                    try:
                        converted_values.append(
                            Value.constant(
                                scalar_conversion(
                                    name,
                                    combo[: len(arguments)],
                                    {
                                        key: combo[len(arguments) + index]
                                        for index, (key, _) in enumerate(keywords)
                                    },
                                )
                            )
                        )
                    except (TypeError, ValueError, OverflowError) as exc:
                        result.append(self.error(store.copy(), type(exc).__name__))
                if converted_values:
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(store), self.ops.join_all(converted_values)
                        )
                    )
                return result
            if name in {"str", "repr"} and len(args) == 1 and not keywords:
                if (
                    name == "str"
                    and not args[0].strings.is_bottom()
                    and not replace(args[0], strings=self.ops.string.bottom()).is_bottom()
                ):
                    return self.unknown(store, node, "mixed_scalar_string_conversion", havoc=False)
                return [
                    NormalExprFlow(
                        StateHandle.capture(store),
                        Value(
                            strings=args[0].strings
                            if name == "str" and (not args[0].strings.is_bottom())
                            else self.ops.string.top()
                        ),
                    )
                ]
            if name == "abs":
                valid = (
                    Value(ints=self.ops.integer.top())
                    if not args[0].ints.is_bottom
                    else Value.bottom()
                )
                if args[0].floats or "float" in args[0].top_sorts:
                    valid = self.ops.join(valid, Value(top_sorts=frozenset(("float",))))
                result = (
                    [NormalExprFlow(StateHandle.capture(store.copy()), valid)]
                    if not valid.is_bottom()
                    else []
                )
                invalid = replace(
                    args[0],
                    ints=self.ops.integer.bottom(),
                    floats=frozenset(),
                    top_sorts=args[0].top_sorts - {"int", "float"},
                )
                if not invalid.is_bottom():
                    result.append(self.error(store.copy(), "TypeError"))
                return result
            sort = "int" if name == "int" else "float"
            return [
                replace_flow(out, value=Value(top_sorts=frozenset((sort,))))
                if out.kind == "normal"
                else out
                for out in self.unknown(
                    store, node, "abstract_scalar_conversion:" + name, havoc=False
                )
            ]
        if name in {"getattr", "hasattr", "setattr", "delattr"}:
            return self.attributes.reflective(name, args, keywords, store, frame, node)
        if name in {"isinstance", "issubclass"}:
            if keywords or len(args) != 2:
                return [self.error(store, "TypeError")]
            affected = [store.heap.get(ref) for value in args for ref in value.refs]
            affected.extend(
                store.heap.get(cls) for obj in tuple(affected) if obj for cls in obj.class_ids
            )
            if args[1].top or args[1].top_sorts or any(obj and obj.havoc for obj in affected):
                return [
                    replace_flow(out, value=Value(bools=frozenset((False, True))))
                    if out.kind == "normal"
                    else out
                    for out in self.unknown(store, node, "havoc_class_relation_query")
                ]
            truths = set()
            for ref in args[0].refs:
                obj = store.heap.get(ref)
                classes = obj.class_ids if obj and name == "isinstance" else frozenset((ref,))
                for cls in args[1].refs:
                    truths.add(
                        cls in classes
                        or any(
                            cls in store.heap.get(c, HeapObject("class")).metadata.lookup("mro", ())
                            for c in classes
                        )
                    )
            if args[0].top or not truths:
                truths.update((False, True))
            return [NormalExprFlow(StateHandle.capture(store), Value(bools=frozenset(truths)))]
        if name == "type":
            if len(args) == 1:
                if args[0].top or any(
                    store.heap.get(ref) and store.heap[ref].havoc for ref in args[0].refs
                ):
                    self.d.open("unknown_runtime_type", store, node)
                    return [
                        NormalExprFlow(
                            StateHandle.capture(store), Value(top_sorts=frozenset(("type",)))
                        )
                    ]
                refs = set()
                for ref in args[0].refs:
                    obj = store.heap.get(ref)
                    if obj:
                        refs |= set(obj.class_ids)
                return [
                    NormalExprFlow(
                        StateHandle.capture(store),
                        Value(refs=frozenset(refs)) if refs else Value.unknown(),
                    )
                ]
            return self.unknown(store, node, "dynamic_class_construction")
        if name == "iter":
            if keywords:
                return [self.error(store, "TypeError")]
            if len(args) != 1:
                return (
                    self.unknown(store, node, "callable_sentinel_iterator")
                    if len(args) == 2
                    else [self.error(store, "TypeError")]
                )
            return self.continuations.get_iterator(args[0], store, frame, node)
        if name == "next":
            if not 1 <= len(args) <= 2 or keywords:
                return [self.error(store, "TypeError")]
            result = self.resume(args[0], store, frame, node, "next")
            if len(args) > 1:
                result = [
                    NormalExprFlow(StateHandle.capture(f.store), args[1])
                    if self.continuations.is_stop(f)
                    else f
                    for f in result
                ]
            return result
        if name in {"sum", "all", "any", "min", "max", "sorted", "reversed", "enumerate", "zip"}:
            if keywords:
                return self.unknown(store, node, "aggregate_keyword_protocol:" + name)
            return self.aggregate_builtin(name, args, store, frame, node)
        if name == "super":
            if len(args) < 2:
                classcell = dict(frame.closure).get("__class__")
                owner = store.cells.get(classcell, Value.bottom()) if classcell else Value.bottom()
                function = next(
                    (info for info in self.d.functions.values() if info.qualname == frame.qualname),
                    None,
                )
                positional = (
                    (*function.node.args.posonlyargs, *function.node.args.args) if function else ()
                )
                receiver = (
                    store.cells.get(frame.cell(positional[0].arg), Value.bottom())
                    if positional
                    else Value.bottom()
                )
                if owner.is_bottom() or receiver.is_bottom():
                    return [self.error(store, "RuntimeError")]
                args = [owner, receiver]
            return [
                NormalExprFlow(
                    StateHandle.capture(store),
                    self.alloc(
                        node,
                        store,
                        frame,
                        "super",
                        metadata={"class": args[0], "receiver": args[1]},
                    ),
                )
            ]
        return self.unknown(store, node, "unsupported_builtin:" + name)

    def builtin_method(
        self,
        name: str,
        receiver: Value,
        args: Sequence[Value],
        keywords: Sequence[tuple[str, Value]],
        store: Store,
        frame: Frame,
        node: ast.AST | None,
    ) -> list[Flow]:
        result: list[Flow]
        self.sync(store)
        views = frozenset(
            ref
            for ref in receiver.refs
            if store.heap.get(ref) and store.heap[ref].kind == "dict_view"
        )
        if views:
            result = []
            for ref in views:
                view_object = store.heap[ref]
                current = store.copy()
                if view_object.havoc:
                    result.extend(self.unknown(current, node, "havoc_dictionary_view"))
                    continue
                if name == "__iter__" and not args and not keywords:
                    result.extend(
                        self.dictionary_views.iterator(
                            view_object.metadata.view_owner(),
                            view_object.metadata["view_kind"],
                            current,
                            frame,
                            node,
                        )
                    )
                elif name == "__len__" and not args and not keywords:
                    result.extend(self.dictionary_views.length(view_object, current, frame, node))
                elif name == "__contains__" and len(args) == 1 and not keywords:
                    result.extend(
                        self.dictionary_views.contains(view_object, args[0], current, frame, node)
                    )
                else:
                    result.append(self.error(current, "TypeError"))
            remainder = replace(receiver, refs=receiver.refs - views)
            if not remainder.is_bottom():
                result.extend(
                    self.builtin_method(name, remainder, args, keywords, store.copy(), frame, node)
                )
            return result
        if any(
            store.heap.get(ref) and store.heap[ref].kind == "namespace" for ref in receiver.refs
        ):
            return self.namespaces.method(name, receiver, args, keywords, store, frame, node)
        result = []
        if name in {"send", "throw", "close", "__next__", "__await__"}:
            expected = {
                "send": (1, 1),
                "throw": (1, 3),
                "close": (0, 0),
                "__next__": (0, 0),
                "__await__": (0, 0),
            }[name]
            if keywords or not expected[0] <= len(args) <= expected[1]:
                return [self.error(store, "TypeError")]
            if name == "throw" and len(args) > 1:
                if len(args) == 3 and args[2].exact_constants() != [None]:
                    return self.unknown(store, node, "generator_throw_traceback_protocol")
                result = []
                for ref in args[0].refs:
                    target = store.heap.get(ref)
                    if (
                        ref.startswith("builtin:")
                        and ref.split(":", 1)[1] in EXCEPTION_NAMES
                        or target
                        and target.kind == "class"
                    ):
                        throw_sequences: list[tuple[Value, ...]] = []
                        if args[1].exact_constants() == [None]:
                            throw_sequences = [()]
                        for value_ref in args[1].refs:
                            packed = store.heap.get(value_ref)
                            if (
                                packed
                                and packed.kind == "tuple"
                                and type(packed.metadata.lookup("length")) is int
                            ):
                                throw_sequences.append(
                                    tuple(
                                        packed.fields.get(index, packed.default)
                                        for index in range(packed.metadata["length"])
                                    )
                                )
                        if not throw_sequences:
                            throw_sequences = [(args[1],)]
                        for throw_sequence in throw_sequences:
                            for made in self.invoke(
                                Value.ref(ref),
                                list(throw_sequence),
                                (),
                                store.copy(),
                                frame,
                                node,
                                publish=False,
                            ):
                                if made.kind == "normal":
                                    result.extend(
                                        self.resume(
                                            receiver,
                                            made.store,
                                            frame,
                                            node,
                                            "throw",
                                            require_payload(made),
                                        )
                                    )
                                else:
                                    result.append(made)
                    elif args[1].exact_constants() == [None]:
                        result.extend(
                            self.resume(
                                receiver, store.copy(), frame, node, "throw", Value.ref(ref)
                            )
                        )
                    else:
                        result.append(self.error(store.copy(), "TypeError"))
                if args[0].top or args[0].top_sorts:
                    result.extend(self.unknown(store, node, "unknown_throw_exception_class"))
                return result or [self.error(store, "TypeError")]
            return self.resume(
                receiver,
                store,
                frame,
                node,
                {"__next__": "next", "__await__": "await"}.get(name, name),
                args[0] if args else Value.constant(None),
            )
        if name == "__iter__":
            return self.builtin("iter", [receiver], (), store, frame, node, publish=False)
        for ref in receiver.refs:
            obj = store.heap.get(ref)
            if obj is None:
                continue
            s = store.copy()
            target = s.heap[ref]
            if obj.havoc:
                result.extend(self.unknown(s, node, "havoc_builtin_state"))
                continue
            if obj.kind == "property" and name in {"setter", "getter", "deleter"}:
                if not args:
                    result.append(self.error(s, "TypeError"))
                    continue
                fields = dict(target.fields)
                fields[{"setter": "fset", "getter": "fget", "deleter": "fdel"}[name]] = args[0]
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(s), self.alloc(node, s, frame, "property", fields)
                    )
                )
                continue
            if obj.kind == "list" and name in {"append", "extend", "insert"}:
                length = target.metadata.lookup("length")
                if name == "append" and len(args) == 1:
                    if length is None:
                        s.invalidate_guards(ref)
                        target.default = self.ops.join(target.default, args[0])
                        s.record_write(HeapDefaultWrite(ref))
                    else:
                        s.write_slot(ref, length, args[0], self.ops)
                        target.metadata["length"] = length + 1
                    result.append(self.normal(s))
                    continue
                if name == "extend" and args:
                    sequences, unknown = self.iterable_values(args[0], s)
                    for sequence in sequences:
                        copy = s.copy()
                        t = copy.heap[ref]
                        n = t.metadata.lookup("length")
                        if n is not None:
                            for index, value in enumerate(sequence):
                                copy.write_slot(ref, n + index, value, self.ops)
                            t.metadata["length"] = n + len(sequence)
                        else:
                            copy.invalidate_guards(ref)
                            t.default = self.ops.join(t.default, self.ops.join_all(sequence))
                            copy.record_write(HeapDefaultWrite(ref))
                        result.append(self.normal(copy))
                    if unknown:
                        result.extend(self.unknown(s, node, "unknown_list_extend"))
                    continue
            if obj.kind == "dict":
                if name == "get" and args:
                    default = args[1] if len(args) > 1 else Value.constant(None)
                    for flow in self.getitem(receiver, args[0], s, frame, node):
                        result.append(
                            NormalExprFlow(StateHandle.capture(flow.store), default)
                            if flow.exception == "KeyError"
                            else flow
                        )
                    continue
                if name == "setdefault" and args:
                    default = args[1] if len(args) > 1 else Value.constant(None)
                    for flow in self.getitem(receiver, args[0], s, frame, node):
                        if flow.exception == "KeyError":
                            for stored in self.setitem(
                                receiver, args[0], default, flow.store, frame, node
                            ):
                                result.append(
                                    make_completion(
                                        StateHandle.capture(stored.store),
                                        stored.kind,
                                        default
                                        if stored.kind == "normal"
                                        else flow_payload(stored),
                                        stored.exception,
                                        FlowTail(),
                                    )
                                )
                        else:
                            result.append(flow)
                    continue
                if name == "update":
                    fields = dict(keywords)
                    if args:
                        for other in args[0].refs:
                            rhs = s.heap.get(other)
                            if rhs and rhs.kind == "dict":
                                fields.update(rhs.fields)
                            else:
                                self.d.open("unknown_dict_update", s, node)
                                target.havoc = True
                    for key, value in fields.items():
                        s.write_slot(ref, key, value, self.ops)
                    target.metadata["length"] = len(target.fields)
                    result.append(self.normal(s))
                    continue
                if name in {"keys", "values", "items"}:
                    if args or keywords:
                        result.append(self.error(s, "TypeError"))
                        continue
                    result.append(
                        NormalExprFlow(
                            StateHandle.capture(s),
                            self.dictionary_views.create(
                                ref,
                                "keys"
                                if name == "keys"
                                else "values"
                                if name == "values"
                                else "items",
                                s,
                                frame,
                                node,
                            ),
                        )
                    )
                    continue
            if name == "clear" and obj.kind in {"dict", "list", "set"}:
                s.invalidate_guards(ref)
                for key in list(target.fields):
                    target.fields.pop(key, None)
                    target.maybe_missing.add(key)
                    s.record_write(HeapDelete(ref, key))
                target.default = Value.bottom()
                target.metadata["length"] = 0
                s.record_write(HeapDefaultWrite(ref))
                result.append(self.normal(s))
                continue
            if name == "copy" and obj.kind in {"dict", "list", "set"}:
                value = self.alloc(
                    node, s, frame, obj.kind, dict(target.fields), dict(target.metadata)
                )
                result.append(NormalExprFlow(StateHandle.capture(s), value))
                continue
            if name == "pop" and obj.kind in {"dict", "list"}:
                keys = (
                    args[0].exact_constants() if args else [target.metadata.lookup("length", 1) - 1]
                )
                if keys is None:
                    result.extend(self.unknown(s, node, "unknown_pop"))
                    continue
                for key in keys:
                    value, missing = s.read_slot(ref, key, self.ops)
                    if not value.is_bottom():
                        copy = s.copy()
                        copy.invalidate_guards(ref)
                        copy.heap[ref].fields.pop(key, None)
                        copy.heap[ref].maybe_missing.add(key)
                        copy.record_write(HeapDelete(ref, key))
                        result.append(NormalExprFlow(StateHandle.capture(copy), value))
                    if missing:
                        result.append(
                            NormalExprFlow(StateHandle.capture(s.copy()), args[1])
                            if len(args) > 1
                            else self.error(
                                s.copy(), "KeyError" if obj.kind == "dict" else "IndexError"
                            )
                        )
                continue
        if result:
            return result
        constants = receiver.exact_constants()
        arg_constants = _constant_options(args)
        if constants is not None and arg_constants is not None:
            for scalar_receiver in constants:
                if not isinstance(scalar_receiver, str):
                    continue
                for combo in product(*arg_constants):
                    try:
                        concrete = apply_string_method(
                            scalar_receiver, name, combo, _first_keyword_constants(keywords)
                        )
                        if isinstance(concrete, UnsupportedStringMethod):
                            continue
                        if isinstance(concrete, list):
                            s = store.copy()
                            out = self.alloc(
                                node,
                                s,
                                frame,
                                "list",
                                {i: Value.constant(x) for i, x in enumerate(concrete)},
                                {"length": len(concrete)},
                            )
                            result.append(NormalExprFlow(StateHandle.capture(s), out))
                        else:
                            result.append(
                                NormalExprFlow(
                                    StateHandle.capture(store.copy()), Value.constant(concrete)
                                )
                            )
                    except (TypeError, ValueError, IndexError) as exc:
                        result.append(self.error(store.copy(), type(exc).__name__))
        return result or self.unknown(store, node, "unsupported_builtin_method:" + name)

    def resume(
        self,
        receiver: Value,
        store: Store,
        frame: Frame,
        node: ast.AST | None,
        operation: str,
        value: Value | None = None,
    ) -> list[Flow]:
        if operation == "next":
            return self.continuations.next_iterator(receiver, store, frame, node)
        return self.continuations.resume(receiver, store, frame, node, operation, value)

    def aggregate_builtin(
        self, name: str, args: Sequence[Value], store: Store, frame: Frame, node: ast.AST | None
    ) -> list[Flow]:
        result: list[Flow]
        if not args:
            return [self.error(store, "TypeError")]
        sequences, unknown = self.iterable_values(args[0], store)
        result = []
        if name in {"min", "max"} and len(args) > 1:
            sequences = [list(args)]
            unknown = False
        for sequence in sequences:
            if name == "enumerate":
                start = 0
                if len(args) > 1:
                    start_constants = args[1].exact_constants()
                    if start_constants:
                        first_start = start_constants[0]
                        if not isinstance(first_start, int):
                            result.append(self.error(store.copy(), "TypeError"))
                            continue
                        start = first_start
                s = store.copy()
                values = [
                    self.alloc(
                        node, s, frame, "tuple", {0: Value.constant(i + start), 1: v}, {"length": 2}
                    )
                    for i, v in enumerate(sequence)
                ]
                result.append(
                    NormalExprFlow(
                        StateHandle.capture(s),
                        self.alloc(
                            node,
                            s,
                            frame,
                            "list",
                            dict(enumerate(values)),
                            {"length": len(values)},
                        ),
                    )
                )
                continue
            exact = _single_constants(sequence)
            if exact is not None:
                try:
                    if name in {"sorted", "reversed"}:
                        ordered = scalar_sort(exact) if name == "sorted" else list(reversed(exact))
                        ordered_values = [Value.constant(item) for item in ordered]
                        current = store.copy()
                        result.append(
                            NormalExprFlow(
                                StateHandle.capture(current),
                                self.alloc(
                                    node,
                                    current,
                                    frame,
                                    "list",
                                    dict(enumerate(ordered_values)),
                                    {"length": len(ordered_values)},
                                ),
                            )
                        )
                        continue
                    if name in {"sum", "all", "any", "min", "max"}:
                        starts = (
                            args[1].exact_constants() if name == "sum" and len(args) > 1 else [0]
                        )
                        if starts is None:
                            result.extend(self.unknown(store, node, "aggregate_sum_start"))
                            continue
                        for initial in starts:
                            result.append(
                                NormalExprFlow(
                                    StateHandle.capture(store.copy()),
                                    Value.constant(scalar_aggregate(name, exact, initial)),
                                )
                            )
                        continue
                except (TypeError, ValueError) as exc:
                    result.append(self.error(store.copy(), type(exc).__name__))
                    continue
            result.extend(self.unknown(store, node, "aggregate_input:" + name))
        if unknown:
            result.extend(self.unknown(store, node, "aggregate_iteration:" + name))
        return result or self.unknown(store, node, "unsupported_aggregate:" + name)

    def with_statement(
        self, node: ast.With | ast.AsyncWith, store: Store, frame: Frame
    ) -> list[Flow]:
        return self.continuations.with_statement(node, store, frame)

    def comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp, store: Store, frame: Frame
    ) -> list[Flow]:
        from pythonstan.analysis.semantics.comprehensions import ComprehensionEvaluation

        return ComprehensionEvaluation(self).evaluate(node, store, frame)
