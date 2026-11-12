"""Alias-preserving legacy state boundary to the shared abstract engine.

This module converts representations. Python operations execute the ordinary
Transfer/InterproceduralDriver, never a second compatibility interpreter.
Unavailable metadata produces explicit obligations.
"""

from __future__ import annotations

import ast
import builtins
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from enum import Enum
from typing import TYPE_CHECKING, Protocol, TypeGuard, override, runtime_checkable

from pythonstan.analysis.contracts import SourceModule
from pythonstan.analysis.contracts.budgets import AnalysisBudget
from pythonstan.analysis.contracts.identity import CellId, ContextId, FrameId, ObjectId, UnitId
from pythonstan.analysis.contracts.json_data import JsonObject, JsonValue
from pythonstan.analysis.contracts.observation import ObserverPort
from pythonstan.analysis.contracts.oracle import CandidateOraclePort
from pythonstan.analysis.contracts.private_slots import (
    keyword_default_slot,
    positional_default_slot,
)
from pythonstan.analysis.contracts.work import WorkLedgerPort
from pythonstan.analysis.domains.ints import IntLattice, Parity
from pythonstan.analysis.domains.strings import StrLattice
from pythonstan.analysis.domains.values import Value
from pythonstan.analysis.engines.abstract.driver import BudgetStop, InterproceduralDriver
from pythonstan.analysis.engines.abstract.state import Frame, FunctionInfo, HeapObject, Store
from pythonstan.analysis.pointer.kcfa.class_hierarchy import ClassHierarchyManager
from pythonstan.analysis.pointer.kcfa.context import (
    NativeContext,
    ObjectContext,
    Scope,
    is_native_context,
)
from pythonstan.analysis.pointer.kcfa.heap_model import Field
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, FunctionObject, UnknownValue
from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
from pythonstan.analysis.semantics.names import classify_bindings
from pythonstan.analysis.state.flow import Flow, flow_payload, require_payload
from pythonstan.analysis.state.keys import FieldKey, LiteralKey
from pythonstan.analysis.state.metadata import ComponentPayload
from pythonstan.compat.legacy_ai.api import AISummary, ArgBinding, PtaQuery
from pythonstan.compat.legacy_ai.heap import AIAddr
from pythonstan.compat.legacy_ai.state import AIState
from pythonstan.compat.legacy_ai.value_domain import AbsVal
from pythonstan.frontend.source import SourceInput, SourceProgram, source_constant, source_position
from pythonstan.ir.ir_statements import IRClass, IRFunc, IRModule, IRScope
from pythonstan.world.scope_manager import ScopeManager

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.ai.solver import SolverConfig

type SourceDefinition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef

type KeySignatureValue = None | bool | int | float | str | tuple[KeySignature, ...]
type KeySignature = tuple[str, KeySignatureValue]


@runtime_checkable
class NativeStateProvider(Protocol):
    @property
    def _state(self) -> PointerAnalysisState: ...


@runtime_checkable
class NativeHierarchyProvider(Protocol):
    @property
    def _class_hierarchy(self) -> ClassHierarchyManager | None: ...


_NATIVE_STATE_RUNTIME: type[object] = PointerAnalysisState


def _has_native_state(value: object) -> TypeGuard[NativeStateProvider]:
    return isinstance(value, NativeStateProvider) and isinstance(
        value._state, _NATIVE_STATE_RUNTIME
    )


def _has_native_hierarchy(value: object) -> TypeGuard[NativeHierarchyProvider]:
    if not isinstance(value, NativeHierarchyProvider):
        return False
    return value._class_hierarchy is None or isinstance(
        value._class_hierarchy, ClassHierarchyManager
    )


class ResidualKey(Enum):
    UNKNOWN = "unrepresented_native_key"


def _literal_key(value: object) -> LiteralKey:
    """Decode the actual Native literal-key boundary into the Source key model."""
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return value
    if isinstance(value, tuple):
        return tuple(_literal_key(part) for part in value)
    raise BoundaryUnavailable("Unrepresented dictionary key")


class BoundaryUnavailable(ValueError):
    pass


def _checked_context(value: object) -> NativeContext:
    if not is_native_context(value):
        raise BoundaryUnavailable("Unsupported custom Native context implementation")
    return value


def _capsule_index(item: tuple[int, AbsVal]) -> int:
    return item[0]


class SharedDriver(InterproceduralDriver):
    def __init__(
        self,
        modules: Mapping[str, SourceInput] | Iterable[SourceInput],
        options: Mapping[str, object] | None = None,
        oracle: CandidateOraclePort[Value] | None = None,
        observer: ObserverPort | None = None,
        program: SourceProgram | None = None,
        *,
        work_ledger: WorkLedgerPort | None = None,
    ) -> None:
        self.work_ledger: WorkLedgerPort | None = work_ledger
        super().__init__(modules, options, oracle, observer, program)

    @override
    def charge(self, amount: int, category: str) -> None:
        # A denied local transfer never consumes shared work.
        if amount < 0:
            raise ValueError("negative work charge")
        if amount > self.max_work - self.work:
            raise BudgetStop("max_work")
        if self.work_ledger is not None:
            remaining = self.work_ledger.remaining
            if type(remaining) is int and amount > remaining:
                raise BudgetStop("shared_work_budget")
            for _ in range(amount):
                if self.work_ledger.consume(kind="abstract_transfer") is False:
                    raise BudgetStop("shared_work_budget")
        super().charge(amount, category)


class LegacyStateBridge:
    """One invocation's complete value, alias and outgoing state conversion."""

    def __init__(
        self,
        query: PtaQuery | None,
        budget: AnalysisBudget,
        context: NativeContext,
        scope: Scope | None,
        initial: AIState,
        driver: SharedDriver,
    ) -> None:
        self.query: PtaQuery | None = query
        self.budget: AnalysisBudget = budget
        self.context: NativeContext = _checked_context(context)
        self.scope: Scope | None = scope
        self.initial: AIState = initial
        if initial.registry is None:
            raise BoundaryUnavailable("Legacy input registry unavailable")
        self.registry = initial.registry
        self.driver: SharedDriver = driver
        self.objects: dict[AbstractObject, ObjectId] = {}
        self.reverse: dict[ObjectId, AbstractObject] = {}
        self.addresses: dict[AIAddr, ObjectId] = {}
        self.reads: set[tuple[AbstractObject, Field]] = set()
        self.reasons: set[str] = set()
        self.store: Store = Store()
        self.native_state: PointerAnalysisState | None = None
        self.manager: ScopeManager | None = None
        self.ir_scopes: tuple[IRScope, ...] = ()
        self.source_index: dict[tuple[str, str], list[SourceDefinition]] = {}

    def register_function(self, obj: FunctionObject, identifier: ObjectId) -> None:
        self.reasons.add("bridge_callable_source_unavailable")

    def source_node(self, scope_ir: IRScope) -> SourceDefinition | None:
        return None

    def source_ir(self, module: str, qualname: str, node: ast.AST | None) -> IRScope | None:
        return None

    def native_parent(self, prototype: IRScope) -> Scope | None:
        return None

    @staticmethod
    def default_slot(index: int) -> FieldKey:
        return positional_default_slot(index)

    @staticmethod
    def keyword_slot(name: str) -> FieldKey:
        return keyword_default_slot(name)

    def value(self, old: AbsVal) -> Value:
        from pythonstan.analysis.pointer.kcfa.object import ConstantObject

        if old.is_bottom():
            return Value.bottom()
        refs: set[ObjectId] = set()
        constants: list[Value] = []
        for obj in old.addrs:
            if isinstance(obj, ConstantObject):
                constants.append(Value.constant(obj.value))
            elif isinstance(obj, UnknownValue):
                constants.append(Value.unknown())
            else:
                refs.add(self.object(obj))
        if old.scalar_kinds is None:
            if not old.addr_top:
                self.reasons.add("bridge_untagged_scalar_product")
            scalar = Value.unknown()
        else:
            integer, string, boolean = (
                old.get_int_lattice(),
                old.get_string_lattice(),
                old.get_bool_values(),
            )
            scalar = Value(
                ints=IntLattice(
                    integer.consts,
                    integer.lo,
                    integer.hi,
                    Parity(integer.parity.value),
                    integer.is_bottom,
                    integer.is_top,
                ),
                strings=StrLattice(
                    string.consts,
                    string.prefixes,
                    string.suffixes,
                    string.has_unknown,
                    string.conjunctions,
                ),
                bools=frozenset(
                    x
                    for x in (False, True)
                    if (boolean.may_be_true() if x else boolean.may_be_false())
                ),
                none=old.may_be_none is True,
                top=old.addr_top,
            )
        return self.driver.ops.join_all([scalar, *constants, Value(refs=frozenset(refs))])

    def field_key(self, field: Field, kind: str) -> FieldKey | ResidualKey:
        from pythonstan.analysis.pointer.kcfa.heap_model import FieldKind

        if field.kind in {FieldKind.ATTRIBUTE, FieldKind.OWN_ATTRIBUTE}:
            if field.name is None:
                raise BoundaryUnavailable("Attribute field name unavailable")
            return field.name
        if field.kind == FieldKind.POSITION:
            return field.index
        if field.kind == FieldKind.KEY:
            if field.name is None:
                raise BoundaryUnavailable("Native key field name unavailable")
            if kind in {"list", "tuple", "range"} and field.key_type is None:
                try:
                    return int(field.name)
                except (ValueError, TypeError):
                    pass
            if field.key_type == "NoneType":
                return None
            if field.key_type in {"number", "tuple"}:
                try:
                    literal: object = ast.literal_eval(field.name)
                    return _literal_key(literal)
                except (ValueError, TypeError, SyntaxError):
                    self.reasons.add("bridge_numeric_key")
                    return _RESIDUAL
            return field.name
        if field.kind not in {
            FieldKind.ELEMENT,
            FieldKind.VALUE,
            FieldKind.UNKNOWN,
            FieldKind.OWN_UNKNOWN,
            FieldKind.WILDCARD_KEY,
        }:
            kind_name: object = field.kind.value
            if not isinstance(kind_name, str):
                raise BoundaryUnavailable("Unrepresented Native field kind")
            self.reasons.add("bridge_field_metadata:" + kind_name)
        return _RESIDUAL

    def object(self, obj: AbstractObject) -> ObjectId:
        from pythonstan.analysis.pointer.kcfa import object as native
        from pythonstan.analysis.pointer.kcfa.heap_model import attr, unknown
        from pythonstan.compat.legacy_ai.heap import AIAddr

        if obj in self.objects:
            return self.objects[obj]
        if isinstance(obj, native.BuiltinMethodObject):
            identifier = ObjectId(f"compat-object:{len(self.objects)}")
            self.objects[obj] = identifier
            self.reverse[identifier] = obj
            self.store.heap[identifier] = HeapObject(
                "builtin_method",
                metadata={
                    "name": obj.method_name,
                    "receiver": Value.ref(self.object(obj.receiver)),
                },
            )
            self.addresses[AIAddr.from_kcfa_obj(obj)] = identifier
            return identifier
        if isinstance(obj, native.MethodObject) and obj.ir.is_static_method:
            identifier = ObjectId(f"compat-object:{len(self.objects)}")
            self.objects[obj] = identifier
            self.reverse[identifier] = obj
            self.store.heap[identifier] = HeapObject("staticmethod")
            unbound = ObjectId(f"compat-unbound:{len(self.objects)}")
            prototype = native.FunctionObject(
                obj.context,
                native.AllocSite(obj.ir, native.AllocKind.FUNCTION),
                obj.container_scope,
                obj.ir,
            )
            self.objects[prototype] = unbound
            self.reverse[unbound] = prototype
            self.store.heap[unbound] = HeapObject("function")
            self.register_function(prototype, unbound)
            self.store.heap[identifier].fields["callable"] = Value.ref(unbound)
            self.addresses[AIAddr.from_kcfa_obj(obj)] = identifier
            return identifier
        if isinstance(obj, native.MethodObject) and (
            obj.instance_obj is not None or obj.ir.is_class_method
        ):
            identifier = ObjectId(f"compat-object:{len(self.objects)}")
            self.objects[obj] = identifier
            self.reverse[identifier] = obj
            self.store.heap[identifier] = HeapObject("bound_method")
            capsule = {
                name: value
                for (owner, name), value in self.initial.shared_bindings.items()
                if owner == f"$method:{hash(obj)}"
            }
            if "callable" in capsule and "receiver" in capsule:
                self.store.heap[identifier].metadata = {
                    "callable": self.value(capsule["callable"]),
                    "receiver": self.value(capsule["receiver"]),
                }
                self.addresses[AIAddr.from_kcfa_obj(obj)] = identifier
                return identifier
            unbound = ObjectId(f"compat-unbound:{len(self.objects)}")
            prototype = native.FunctionObject(
                obj.context,
                native.AllocSite(obj.ir, native.AllocKind.FUNCTION),
                obj.container_scope,
                obj.ir,
            )
            self.objects[prototype] = unbound
            self.reverse[unbound] = prototype
            self.store.heap[unbound] = HeapObject("function")
            self.register_function(prototype, unbound)
            receiver = obj.instance_obj if obj.instance_obj is not None else obj.class_obj
            self.store.heap[identifier].metadata = {
                "callable": Value.ref(unbound),
                "receiver": Value.ref(self.object(receiver)),
            }
            self.addresses[AIAddr.from_kcfa_obj(obj)] = identifier
            return identifier
        if isinstance(obj, (native.BuiltinFunctionObject, native.BuiltinClassObject)):
            name = (
                obj.builtin_name
                if isinstance(obj, native.BuiltinClassObject)
                else obj.function_name
            )
            identifier = ObjectId("builtin:" + name)
            self.objects[obj] = identifier
            self.reverse[identifier] = obj
            return identifier
        identifier = ObjectId(f"compat-object:{len(self.objects)}")
        self.objects[obj] = identifier
        self.reverse[identifier] = obj
        self.addresses[AIAddr.from_kcfa_obj(obj)] = identifier
        kind = (
            "instance"
            if isinstance(obj, native.InstanceObject)
            else "class"
            if isinstance(obj, native.ClassObject)
            else "function"
            if isinstance(obj, native.FunctionObject)
            else "module"
            if isinstance(obj, native.ModuleObject)
            else "list"
            if isinstance(obj, native.ListObject)
            else "tuple"
            if isinstance(obj, native.TupleObject)
            else "dict"
            if isinstance(obj, native.DictObject)
            else "set"
            if isinstance(obj, native.SetObject)
            else obj.builtin_type
            if isinstance(obj, native.BuiltinInstanceObject)
            else "unknown"
        )
        retained = self.initial.heap.get(AIAddr.from_kcfa_obj(obj))
        if retained is not None:
            kind = retained.kind.value
        target = self.store.heap[identifier] = HeapObject(kind, singleton=False)
        singleton = self.initial.shared_bindings.get((f"$object:{hash(obj)}", "singleton"))
        if singleton is not None:
            constants = self.value(singleton).exact_constants()
            if constants == [True]:
                target.singleton = True
        if kind in {"unknown", "foreign", "summary"}:
            target.havoc = True
            self.reasons.add("bridge_object_kind:" + type(obj).__name__ + ":" + kind)
        from pythonstan.models.exceptions import EXCEPTION_NAMES

        if isinstance(obj, native.BuiltinInstanceObject) and obj.builtin_type in EXCEPTION_NAMES:
            kind = target.kind = "exception"
            target.metadata.update(
                exception_name=obj.builtin_type, exception_types=(obj.builtin_type,)
            )
            target.class_ids = frozenset((ObjectId("builtin:" + obj.builtin_type),))
        if isinstance(obj, native.InstanceObject):
            target.class_ids = frozenset((self.object(obj.class_obj),))
        if isinstance(obj, native.ClassObject):
            capsule = {
                name: value
                for (owner, name), value in self.initial.shared_bindings.items()
                if owner == f"$class:{hash(obj)}"
            }
            if "shape" in capsule:
                for category in ("bases", "mro"):
                    entries = sorted(
                        (
                            (int(name.split(":")[1]), value)
                            for name, value in capsule.items()
                            if name.startswith(category + ":")
                        ),
                        key=_capsule_index,
                    )
                    values = [self.value(value) for _, value in entries]
                    if all(len(value.refs) == 1 and not value.top for value in values):
                        target.metadata[category] = tuple(
                            next(iter(value.refs)) for value in values
                        )
                    else:
                        target.havoc = True
                        self.reasons.add("bridge_class_capsule_unavailable")
            else:
                mro = self.query.get_class_mro(obj) if self.query is not None else []
                target.metadata["mro"] = tuple(self.object(base) for base in mro if base != obj)
                hierarchy = (
                    self.query._class_hierarchy if _has_native_hierarchy(self.query) else None
                )
                bases = (
                    tuple(hierarchy.get_bases(obj))
                    if hierarchy is not None and hierarchy.has_class(obj)
                    else ()
                )
                source_node = self.source_node(obj.ir)
                if bases:
                    target.metadata["bases"] = tuple(self.object(base) for base in bases)
                elif isinstance(source_node, ast.ClassDef) and not source_node.bases:
                    target.metadata["bases"] = (ObjectId("builtin:object"),)
                else:
                    # Missing Native base metadata is a covering havoc class.
                    # Consuming it executes ordinary havoc lookup/call effects;
                    # an unused module global does not execute a class body.
                    target.metadata["bases"] = ()
                    target.havoc = True
                if not target.havoc and "builtin:object" not in target.metadata["mro"]:
                    target.metadata["mro"] = tuple(target.metadata["mro"]) + (
                        ObjectId("builtin:object"),
                    )
            original_source = obj.ir.get_source_snapshot()
            target.metadata.update(
                module=original_source.module if original_source is not None else "",
                qualname=obj.ir.get_qualname(),
                name=obj.ir.get_qualname().split(".")[-1],
            )
        if isinstance(obj, native.ModuleObject):
            target.metadata["module"] = obj.ir.get_qualname()
        if kind == "namespace":
            capsule = {
                name: value
                for (owner, name), value in self.initial.shared_bindings.items()
                if owner == f"$namespace:{hash(obj)}"
            }
            for name in ("namespace_kind", "module", "prefix"):
                text_constants = capsule[name].get_const_strs() if name in capsule else None
                if text_constants is not None and len(text_constants) == 1:
                    target.metadata[name] = next(iter(text_constants))
            if "owner" in capsule:
                value = self.value(capsule["owner"])
                if len(value.refs) == 1 and not value.top:
                    target.metadata["owner"] = next(iter(value.refs))
            if "readonly" in capsule:
                constants = self.value(capsule["readonly"]).exact_constants()
                if constants is not None and len(constants) == 1:
                    target.metadata["readonly"] = bool(constants[0])
            mode = target.metadata.lookup("namespace_kind")
            if (
                mode not in {"globals", "class_locals", "object", "locals_snapshot"}
                or mode == "object"
                and "owner" not in target.metadata
                or mode == "globals"
                and target.metadata.lookup("module") not in self.driver.modules
                or mode == "class_locals"
                and "prefix" not in target.metadata
            ):
                target.havoc = True
                self.reasons.add("bridge_namespace_capsule_unavailable")
        if isinstance(obj, native.FunctionObject):
            self.register_function(obj, identifier)
        if AIAddr.from_kcfa_obj(obj) in self.initial.heap:
            # This caller capsule is the actual local AI shape. A fresh Source
            # object has no Native query registration, so querying Native here
            # would turn absent metadata into a spurious host-query failure.
            return identifier
        state = self.query._state if _has_native_state(self.query) else None
        fields: set[Field] = {
            field
            for owner, field in (() if state is None else tuple(state._field_accesses))
            if owner == obj
        }
        if kind in {"instance", "class"}:
            fields.update(
                attr(name)
                for name in (
                    "__getattribute__",
                    "__getattr__",
                    "__setattr__",
                    "__get__",
                    "__set__",
                    "__delete__",
                )
            )
            fields.add(unknown())
        for field in fields:
            if field.kind.value == "class_metadata" and field.name in {
                "native_mapping_keys",
                "native_mapping_nonscalar_keys",
                "metaclass",
            }:
                continue
            try:
                if self.query is None:
                    raise BoundaryUnavailable("Native field query unavailable")
                points = frozenset(self.query.field_points_to(obj, field, self.scope, self.context))
            except Exception:
                self.reasons.add("bridge_field_query_failure")
                target.havoc = True
                continue
            self.reads.add((obj, field))
            if not points:
                continue
            from pythonstan.compat.legacy_ai.value_domain import AbsVal

            value = self.value(AbsVal.from_addrs(points))
            key = self.field_key(field, kind)
            if kind == "class" and any(
                isinstance(point, native.FunctionObject) for point in points
            ):
                # PTA supplies callable candidates, not a covering value set or
                # a MUST-presence proof. Keep the witnessed Source callees and
                # let the ordinary unknown-call branch account for callbacks,
                # mutation and exceptional outcomes when this field is used.
                value = self.driver.ops.join(value, Value.unknown())
            if key is _RESIDUAL:
                target.default = self.driver.ops.join(target.default, value)
            else:
                target.fields[key] = self.driver.ops.join(
                    target.fields.get(key, Value.bottom()), value
                )
                target.maybe_missing.add(key)  # Native may-points-to is not a presence proof.
                if kind in {"staticmethod", "classmethod"} and key == "func":
                    target.fields["callable"] = self.driver.ops.join(
                        target.fields.get("callable", Value.bottom()), value
                    )
                    target.maybe_missing.add("callable")
        if kind in {"staticmethod", "classmethod"} and "callable" not in target.fields:
            target.havoc = True
        return identifier

    def _value(self, old: AbsVal) -> Value:
        return self.value(old)

    def import_state(self, frame: Frame) -> Store:
        """Import roots first, then effective local slots/overlays and shared cells."""
        from pythonstan.analysis.pointer.kcfa.object import BuiltinInstanceObject
        from pythonstan.compat.legacy_ai.domains.mapslots import Presence
        from pythonstan.compat.legacy_ai.heap import AIAddr

        # Recover actual owner identities from the whole retained capsule
        # before manufacturing any AI-only allocation. A method's finite
        # address projection intentionally cannot reconstruct its receiver.
        values = [
            *self.initial.env.values(),
            *self.initial.shared_bindings.values(),
            *self.initial.overlay.values(),
        ]
        if self.initial.exn is not None:
            values.append(self.initial.exn)
        for obj in self.initial.heap.values():
            for owner in obj.cls_addrs:
                self.object(owner)
            values.extend(
                [obj.slots.default.val, *(slot.val for slot in obj.slots.partitions.values())]
            )
            if obj.container_slots is not None:
                values.extend(
                    [obj.container_slots.elem_bucket, *obj.container_slots.tracked_indices.values()]
                )
        for owner, _ in self.initial.overlay:
            self.object(owner)
        for value in values:
            self.value(value)
        # Preinstall AI-local objects so cycles can be converted without inventing
        # two addresses for one native allocation/context pair.
        for address, obj in self.initial.heap.items():
            if address not in self.addresses:
                original = next(
                    (owner for owner in self.objects if AIAddr.from_kcfa_obj(owner) == address),
                    None,
                )
                if original is None:
                    original = BuiltinInstanceObject(
                        address.context, address.alloc_site, obj.kind.value
                    )
                identifier = self.object(original)
                if address not in self.addresses:
                    self.addresses[address] = identifier
                    self.store.heap[identifier].havoc = True
                    self.reasons.add("bridge_local_owner_identity_unavailable")
        for name, value in self.initial.env.items():
            self.store.cells[frame.cell(name)] = self.value(value)
        for (binding_owner, name), value in self.initial.shared_bindings.items():
            if binding_owner == "$namespace-havoc":
                self.store.havoc_namespaces |= frozenset((name,))
                continue
            if binding_owner == "$imports-havoc":
                self.store.havoc_imports = True
                continue
            if binding_owner.startswith(
                (
                    "$default:",
                    "$annotation:",
                    "$closure-id:",
                    "$namespace:",
                    "$object:",
                    "$method:",
                    "$class:",
                )
            ):
                continue
            cell = CellId(binding_owner if name == "$cell" else f"global:{binding_owner}:{name}")
            self.store.cells[cell] = self.value(value)
        for address, obj in self.initial.heap.items():
            target = self.store.heap[self.addresses[address]]
            target.kind = obj.kind.value
            target.class_ids = frozenset(self.object(value) for value in obj.cls_addrs)
            from pythonstan.analysis.contracts.private_slots import is_private_slot

            # These are already restored from the retained function capsule by
            # register_function. They are deliberately absent from user slots.
            target.fields = {
                field: value for field, value in target.fields.items() if is_private_slot(field)
            }
            target.maybe_missing = set()
            target.default = self.value(obj.slots.default.val)
            for signature, slot in obj.slots.partitions.items():
                key = self.signature_key(signature)
                if key is _RESIDUAL:
                    target.default = self.driver.ops.join(target.default, self.value(slot.val))
                    continue
                # An explicit ABSENT partition overrides a residual default.
                target.fields[key] = (
                    Value.bottom() if slot.pres == Presence.ABSENT else self.value(slot.val)
                )
                if slot.pres != Presence.PRESENT:
                    target.maybe_missing.add(key)
            if obj.container_slots is not None:
                payload = obj.container_slots
                target.default = self.driver.ops.join(
                    target.default, self.value(payload.elem_bucket)
                )
                for key, value in payload.tracked_indices.items():
                    target.fields[key] = self.value(value)
                length = payload.length.get_consts()
                if length is not None and len(length) == 1:
                    target.metadata["length"] = next(iter(length))
                else:
                    target.metadata["length_value"] = Value(ints=payload.length)
            if target.kind in {"foreign", "unknown"}:
                target.havoc = True
                self.reasons.add("bridge_incoming_object_kind:" + target.kind)
        for (owner, field), value in self.initial.overlay.items():
            identifier = self.object(owner)
            target = self.store.heap[identifier]
            key = self.field_key(field, target.kind)
            if key is _RESIDUAL:
                target.default = self.driver.ops.join(target.default, self.value(value))
            else:
                target.fields[key] = self.value(value)
                target.maybe_missing.discard(key)
        for owner in self.initial.overlay_havoc:
            identifier = self.object(owner)
            self.store.heap[identifier].havoc = True
        for old, new in self.initial.address_remaps.items():
            try:
                self.objects[old] = self.object(new)
            except (AttributeError, TypeError):
                self.reasons.add("bridge_unrepresented_incoming_alias")
        if self.initial.exn is not None:
            exception = self.value(self.initial.exn)
            self.store.cells[CellId("$active_exception")] = exception
            tags: set[str] = set()
            for identifier in exception.refs:
                exception_object = self.store.heap.get(identifier)
                if exception_object:
                    tags.update(exception_object.metadata.lookup("exception_types", ()))
            names = exception.exact_constants()
            if names:
                tags.update(value for value in names if isinstance(value, str))
            if len(tags) == 1:
                self.store.cells[CellId("$active_exception_type")] = Value.constant(
                    next(iter(tags))
                )
            else:
                self.store.cells[CellId("$active_exception_type")] = Value.constant("BaseException")
        self.store.writes = set()
        return self.store

    @staticmethod
    def signature_key(signature: tuple[str, builtins.object]) -> FieldKey | ResidualKey:
        kind, value = signature
        if kind in {"str", "int", "float", "bool", "none", "NoneType"}:
            return None if kind in {"none", "NoneType"} else _literal_key(value)
        if kind == "tuple":
            if not isinstance(value, tuple):
                return _RESIDUAL
            parts: list[FieldKey] = []
            for part in value:
                if not isinstance(part, tuple) or len(part) != 2 or not isinstance(part[0], str):
                    return _RESIDUAL
                decoded = LegacyStateBridge.signature_key((part[0], part[1]))
                if decoded is _RESIDUAL:
                    return _RESIDUAL
                parts.append(decoded)
            return tuple(parts)
        return _RESIDUAL

    @staticmethod
    def key_signature(value: FieldKey) -> KeySignature:
        if value is None:
            return ("none", None)
        if type(value) is bool:
            return ("int", int(value))
        if type(value) is int:
            return ("int", value)
        if type(value) is float:
            return ("int", int(value)) if value.is_integer() else ("float", value)
        if type(value) is str:
            return ("str", value)
        if type(value) is tuple:
            return ("tuple", tuple(LegacyStateBridge.key_signature(part) for part in value))
        raise BoundaryUnavailable("Unrepresented dictionary key")

    def old_object(self, identifier: ObjectId, store: Store) -> AbstractObject:
        """Reify a source-backed allocation; no callee body or module is replayed."""
        from pythonstan.analysis.pointer.kcfa import object as native
        from pythonstan.models.builtins import CONTAINER_TYPES
        from pythonstan.models.exceptions import EXCEPTION_NAMES

        identifier = self.driver.ops.arena.resolve(identifier)
        if identifier in self.reverse:
            return self.reverse[identifier]
        if identifier.startswith("builtin:"):
            name = identifier.split(":", 1)[1]
            builtin_factory = (
                native.BuiltinClassObject
                if name
                in EXCEPTION_NAMES
                | CONTAINER_TYPES
                | {
                    "object",
                    "type",
                    "bool",
                    "int",
                    "float",
                    "complex",
                    "property",
                    "staticmethod",
                    "classmethod",
                    "super",
                }
                else native.BuiltinFunctionObject
            )
            builtin = builtin_factory(
                self.context,
                native.AllocSite("shared-builtin:" + name, native.AllocKind.BUILTIN),
                name,
            )
            self.reverse[identifier] = builtin
            self.objects[builtin] = identifier
            return builtin
        result: AbstractObject
        obj = store.heap.get(identifier)
        if obj is None:
            self.reasons.add("bridge_missing_outgoing_object")
            result = native.UnknownValue(
                self.context,
                native.AllocSite("shared-missing:" + identifier, native.AllocKind.UNKNOWN),
            )
        elif obj.kind == "function":
            info = self.driver.functions.get(identifier)
            prototype = (
                self.source_ir(info.module, info.qualname, info.node) if info is not None else None
            )
            container = self.native_parent(prototype) if prototype is not None else None
            if not isinstance(prototype, IRFunc) or container is None:
                self.reasons.add("bridge_outgoing_function_prototype")
                result = native.UnknownValue(
                    self.context,
                    native.AllocSite("shared-function:" + identifier, native.AllocKind.UNKNOWN),
                )
            else:
                result = native.FunctionObject(
                    self.fresh_context(identifier, prototype),
                    native.AllocSite(prototype, native.AllocKind.FUNCTION),
                    container,
                    prototype,
                )
        elif obj.kind == "bound_method":
            target = obj.metadata.lookup("callable", Value.bottom())
            receiver = obj.metadata.lookup("receiver", Value.bottom())
            fn = self.old_object(next(iter(target.refs)), store) if len(target.refs) == 1 else None
            recv = (
                self.old_object(next(iter(receiver.refs)), store)
                if len(receiver.refs) == 1
                else None
            )
            cls = (
                recv.class_obj
                if isinstance(recv, native.InstanceObject)
                else recv
                if isinstance(recv, native.ClassObject)
                else None
            )
            if isinstance(fn, native.FunctionObject) and isinstance(cls, native.ClassObject):
                result = native.MethodObject(
                    self.fresh_context(identifier, fn.ir),
                    native.AllocSite(fn.ir, native.AllocKind.METHOD),
                    fn.container_scope,
                    fn.ir,
                    cls,
                    recv if isinstance(recv, native.InstanceObject) else None,
                )
            else:
                self.reasons.add("bridge_outgoing_bound_method")
                result = native.UnknownValue(
                    self.context,
                    native.AllocSite("shared-bound:" + identifier, native.AllocKind.UNKNOWN),
                )
        elif obj.kind == "builtin_method":
            receiver = obj.metadata.lookup("receiver", Value.bottom())
            if len(receiver.refs) == 1:
                result = native.BuiltinMethodObject(
                    self.context,
                    native.AllocSite(
                        "shared-builtin-method:" + identifier, native.AllocKind.BUILTIN
                    ),
                    obj.metadata["name"],
                    self.old_object(next(iter(receiver.refs)), store),
                )
            else:
                self.reasons.add("bridge_outgoing_builtin_method")
                result = native.UnknownValue(
                    self.context,
                    native.AllocSite("shared-method:" + identifier, native.AllocKind.UNKNOWN),
                )
        elif obj.kind == "class":
            module = obj.metadata.lookup("module")
            qualname = obj.metadata.lookup("qualname")
            prototype = self.source_ir(module, qualname, None) if module and qualname else None
            if prototype is None and module:
                # Runtime __qualname__ has <locals> components and omits the
                # module. Resolve the immutable source point against real IR
                # prototypes rather than treating that display name as an IR
                # lexical identity or fabricating a parent scope.
                originals = [
                    node
                    for (owner, _), nodes in self.source_index.items()
                    if owner == module
                    for node in nodes
                    if isinstance(node, ast.ClassDef)
                    and identifier.startswith("class:" + self.driver.point(node) + "@")
                ]
                if len(originals) == 1:
                    matches = [
                        scope for scope in self.ir_scopes if self.source_node(scope) is originals[0]
                    ]
                    if len(matches) == 1:
                        prototype = matches[0]
            container = self.native_parent(prototype) if prototype is not None else None
            if isinstance(prototype, IRClass) and container is not None:
                result = native.ClassObject(
                    self.fresh_context(identifier, prototype),
                    native.AllocSite(prototype, native.AllocKind.CLASS),
                    container,
                    prototype,
                )
            else:
                self.reasons.add("bridge_outgoing_class_prototype")
                result = native.BuiltinInstanceObject(
                    self.context,
                    native.AllocSite("shared-class:" + identifier, native.AllocKind.INSTANCE),
                    "class",
                )
        elif obj.kind in {"list", "tuple", "dict", "set"}:
            container_factory, allocation_kind = {
                "list": (native.ListObject, native.AllocKind.LIST),
                "tuple": (native.TupleObject, native.AllocKind.TUPLE),
                "dict": (native.DictObject, native.AllocKind.DICT),
                "set": (native.SetObject, native.AllocKind.SET),
            }[obj.kind]
            result = container_factory(
                self.context, native.AllocSite("shared:" + identifier, allocation_kind)
            )
        elif obj.kind == "instance" and len(obj.class_ids) == 1:
            instance_class = self.old_object(next(iter(obj.class_ids)), store)
            if isinstance(instance_class, native.ClassObject):
                result = native.InstanceObject(
                    self.context,
                    native.AllocSite("shared:" + identifier, native.AllocKind.INSTANCE),
                    instance_class,
                )
            else:
                result = native.BuiltinInstanceObject(
                    self.context,
                    native.AllocSite("shared:" + identifier, native.AllocKind.INSTANCE),
                    "instance",
                )
        else:
            kind = obj.kind
            result = native.BuiltinInstanceObject(
                self.context,
                native.AllocSite("shared:" + identifier, native.AllocKind.INSTANCE),
                obj.metadata.lookup("exception_name", "BaseException")
                if kind == "exception"
                else kind,
            )
            if kind not in {
                "instance",
                "exception",
                "namespace",
                "staticmethod",
                "classmethod",
                "property",
            }:
                self.reasons.add("bridge_outgoing_metadata:" + kind)
        self.reverse[identifier] = result
        self.objects[result] = identifier
        from pythonstan.compat.legacy_ai.heap import AIAddr

        self.addresses[AIAddr.from_kcfa_obj(result)] = identifier
        return result

    @staticmethod
    def fresh_context(identifier: ObjectId, prototype: IRFunc | IRClass) -> ObjectContext:
        """Represent a fresh Source activation with one flat finite atom.

        The real IR prototype remains the allocation site and the real lexical
        Native parent remains the container. Imported Native objects bypass
        this path and retain their original identity. No context embeds an
        object, frame, caller context or recursively expanding history.
        """
        from pythonstan.analysis.pointer.kcfa.context import FiniteContextAtom, ObjectContext

        return ObjectContext((FiniteContextAtom("object", prototype, (identifier,)),), depth=1)

    def old_value(self, value: Value, store: Store) -> AbsVal:
        from pythonstan.analysis.pointer.kcfa.object import (
            AllocKind,
            AllocSite,
            ConstantObject,
            UnknownValue,
        )
        from pythonstan.compat.legacy_ai.value_domain import AbsVal

        value = self.driver.ops.normalize(value)
        if value.top or value.top_sorts - {"str", "int", "bool", "none"}:
            if not value.top:
                self.reasons.add("bridge_outgoing_unknown_or_unrepresented_sort")
            # A covering Top retains discovered witnesses. They are necessary
            # for call/effect propagation and for reconstructing every heap
            # owner in the caller capsule on the next invocation.
            concrete = self.old_value(
                replace(value, top=False, top_sorts=frozenset(), unbound=False), store
            )
            witnesses = concrete.addrs | frozenset(
                (UnknownValue(self.context, AllocSite("shared-unknown-value", AllocKind.UNKNOWN)),)
            )
            return AbsVal.top(self.initial.registry).with_addrs(witnesses)
        result = AbsVal.bottom(self.initial.registry)
        if not value.ints.is_bottom:
            integer = value.ints
            result = result.join(AbsVal.from_int(0).with_component("int", integer))
        if not value.strings.is_bottom():
            result = result.join(AbsVal.from_str("").with_component("string", value.strings))
        for boolean in value.bools:
            result = result.join(AbsVal.bool_true() if boolean else AbsVal.bool_false())
        if value.none:
            result = result.join(AbsVal.none_val())
        for sort in value.top_sorts:
            if sort == "str":
                result = result.join(AbsVal.from_str("").with_component("string", StrLattice.top()))
            elif sort == "int":
                result = result.join(AbsVal.from_int(0).with_component("int", IntLattice.top()))
            elif sort == "bool":
                result = result.join(AbsVal.bool_top())
            elif sort == "none":
                result = result.join(AbsVal.none_val())
        objects = {self.old_object(identifier, store) for identifier in value.refs}
        constants = value.exact_constants()
        if constants is not None:
            objects.update(
                ConstantObject(
                    self.context,
                    AllocSite(
                        ("shared-scalar", type(number).__name__, repr(number)), AllocKind.CONSTANT
                    ),
                    number,
                )
                for number in constants
            )
        objects.update(
            ConstantObject(
                self.context, AllocSite(("shared-float", number), AllocKind.CONSTANT), number
            )
            for number in value.floats
        )
        if objects:
            result = result.join(AbsVal.from_addrs(frozenset(objects)))
        if value.unbound and not value.without_unbound().is_bottom():
            self.reasons.add("bridge_outgoing_maybe_unbound")
            result = result.join(AbsVal.top(self.initial.registry))
        return result.with_registry(self.registry)

    def old_state(
        self, store: Store, frame: Frame, extra_roots: Iterable[ObjectId] = ()
    ) -> AIState:
        from pythonstan.analysis.contracts.private_slots import (
            function_annotations_slot,
            is_private_slot,
        )
        from pythonstan.analysis.pointer.kcfa.heap_model import attr, key, unknown
        from pythonstan.compat.legacy_ai.domains.mapslots import Presence, Slot, SlotMap
        from pythonstan.compat.legacy_ai.heap import AbsObj, AIAddr, ContainerSlots, ObjKind
        from pythonstan.compat.legacy_ai.state import AIState

        result = AIState(pta_query=self.query, budget=self.budget, registry=self.initial.registry)
        for name in frame.locals:
            value = store.cells.get(frame.cell(name), Value(unbound=True))
            if not value.without_unbound().is_bottom():
                result.env[name] = self.old_value(value, store)
        if frame.is_module:
            prefix = "global:" + frame.module + ":"
            for cell, value in store.cells.items():
                if cell.startswith(prefix) and not value.without_unbound().is_bottom():
                    result.env[cell[len(prefix) :]] = self.old_value(value, store)
        for cell, value in store.cells.items():
            if cell.startswith("global:"):
                owner, name = cell[len("global:") :].rsplit(":", 1)
                result.shared_bindings[(owner, name)] = self.old_value(value, store)
            elif cell not in {"$active_exception", "$active_exception_type"}:
                result.shared_bindings[(cell, "$cell")] = self.old_value(value, store)
        # Garbage does not belong to the externally observable store. Return
        # objects already reified by old_value join these actual roots.
        pending = [
            identifier for value in store.cells.values() for identifier in value.refs
        ] + list(extra_roots)
        reachable: set[ObjectId] = set()

        def edges(payload: ComponentPayload) -> list[ObjectId]:
            if isinstance(payload, Value):
                return list(payload.refs)
            if isinstance(payload, (tuple, list, set, frozenset)):
                return [ref for item in payload for ref in edges(item)]
            if isinstance(payload, dict):
                return [ref for item in payload.values() for ref in edges(item)]
            return []

        while pending:
            identifier = pending.pop()
            if identifier in reachable or identifier not in store.heap:
                continue
            reachable.add(identifier)
            obj = store.heap[identifier]
            pending.extend(obj.class_ids)
            for value in (*obj.fields.values(), obj.default):
                pending.extend(value.refs)
            pending.extend(edges(obj.metadata))
            if obj.kind == "namespace" and obj.metadata.lookup("namespace_kind") == "object":
                namespace_owner = obj.metadata.lookup("owner")
                if namespace_owner is not None:
                    pending.append(obj.metadata.namespace_owner())
            if obj.kind == "class":
                pending.extend((*obj.metadata.lookup("bases", ()), *obj.metadata.lookup("mro", ())))
            for _, cell in obj.metadata.lookup("closure", ()):
                if cell in store.cells:
                    pending.extend(store.cells[cell].refs)
        for identifier in reachable:
            self.old_object(identifier, store)
        for identifier in reachable:
            obj = store.heap[identifier]
            native = self.old_object(identifier, store)
            address = AIAddr.from_kcfa_obj(native)
            slots = SlotMap()
            slots.default = Slot(self.old_value(obj.default, store), Presence.MAYBE)
            for field, value in obj.fields.items():
                if is_private_slot(field):
                    continue
                try:
                    signature = self.key_signature(field)
                except BoundaryUnavailable:
                    slots.default = slots.default.join(
                        Slot(self.old_value(value, store), Presence.MAYBE)
                    )
                    self.reasons.add("bridge_unrepresented_slot_key")
                    continue
                slots.partitions[signature] = Slot(
                    self.old_value(value, store),
                    Presence.MAYBE if field in obj.maybe_missing else Presence.PRESENT,
                )
            kind = next((member for member in ObjKind if member.value == obj.kind), ObjKind.FOREIGN)
            container = None
            if obj.kind in {"list", "tuple", "dict", "set", "range"}:
                length = (
                    IntLattice.const(obj.metadata["length"])
                    if type(obj.metadata.lookup("length")) is int
                    else obj.metadata["length_value"].ints
                    if isinstance(obj.metadata.lookup("length_value"), Value)
                    else IntLattice.nonneg()
                )
                container = ContainerSlots(
                    elem_bucket=self.old_value(obj.default, store),
                    tracked_indices={
                        field: self.old_value(value, store)
                        for field, value in obj.fields.items()
                        if isinstance(field, (int, str, float, bool)) or field is None
                    },
                    length=length,
                    may_be_empty=length.may_contain(0),
                )
            converted = AbsObj(
                address,
                kind,
                frozenset(self.old_object(cls, store) for cls in obj.class_ids),
                slots,
                container,
            )
            if address in result.heap:
                previous = result.heap[address]
                converted = previous.join(converted)
                if previous.kind != kind:
                    converted.kind = ObjKind.FOREIGN
                    self.reasons.add("bridge_outgoing_address_kind_collision")
            result.heap[address] = converted
            for field, value in obj.fields.items():
                if is_private_slot(field):
                    continue
                native_field = (
                    key(field)
                    if obj.kind in {"dict", "list", "tuple", "set", "range"}
                    else attr(str(field))
                )
                # Overlay entries are definite caller observations; a may-slot
                # stays in SlotMap so a roundtrip cannot upgrade its presence.
                if field not in obj.maybe_missing:
                    result.overlay[(native, native_field)] = self.old_value(value, store)
            if not obj.default.is_bottom():
                result.overlay[(native, unknown())] = self.old_value(obj.default, store)
            if obj.havoc:
                result.overlay_havoc.add(native)
            from pythonstan.compat.legacy_ai.value_domain import AbsVal

            result.shared_bindings[(f"$object:{hash(native)}", "singleton")] = (
                AbsVal.bool_true() if obj.singleton else AbsVal.bool_false()
            )
            if obj.kind == "bound_method":
                for name in ("callable", "receiver"):
                    method_value = obj.metadata.lookup(name)
                    if isinstance(method_value, Value):
                        result.shared_bindings[(f"$method:{hash(native)}", name)] = self.old_value(
                            method_value, store
                        )
            if obj.kind == "class":
                owner = f"$class:{hash(native)}"
                result.shared_bindings[(owner, "shape")] = AbsVal.bool_true()
                for category in ("bases", "mro"):
                    for index, reference in enumerate(obj.metadata.lookup(category, ())):
                        result.shared_bindings[(owner, f"{category}:{index}")] = self.old_value(
                            Value.ref(reference), store
                        )
            if obj.kind == "namespace":
                from pythonstan.compat.legacy_ai.value_domain import AbsVal

                owner = f"$namespace:{hash(native)}"
                for name in ("namespace_kind", "module", "prefix"):
                    namespace_text = obj.metadata.lookup(name)
                    if isinstance(namespace_text, str):
                        result.shared_bindings[(owner, name)] = AbsVal.from_str(namespace_text)
                if "readonly" in obj.metadata:
                    result.shared_bindings[(owner, "readonly")] = (
                        AbsVal.bool_true() if obj.metadata["readonly"] else AbsVal.bool_false()
                    )
                namespace_ref = obj.metadata.lookup("owner")
                if namespace_ref is not None:
                    result.shared_bindings[(owner, "owner")] = self.old_value(
                        Value.ref(obj.metadata.namespace_owner()), store
                    )
            if obj.kind == "function":
                info = self.driver.functions.get(identifier)
                if info is not None:
                    activation = str(hash(native))
                    parameters = (*info.node.args.posonlyargs, *info.node.args.args)
                    for index, value in enumerate(info.defaults):
                        field = self.default_slot(index)
                        name = parameters[len(parameters) - len(info.defaults) + index].arg
                        result.shared_bindings[(f"$default:{info.qualname}@{activation}", name)] = (
                            self.old_value(obj.fields.get(field, value), store)
                        )
                    for name, value in info.kw_defaults:
                        result.shared_bindings[(f"$default:{info.qualname}@{activation}", name)] = (
                            self.old_value(obj.fields.get(self.keyword_slot(name), value), store)
                        )
                    annotation = obj.fields.get(function_annotations_slot())
                    if annotation is not None:
                        result.shared_bindings[
                            (f"$annotation:{info.qualname}@{activation}", "__annotations__")
                        ] = self.old_value(annotation, store)
                    for name, cell in obj.metadata.lookup("closure", info.closure):
                        result.shared_bindings[
                            (f"$closure-id:{info.qualname}@{activation}", name)
                        ] = AbsVal.from_str(cell)
                        if cell in store.cells:
                            result.shared_bindings[(cell, "$cell")] = self.old_value(
                                store.cells[cell], store
                            )
        from pythonstan.compat.legacy_ai.value_domain import AbsVal

        for module in store.havoc_namespaces:
            result.shared_bindings[("$namespace-havoc", module)] = AbsVal.bool_true()
        if store.havoc_imports:
            result.shared_bindings[("$imports-havoc", "*")] = AbsVal.bool_true()
        if "$active_exception" in store.cells:
            result.exn = self.old_value(store.cells[CellId("$active_exception")], store)
        return result

    def summary(self, flows: Sequence[Flow], frame: Frame) -> AISummary:
        from pythonstan.analysis.contracts.private_slots import is_private_slot
        from pythonstan.analysis.pointer.kcfa.heap_model import attr, key, unknown
        from pythonstan.compat.legacy_ai.api import AISummary, FieldWrite, UnknownEffect
        from pythonstan.compat.legacy_ai.value_domain import AbsVal

        normal = [flow for flow in flows if flow.kind in {"normal", "return"}]
        exceptional = [flow for flow in flows if flow.kind == "raise"]
        ret = AbsVal.bottom(self.initial.registry)
        for normal_flow in normal:
            payload = flow_payload(normal_flow)
            if payload is not None:
                ret = ret.join(self.old_value(payload, normal_flow.store))
        joined: Store | None = None
        for normal_flow in normal:
            joined = (
                normal_flow.store.copy()
                if joined is None
                else joined.join(normal_flow.store, self.driver.ops)
            )
        exit_state = (
            None
            if joined is None
            else self.old_state(
                joined,
                frame,
                (
                    ref
                    for normal_flow in normal
                    for payload in (flow_payload(normal_flow),)
                    if payload is not None
                    for ref in payload.refs
                ),
            )
        )
        writes = []
        for flow in flows:
            from pythonstan.analysis.state.effects import HeapDefaultWrite, HeapDelete, HeapWrite

            for effect in flow.store.writes:
                if isinstance(effect, (HeapWrite, HeapDelete)):
                    tag, identifier, field = "heap", effect.owner, effect.key
                elif isinstance(effect, HeapDefaultWrite):
                    tag, identifier, field = "heap_default", effect.owner, None
                else:
                    continue
                if tag not in {"heap", "heap_default"} or identifier not in flow.store.heap:
                    continue
                if tag == "heap" and is_private_slot(field):
                    continue
                obj = flow.store.heap[identifier]
                native = self.old_object(identifier, flow.store)
                native_field = (
                    unknown()
                    if tag == "heap_default"
                    else key(field)
                    if obj.kind in {"dict", "list", "tuple", "set", "range"}
                    else attr(str(field))
                )
                value = self.old_value(
                    obj.default if tag == "heap_default" else obj.fields.get(field, Value.bottom()),
                    flow.store,
                )
                writes.append(FieldWrite(frozenset((native,)), native_field, value.addrs, value))

        def exception_name(flow: Flow) -> str:
            return "BaseException" if flow.exception in {None, "AnyException"} else flow.exception

        exceptional_states = tuple(
            (exception_name(flow), self.old_state(flow.store, frame, require_payload(flow).refs))
            for flow in exceptional
        )
        calls = []
        for _caller, callee, point in self.driver.calls:
            objects = set()
            for identifier, info in self.driver.functions.items():
                if info.qualname == callee:
                    source_store = next(
                        (flow.store for flow in flows if identifier in flow.store.heap), self.store
                    )
                    objects.add(self.old_object(identifier, source_store))
            if objects:
                calls.append((point, frozenset(objects)))
        reasons = self.reasons | set(self.driver.obligations)
        if "budget:shared_work_budget" in reasons:
            reasons.add("shared_work_budget")
        for flow in flows:
            reasons.update(flow.store.obligations)
        if any(flow.kind not in {"normal", "return", "raise"} for flow in flows):
            reasons.add("bridge_suspension_or_control_outcome")
        if reasons:
            for projected in ([exit_state] if exit_state is not None else []) + [
                state for _, state in exceptional_states
            ]:
                projected.overlay_havoc.update(self.objects)
                projected.shared_bindings = {
                    key: AbsVal.top(projected.registry)
                    if not key[0].startswith(
                        (
                            "$default:",
                            "$annotation:",
                            "$closure-id:",
                            "$namespace:",
                            "$object:",
                            "$method:",
                            "$class:",
                        )
                    )
                    else value
                    for key, value in projected.shared_bindings.items()
                }
        unknown_effects = (
            tuple(
                UnknownEffect(kind, frozenset(self.objects))
                for kind in ("may_read", "may_write", "may_call")
            )
            if reasons
            else ()
        )
        result = AISummary(
            ret=ret.addrs,
            ret_value=ret,
            normal_exit=bool(normal),
            exit_state=exit_state,
            writes=tuple(writes),
            reads=tuple((frozenset((obj,)), field) for obj, field in self.reads),
            calls=tuple(calls),
            unknown_effects=unknown_effects,
            may_raise=frozenset(exception_name(flow) for flow in exceptional),
            prefix_exceptions=frozenset(self.driver.exceptions)
            | frozenset(exception_name(flow) for flow in exceptional),
            exceptional_exit_states=exceptional_states,
            is_complete=not reasons,
            incomplete_reasons=tuple(sorted(reasons)),
        )
        result.core_statistics = {
            **dict(self.driver.stats),
            "work": self.driver.work,
            "backend": "shared_abstract",
        }
        return result


_RESIDUAL = ResidualKey.UNKNOWN


def options(budget: AnalysisBudget, solver_config: SolverConfig | None = None) -> JsonObject:
    """Project actual supported controls; inactive legacy knobs remain explicit."""
    values = budget.to_dict()
    defaults = type(budget)().to_dict()
    abstract: JsonObject = dict[str, JsonValue](values)
    abstract["int_domain_max_consts"] = 10
    supported = {
        "max_iterations",
        "max_stmt_visits",
        "max_addr_set_size",
        "max_const_strings",
        "enable_narrowing",
        "narrowing_iterations",
        "enable_value_partition",
        "max_value_partitions",
        "strict_exceptions",
    }
    for name, value in values.items():
        if name not in supported and value != defaults[name]:
            raise BoundaryUnavailable("Retired legacy budget control: " + name)
    if solver_config is not None:
        solver_defaults = type(solver_config)()
        overlaps = (
            (
                "max_iterations",
                "max_iterations",
                solver_config.max_iterations,
                solver_defaults.max_iterations,
            ),
            (
                "max_block_visits",
                "max_stmt_visits",
                solver_config.max_block_visits,
                solver_defaults.max_block_visits,
            ),
            (
                "enable_narrowing",
                "enable_narrowing",
                solver_config.enable_narrowing,
                solver_defaults.enable_narrowing,
            ),
            (
                "narrowing_iterations",
                "narrowing_iterations",
                solver_config.narrowing_iterations,
                solver_defaults.narrowing_iterations,
            ),
            (
                "enable_value_partition",
                "enable_value_partition",
                solver_config.enable_value_partition,
                solver_defaults.enable_value_partition,
            ),
            (
                "max_value_partitions",
                "max_value_partitions",
                solver_config.max_value_partitions,
                solver_defaults.max_value_partitions,
            ),
            (
                "max_addr_set_size",
                "max_addr_set_size",
                solver_config.max_addr_set_size,
                solver_defaults.max_addr_set_size,
            ),
        )
        for old, current, value, default in overlaps:
            if value == default:
                continue
            if values[current] != defaults[current] and value != values[current]:
                raise BoundaryUnavailable("Conflicting legacy budget and solver control: " + old)
            abstract[current] = value
            values[current] = value
        if solver_config.value_partition_str_cap != solver_defaults.value_partition_str_cap:
            raise BoundaryUnavailable("Retired private solver control: value_partition_str_cap")
        for name, changed in (
            ("loop_iters", solver_config.loop_iters != solver_defaults.loop_iters),
            (
                "enable_trace_partitioning",
                solver_config.enable_trace_partitioning
                != solver_defaults.enable_trace_partitioning,
            ),
            (
                "infer_merge_points",
                solver_config.infer_merge_points != solver_defaults.infer_merge_points,
            ),
            ("merge_points", solver_config.merge_points != solver_defaults.merge_points),
            (
                "max_total_traces",
                solver_config.max_total_traces != solver_defaults.max_total_traces,
            ),
        ):
            if changed:
                raise BoundaryUnavailable("Retired private solver control: " + name)
        if (
            solver_config.widen_delay != solver_defaults.widen_delay
            and solver_config.loop_unroll != solver_defaults.loop_unroll
            and solver_config.widen_delay != solver_config.loop_unroll
        ):
            raise BoundaryUnavailable(
                "Conflicting legacy widening controls: widen_delay and loop_unroll"
            )
        if solver_config.widen_delay != solver_defaults.widen_delay:
            abstract["widen_delay"] = solver_config.widen_delay
        if solver_config.loop_unroll != solver_defaults.loop_unroll:
            abstract["widen_delay"] = solver_config.loop_unroll
        abstract["max_traces_per_block"] = solver_config.max_traces_per_block
    abstract["enable_value_partition"] = bool(
        values["enable_value_partition"] and values["max_value_partitions"] > 0
    )
    abstract["refine_booleans"] = abstract["enable_value_partition"]
    abstract["attribute_name_limit"] = values.get("max_const_strings", 10)
    return {
        "abstract": abstract,
        "resources": {
            "max_work": max(0, values["max_iterations"] * max(0, values["max_stmt_visits"])),
            "max_seconds": 0,
        },
    }


def _nested_bodies(node: ast.stmt) -> tuple[list[ast.stmt], ...]:
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.If)):
        return node.body, node.orelse
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return (node.body,)
    if isinstance(node, (ast.Try, ast.TryStar)):
        return (
            node.body,
            node.orelse,
            node.finalbody,
            *(handler.body for handler in node.handlers),
        )
    return ()


class CodeBridge(LegacyStateBridge):
    def __init__(
        self,
        query: PtaQuery | None,
        budget: AnalysisBudget,
        context: NativeContext,
        scope: Scope | None,
        initial: AIState,
        driver: SharedDriver,
        *,
        scope_ir: IRScope | None = None,
    ) -> None:
        super().__init__(query, budget, context, scope, initial, driver)
        self.scope_ir: IRScope | None = scope_ir
        self.native_state = query._state if _has_native_state(query) else None
        self.manager = self.native_state.scope_manager if self.native_state is not None else None
        self.ir_scopes = (
            tuple(self.manager.get_scopes())
            if self.manager is not None
            else (() if scope_ir is None else (scope_ir,))
        )
        for module, tree in self.driver.trees.items():

            def visit(nodes: Iterable[ast.stmt], prefix: str, owner_module: str = module) -> None:
                for node in nodes:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        qualname = prefix + "." + node.name
                        self.source_index.setdefault((owner_module, qualname), []).append(node)
                        visit(node.body, qualname, owner_module)
                    else:
                        for body in _nested_bodies(node):
                            visit(body, prefix, owner_module)

            visit(tree.body, module)

    @override
    def source_node(self, scope_ir: IRScope) -> SourceDefinition | None:
        source = scope_ir.get_source_snapshot()
        if source is None:
            return None
        if not isinstance(scope_ir, (IRModule, IRFunc, IRClass)):
            return None
        node = scope_ir.get_ast()
        candidates = self.source_index.get((source.module, scope_ir.get_qualname()), ())
        matches = [item for item in candidates if source_position(item) == source_position(node)]
        if len(matches) == 1:
            return matches[0]
        return candidates[0] if len(candidates) == 1 else None

    @override
    def source_ir(self, module: str, qualname: str, node: ast.AST | None) -> IRScope | None:
        candidates = []
        for scope in self.ir_scopes:
            source = scope.get_source_snapshot()
            if (
                source is None
                or source.module != module
                or source.digest != self.driver.modules[module].digest
                or scope.get_qualname() != qualname
            ):
                continue
            original = self.source_node(scope)
            if (
                node is None
                or original is not None
                and source_position(node) == source_position(original)
            ):
                candidates.append(scope)
        return candidates[0] if len(candidates) == 1 else None

    @override
    def native_parent(self, prototype: IRScope) -> Scope | None:
        if self.native_state is None:
            return None
        parent = self.manager.father.get(prototype) if self.manager is not None else None
        while parent is not None:
            scopes = self.native_state.get_scopes_for_ir(parent)
            if scopes:
                return next((scope for scope in scopes if scope.context == self.context), scopes[0])
            parent = self.manager.father.get(parent) if self.manager is not None else None
        return None

    def defining_value(self, node: ast.expr, function_identifier: ObjectId) -> Value:
        # Literal syntax is a pure declaration and can be imported without
        # replaying definitions/calls. Other expressions need retained facts.
        if isinstance(node, ast.Constant):
            return Value.constant(source_constant(node))
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            values = [self.defining_value(item, function_identifier) for item in node.elts]
            line, column = source_position(node)
            identifier = ObjectId(f"bridge-default:{function_identifier}:{line}:{column}")
            self.store.heap.setdefault(
                identifier,
                HeapObject(
                    type(node).__name__.lower(),
                    dict[FieldKey, Value](enumerate(values)),
                    metadata={"length": len(values)},
                ),
            )
            return Value.ref(identifier)
        if isinstance(node, ast.Dict) and all(isinstance(key, ast.Constant) for key in node.keys):
            line, column = source_position(node)
            identifier = ObjectId(f"bridge-default:{function_identifier}:{line}:{column}")
            fields: dict[FieldKey, Value] = {}
            for key_node, value_node in zip(node.keys, node.values, strict=False):
                if not isinstance(key_node, ast.Constant):
                    raise BoundaryUnavailable("Literal dictionary key unavailable")
                fields[_literal_key(source_constant(key_node))] = self.defining_value(
                    value_node, function_identifier
                )
            self.store.heap.setdefault(
                identifier, HeapObject("dict", fields, metadata={"length": len(node.keys)})
            )
            return Value.ref(identifier)
        if isinstance(node, ast.Name):
            info = self.driver.functions.get(function_identifier)
            source = self.scope_ir.get_source_snapshot() if self.scope_ir is not None else None
            module = (
                info.module if info is not None else source.module if source is not None else None
            )
            if module is not None:
                value = self.store.cells.get(CellId(f"global:{module}:{node.id}"))
                if value is not None:
                    return value
        # Merely importing an unused Native callable must not execute or replay
        # its definition. Unavailable defining-time values make that function
        # a havoc callable; consuming it invokes the ordinary unknown transfer.
        self.store.heap[function_identifier].havoc = True
        return Value.unknown()

    @override
    def register_function(self, obj: FunctionObject, identifier: ObjectId) -> None:
        node = self.source_node(obj.ir)
        source = obj.ir.get_source_snapshot()
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or source is None:
            self.reasons.add("bridge_callable_source_unavailable")
            self.store.heap[identifier].havoc = True
            return
        module = source.module
        qualname = obj.ir.get_qualname()
        local, globals_, nonlocals_, generator = classify_bindings(node)
        activation = str(hash(obj))
        closure: list[tuple[str, CellId]] = []
        for (owner, name), old in self.initial.shared_bindings.items():
            if owner == f"$closure-id:{qualname}@{activation}":
                ids = old.get_const_strs()
                if ids is not None and len(ids) == 1:
                    closure.append((name, CellId(next(iter(ids)))))
        if not closure:
            bindings = self.query.get_function_cell_vars(obj) if self.query is not None else {}
            from pythonstan.compat.legacy_ai.value_domain import AbsVal

            for name, points in bindings.items():
                cell = CellId(f"bridge-closure:{activation}:{name}")
                self.store.cells[cell] = self.value(AbsVal.from_addrs(frozenset(points)))
                closure.append((name, cell))
        # Install the prototype before processing defaults that may recurse.
        info = FunctionInfo(
            identifier,
            node,
            module,
            qualname,
            tuple(closure),
            (),
            (),
            local,
            globals_,
            nonlocals_,
            generator,
            isinstance(node, ast.AsyncFunctionDef),
        )
        self.driver.functions[identifier] = info
        parameters = (*node.args.posonlyargs, *node.args.args)
        defaults = []
        for index, expression in enumerate(node.args.defaults):
            name = parameters[len(parameters) - len(node.args.defaults) + index].arg
            retained = [
                old
                for (owner, param), old in self.initial.shared_bindings.items()
                if owner == f"$default:{qualname}@{activation}" and param == name
            ]
            value = (
                self.driver.ops.join_all(self.value(old) for old in retained)
                if retained
                else self.defining_value(expression, identifier)
            )
            defaults.append(value)
            self.store.heap[identifier].fields[self.default_slot(index)] = value
        kw_defaults = []
        for parameter, keyword_expression in zip(
            node.args.kwonlyargs, node.args.kw_defaults, strict=False
        ):
            if keyword_expression is None:
                continue
            retained = [
                old
                for (owner, param), old in self.initial.shared_bindings.items()
                if owner == f"$default:{qualname}@{activation}" and param == parameter.arg
            ]
            value = (
                self.driver.ops.join_all(self.value(old) for old in retained)
                if retained
                else self.defining_value(keyword_expression, identifier)
            )
            kw_defaults.append((parameter.arg, value))
            self.store.heap[identifier].fields[self.keyword_slot(parameter.arg)] = value
        info.defaults = tuple(defaults)
        info.kw_defaults = tuple(kw_defaults)
        self.store.heap[identifier].metadata["closure"] = tuple(closure)
        annotations = [
            old
            for (owner, name), old in self.initial.shared_bindings.items()
            if owner == f"$annotation:{qualname}@{activation}" and name == "__annotations__"
        ]
        if annotations:
            from pythonstan.analysis.contracts.private_slots import function_annotations_slot

            self.store.heap[identifier].fields[function_annotations_slot()] = (
                self.driver.ops.join_all(self.value(old) for old in annotations)
            )

    def seed_native_globals(self, module: str) -> None:
        if self.native_state is None:
            return
        from pythonstan.analysis.pointer.kcfa.context import Ctx
        from pythonstan.analysis.pointer.kcfa.variable import Variable
        from pythonstan.compat.legacy_ai.value_domain import AbsVal
        from pythonstan.ir import IRModule

        # Native lowering injects builtin bindings and may lift nested def
        # names. Only original Python bindings belong to the Source namespace.
        bindings = {
            name: set(classify_bindings(tree)[0])
            | {
                global_name
                for item in ast.walk(tree)
                if isinstance(item, ast.Global)
                for global_name in item.names
            }
            for name, tree in self.driver.trees.items()
        }
        for variable, points in tuple(self.native_state._env.items()):
            if not isinstance(variable, Ctx):
                continue
            scope = variable.scope
            content = variable.content
            if scope is None or content is None or not isinstance(scope.stmt, IRModule):
                continue
            if not isinstance(content, Variable):
                continue
            name = content.name
            owner = scope.stmt.get_qualname()
            if name not in bindings.get(owner, set()):
                continue
            cell = CellId(f"global:{owner}:{name}")
            if points and cell not in self.store.cells:
                self.store.cells[cell] = self.value(AbsVal.from_addrs(frozenset(points)))


def source_modules(scope_ir: IRScope, query: PtaQuery | None) -> tuple[SourceModule, ...]:
    """Read producer-captured immutable source, never mutable TAC or disk."""
    snapshot = scope_ir.get_source_snapshot()
    if snapshot is None:
        raise BoundaryUnavailable(
            "source_snapshot_required: private source-less TAC execution is retired"
        )
    modules = {snapshot.module: snapshot}
    state = query._state if _has_native_state(query) else None
    manager = state.scope_manager if state is not None else None
    if manager is not None:
        for scope in manager.get_scopes():
            source = scope.get_source_snapshot()
            if source is not None:
                if source.module in modules and source.digest != modules[source.module].digest:
                    raise BoundaryUnavailable("conflicting source module identity")
                modules[source.module] = source
    return tuple(modules.values())


def execute_scope(
    scope_ir: IRScope,
    initial_state: AIState,
    query: PtaQuery | None,
    budget: AnalysisBudget,
    caller_scope: Scope | None = None,
    call_context: NativeContext | None = None,
    solver_config: SolverConfig | None = None,
    work_ledger: WorkLedgerPort | None = None,
) -> tuple[AISummary, SharedDriver, CodeBridge, Frame]:
    """Execute one existing source scope on the sole maintained engine."""
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext
    from pythonstan.ir import IRModule

    driver = SharedDriver(
        source_modules(scope_ir, query), options(budget, solver_config), work_ledger=work_ledger
    )
    driver.started = time.monotonic()
    driver.scenario = "legacy-shared"
    context = _checked_context(
        call_context
        or (caller_scope.context if caller_scope is not None else None)
        or CallStringContext()
    )
    bridge = CodeBridge(
        query, budget, context, caller_scope, initial_state, driver, scope_ir=scope_ir
    )
    source = scope_ir.get_source_snapshot()
    if source is None:
        raise BoundaryUnavailable("Source scope snapshot unavailable")
    module = source.module
    qualname = scope_ir.get_qualname()
    is_module = isinstance(scope_ir, IRModule)
    node = driver.trees[module] if is_module else bridge.source_node(scope_ir)
    if node is None:
        raise BoundaryUnavailable("source scope prototype unavailable")
    local, globals_, nonlocals_, generator = classify_bindings(node)
    frame = Frame(
        module,
        qualname,
        ContextId(()),
        FrameId("legacy-entry:" + qualname),
        local,
        (),
        globals_,
        nonlocals_,
        is_module=is_module,
    )
    store = bridge.import_state(frame)
    if not is_module:
        bridge.seed_native_globals(module)
        closure = []
        loads = {
            item.id
            for item in ast.walk(node)
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
        }
        for name in loads - local - globals_:
            matches = [
                cell
                for cell in store.cells
                if not cell.startswith("global:") and cell.endswith(":" + name)
            ]
            if len(matches) == 1:
                closure.append((name, matches[0]))
        frame = replace(frame, closure=tuple(closure))
        for name in local:
            store.cells.setdefault(frame.cell(name), Value(unbound=True))
        for name in nonlocals_:
            if name not in dict(closure):
                cell = CellId(f"bridge-nonlocal:{qualname}:{name}")
                store.cells[cell] = Value.unknown()
                closure.append((name, cell))
                bridge.reasons.add("bridge_nonlocal_unavailable")
        frame = replace(frame, closure=tuple(closure))
    store.initialized[module] = "initializing" if is_module else "initialized"
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.type_params:
        return (
            bridge.summary(
                driver.transfer.unknown(store, node, "bridge_pep695_type_parameters"), frame
            ),
            driver,
            bridge,
            frame,
        )
    driver._request(UnitId(frame.identity), frame, tuple(node.body), store, is_module, None)
    try:
        driver._solve()
    except BudgetStop as exc:
        driver.solver_status = "budget_exhausted"
        driver.open("budget:" + str(exc))
        bridge.reasons.add("bridge_budget")
    flows = list(driver.units[UnitId(frame.identity)].outcomes)
    if driver.solver_status != "converged":
        flows.extend(driver.transfer.unknown(store, node, "bridge_incomplete_execution"))
    result = bridge.summary(flows, frame)
    return result, driver, bridge, frame


def execute_intrinsic(
    name: str,
    bindings: Sequence[ArgBinding],
    query: PtaQuery | None,
    budget: AnalysisBudget,
    caller_scope: Scope | None = None,
    call_context: NativeContext | None = None,
    solver_config: SolverConfig | None = None,
    work_ledger: WorkLedgerPort | None = None,
) -> AISummary:
    from pythonstan.analysis.pointer.kcfa.context import CallStringContext
    from pythonstan.compat.legacy_ai.state import AIState

    source = SourceModule("intrinsic", "intrinsic.py", "")
    driver = SharedDriver((source,), options(budget, solver_config), work_ledger=work_ledger)
    driver.started = time.monotonic()
    driver.scenario = "legacy-intrinsic"
    initial = AIState.from_arg_bindings(list(bindings), query, budget)
    bridge = CodeBridge(
        query,
        budget,
        _checked_context(
            call_context
            or (caller_scope.context if caller_scope is not None else None)
            or CallStringContext()
        ),
        caller_scope,
        initial,
        driver,
    )
    frame = Frame(
        "intrinsic", "intrinsic", ContextId(()), FrameId("legacy-intrinsic"), frozenset(initial.env)
    )
    store = bridge.import_state(frame)
    arguments = [bridge.value(initial.env[binding.param_name]) for binding in bindings]
    expression = ast.parse("intrinsic()").body[0]
    if not isinstance(expression, ast.Expr):
        raise BoundaryUnavailable("Intrinsic source expression unavailable")
    node = expression.value
    try:
        driver.tick(node)
        flows = driver.transfer.builtin(name, arguments, (), store, frame, node)
    except BudgetStop:
        flows = driver.transfer.unknown(store, node, "bridge_intrinsic_budget")
    return bridge.summary(flows, frame)
