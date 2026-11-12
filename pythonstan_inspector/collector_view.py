"""Passive, bounded kwargs linkage projected only by the native adapter.

Rows originate in scoped call constraints joined to admitted native call edges.
Collector existence is deliberately not an enumeration premise. Facts and PFG
dependencies stay in the existing views; no expected outputs are recorded here.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from .capture_dto import CaptureProjection, CaptureReference, CaptureView, OwnerCoverage

if TYPE_CHECKING:
    from pythonstan.analysis.pointer.kcfa.solver import PointerSolver
    from pythonstan.analysis.pointer.kcfa.state import NativePointsKey
    from pythonstan.graph.call_graph.call_edge import CallEdge

from pythonstan.analysis.pointer.kcfa import points_to_set
from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint
from pythonstan.analysis.pointer.kcfa.context import (
    CallSite,
    Ctx,
    NativeContext,
    Scope,
    SummaryContext,
)
from pythonstan.analysis.pointer.kcfa.heap_model import Field, HeapRegisterKey
from pythonstan.analysis.pointer.kcfa.object import (
    AbstractObject,
    BuiltinMethodObject,
    ClassObject,
    FunctionObject,
)
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
from pythonstan.analysis.pointer.kcfa.protocol_context import ProtocolKeyContext
from pythonstan.analysis.pointer.kcfa.variable import FieldAccess, Variable, VariableKind
from pythonstan.ir.ir_statements import IRFunc

from .contract import canonical_bytes

VIEW = "kwargs_model"
SEMANTIC_VERSION = "registered_keyword_collector_v1"
_MASKS = ("objs_mask", "classmethods_mask", "instancemethods_mask")
_SIGNATURE_FIELDS = (
    "version",
    "posonly",
    "positional",
    "kwonly",
    "vararg",
    "kwarg",
    "positional_defaults",
    "kwonly_defaults",
)


def _unresolved(status: str, reason: str) -> CaptureReference:
    return {"ref": None, "status": status, "reason": reason}


def _signature(scope: Scope) -> dict[str, object]:
    """Full finite formal descriptor; no display prefix is a full signature."""
    if not isinstance(scope.stmt, IRFunc):
        return {"version": 1, "coverage": "unsupported", "sha256": None}
    args = scope.stmt.args
    groups = (args.posonlyargs, args.args, args.kwonlyargs)
    if sum(map(len, groups)) > 64 or len(args.kw_defaults) > 64:
        return {"version": 1, "coverage": "partial", "sha256": None}
    names = [[arg.arg for arg in group] for group in groups]
    extras = [args.vararg.arg if args.vararg else None, args.kwarg.arg if args.kwarg else None]
    if any(
        type(name) is not str or not name or len(name) > 512
        for name in [*sum(names, []), *[x for x in extras if x is not None]]
    ):
        return {"version": 1, "coverage": "partial", "sha256": None}
    result: dict[str, object] = dict(
        zip(
            _SIGNATURE_FIELDS,
            (
                1,
                *names,
                *extras,
                len(args.defaults),
                [value is not None for value in args.kw_defaults],
            ),
            strict=False,
        )
    )
    result["sha256"] = hashlib.sha256(canonical_bytes(result)).hexdigest()
    result["coverage"] = "complete"
    return result


class _Export:
    def __init__(
        self, solver: PointerSolver, projection: CaptureProjection, views: dict[str, CaptureView]
    ) -> None:
        self.solver: PointerSolver = solver
        self.p: CaptureProjection = projection
        self.views: dict[str, CaptureView] = views
        self.state = solver.state
        self.index_entries = 0
        self.reason: str | None = None
        self.field_owners: dict[tuple[AbstractObject, Field], Ctx[FieldAccess] | None] = {}
        self.fields: dict[AbstractObject, list[Field]] = {}
        self.calls: dict[tuple[Scope, CallSite], list[CallConstraint]] = {}
        self.consumers: dict[
            AbstractObject,
            list[tuple[Scope, NativeContext | None, CallConstraint, BuiltinMethodObject]],
        ] = {}
        self.complete: dict[str, bool] = {}

    def account(self, size: int = 96) -> bool:
        # Temporary indexes are capture-local, charged conservatively at their
        # peak, and bounded independently of the number of output rows.
        if self.reason or not self.p.available(size):
            return False
        if self.index_entries >= self.p.request.max_entities:
            self.reason = "model_index_entity_budget"
            return False
        self.index_entries += 1
        self.p.bytes_used += size
        return True

    def ref(self, obj: object, missing: str = "no_native_reference") -> CaptureReference:
        if obj is None:
            return _unresolved("missing_in_engine", missing)
        identity = self.p.entity(obj)
        if identity is None:
            return _unresolved("omitted_by_capture", self.p.stopped or "entity_budget")
        return {"ref": identity, "status": "resolved"}

    def owner_ref(self, owner: Ctx[Variable] | None) -> CaptureReference:
        if owner is None:
            return _unresolved(
                "missing_in_engine" if self.complete.get("raw_heap_variables") else "unknown",
                "raw_variable_owner_absent",
            )
        return self.ref(owner)

    def _field_owner(self, owner: object) -> None:
        if not isinstance(owner, Ctx) or not isinstance(owner.content, FieldAccess):
            return
        key = (owner.content.obj, owner.content.field)
        if key not in self.field_owners:
            self.field_owners[key] = owner
        elif self.field_owners[key] != owner:
            # A raw field cannot be silently assigned one of two contexts.
            self.field_owners[key] = None

    def scan(self) -> None:
        complete = True
        for owner in self.state._env:
            if not self.account():
                complete = False
                break
            self._field_owner(owner)
            if isinstance(owner, NormalNode):
                self._field_owner(owner.var)
        self.complete["environment"] = complete
        complete = True
        for node in self.state._pointer_flow_graph.nodes:
            if not self.account():
                complete = False
                break
            if isinstance(node, NormalNode):
                self._field_owner(node.var)
        self.complete["field_owners"] = complete and self.complete["environment"]
        complete = True
        for registers in self.state._heap.heap.values():
            if type(registers) is not dict:
                complete = False
                continue
            for owner in registers.values():
                if not self.account():
                    complete = False
                    break
                if not isinstance(owner, Ctx) or not isinstance(owner.content, Variable):
                    complete = False
            if self.reason or self.p.stopped:
                break
        self.complete["raw_heap_variables"] = complete
        complete = True
        for (obj, field), access in self.state._field_accesses.items():
            if not self.account():
                complete = False
                break
            if not isinstance(access, FieldAccess) or access.obj != obj or access.field != field:
                complete = False
            self.fields.setdefault(obj, []).append(field)
        self.complete["field_slots"] = complete
        complete = True
        for scope, constraint in self.state._constraints._constraints:
            if not self.account():
                complete = False
                break
            if isinstance(constraint, CallConstraint):
                self.calls.setdefault((scope, constraint.call_site), []).append(constraint)
        self.complete["constraints"] = complete
        complete = True
        for scope, context, call, obj in self.solver._builtin_invocations.values():
            if not self.account():
                complete = False
                break
            if isinstance(obj, BuiltinMethodObject) and obj.method_name in ("keys", "values"):
                self.consumers.setdefault(obj.receiver, []).append((scope, context, call, obj))
        self.complete["builtin_invocations"] = complete

    def variable(
        self,
        scope: Scope,
        context: NativeContext | None,
        variable: Variable | None = None,
        *,
        name: object = None,
    ) -> Ctx[Variable] | None:
        """Mirror audited raw storage routing, without a native constructor."""
        kind = variable.kind if variable is not None else VariableKind.LOCAL
        name = variable.name if variable is not None else name
        if type(name) is not str:
            return None
        owner_scope, owner_context = scope, context
        if kind == VariableKind.GLOBAL:
            owner_scope, owner_context = scope.module, scope.module.context
        elif kind in (VariableKind.CELL, VariableKind.NONLOCAL):
            obj = scope.obj
            captured_object: object = vars(obj).get("func_obj", obj)
            aliases = (
                self.state._heap.cell_vars
                if kind == VariableKind.CELL
                else self.state._heap.nonlocal_vars
            )
            captured = (
                aliases.get(captured_object, {}).get(name)
                if isinstance(captured_object, (FunctionObject, ClassObject))
                else None
            )
            if captured is not None:
                return captured if isinstance(captured, Ctx) else None
            owner_scope, owner_context = scope.parent, scope.parent.context

        def lookup(scoped: Scope, contextual: NativeContext | None) -> Ctx[Variable] | None:
            if kind == VariableKind.GLOBAL:
                key: HeapRegisterKey = (scoped.module.stmt,)
            elif kind == VariableKind.TEMPORARY and name == "$return":
                key = (contextual, scoped.obj if scoped.obj is not None else scoped.module.stmt)
            else:
                key = (contextual, scoped.stmt)
            owner = self.state._heap.heap.get(key, {}).get(name)
            return owner if isinstance(owner, Ctx) and isinstance(owner.content, Variable) else None

        if name.startswith("$const") and scope != scope.module:
            found = lookup(scope.module, scope.module.context)
            if found is not None:
                return found
        return lookup(owner_scope, owner_context)

    def objects(self, owner: NativePointsKey | None) -> tuple[list[AbstractObject], bool]:
        values: list[AbstractObject] = []
        seen: set[AbstractObject] = set()
        complete = True
        points = self.state._env.get(owner) if owner is not None else None
        if points is None:
            return values, complete
        table = points_to_set._global_table
        for field in _MASKS:
            mask: object = getattr(points, field)
            if type(mask) is not int or mask < 0:
                return values, False
            while mask:
                if not self.account(32):
                    return values, False
                low = mask & -mask
                mask ^= low
                index = low.bit_length() - 1
                if table is None or index >= len(table._id_to_obj):
                    complete = False
                    continue
                obj = table._id_to_obj[index]
                if obj not in seen:
                    seen.add(obj)
                    values.append(obj)
        return values, complete

    def field_ref(self, obj: AbstractObject, field: Field | None) -> CaptureReference:
        owner = self.field_owners.get((obj, field)) if field is not None else None
        complete = self.complete.get("field_owners") and self.complete.get("field_slots")
        if owner is None:
            if field is not None and (obj, field) in self.field_owners:
                return _unresolved("unknown", "ambiguous_qualified_field_owners")
            return _unresolved(
                "missing_in_engine" if complete else "unknown", "qualified_field_owner_absent"
            )
        ref = self.ref(owner)
        # Existing empty owners can otherwise be invisible to ctx_points:
        # get_field creates a real owner/node without necessarily adding facts.
        view = self.views.get("ctx_points")
        if (
            view is not None
            and ref["status"] == "resolved"
            and self.complete.get("environment")
            and owner not in self.state._env
            and view["coverage"].get("registry") == "complete"
        ):
            owners = view["coverage"]["owners"]
            key = str(ref["ref"])
            coverage: OwnerCoverage = {
                "fields": {"facts": "complete"},
                "evidence": "audited_existing_owner_empty_native_env",
            }
            if key not in owners:
                size = len(canonical_bytes({key: coverage}))
                if self.p.available(size):
                    owners[key] = coverage
                    self.p.bytes_used += size
            view["coverage"]["enumerated_owners"] = len(owners)
        return ref

    def channels(
        self, obj: AbstractObject
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], bool]:
        channels: list[dict[str, object]] = []
        other: list[dict[str, object]] = []
        present = set()
        for field in self.fields.get(obj, ()):
            if not self.account(48):
                return channels, other, False
            kind = field.kind.value
            key: str | None = None
            if kind == "key" and field.key_type is None and type(field.name) is str:
                kind, key = "key", field.name
                if len(key) > 512:
                    other.append({"field_kind": "key", "owner": self.field_ref(obj, field)})
                    continue
            elif kind == "elem":
                kind = "aggregate"
            elif kind == "class_metadata" and field.name in (
                "native_mapping_keys",
                "native_mapping_nonscalar_keys",
            ):
                kind = (
                    "native_keys"
                    if field.name == "native_mapping_keys"
                    else "native_nonscalar_keys"
                )
            else:
                other.append({"field_kind": field.kind.value, "owner": self.field_ref(obj, field)})
                continue
            present.add(kind)
            channels.append(
                {
                    "kind": kind,
                    "key": key,
                    "owner": self.field_ref(obj, field),
                    "field_exists": True,
                }
            )
        for kind in ("aggregate", "native_keys", "native_nonscalar_keys"):
            if kind not in present:
                channels.append(
                    {
                        "kind": kind,
                        "key": None,
                        "owner": self.field_ref(obj, None),
                        "field_exists": False,
                    }
                )
        return (
            channels,
            other,
            bool(self.complete.get("field_slots") and self.complete.get("field_owners")),
        )

    def object_rows(
        self, owner: NativePointsKey | None
    ) -> tuple[list[dict[str, object]], list[AbstractObject], bool]:
        objects, complete = self.objects(owner)
        rows: list[dict[str, object]] = []
        for obj in objects:
            ref = self.ref(obj)
            if ref["status"] != "resolved":
                complete = False
                continue
            channels, other, full = self.channels(obj)
            rows.append({"object": ref["ref"], "channels": channels, "other_fields": other})
            complete = complete and full
        return rows, objects, complete

    def consumer_rows(self, objects: list[AbstractObject]) -> tuple[list[dict[str, object]], bool]:
        rows: list[dict[str, object]] = []
        for receiver in objects:
            for scope, context, call, obj in self.consumers.get(receiver, ()):
                if not self.account():
                    return rows, False
                receiver_ref, scope_ref = self.ref(receiver), self.ref(scope)
                if receiver_ref["status"] != "resolved" or scope_ref["status"] != "resolved":
                    return rows, False
                # Existing field descriptor is selected structurally, never
                # manufactured to query the native state.
                channel = "native_keys" if obj.method_name == "keys" else "aggregate"
                slots, _, _ = self.channels(receiver)
                selected = next(
                    (slot["owner"] for slot in slots if slot["kind"] == channel),
                    _unresolved("unknown", "channel_not_decoded"),
                )
                rows.append(
                    {
                        "model": "dict." + obj.method_name,
                        "scope": scope_ref["ref"],
                        "callsite": self.ref(call.call_site),
                        "receiver": receiver_ref["ref"],
                        "receiver_owner": self.owner_ref(
                            self.variable(scope, context, obj.receiver_var)
                        ),
                        "input_owner": selected,
                        "target": self.owner_ref(self.variable(scope, context, call.target))
                        if call.target
                        else _unresolved("not_captured", "call_has_no_target"),
                        "channel": channel,
                        "stage": "registered_model_invocation",
                    }
                )
        return rows, bool(self.complete.get("builtin_invocations"))

    def row(
        self, invocation: int, edge: CallEdge[Ctx[CallSite], Scope], call: CallConstraint
    ) -> dict[str, object] | None:
        caller, callee = edge.callsite.scope, edge.callee
        if caller is None:
            return None
        essential = [
            self.ref(obj)
            for obj in (
                edge.callsite,
                caller,
                call.call_site,
                call.call_site.statement,
                callee,
                callee.obj,
            )
        ]
        if any(ref["status"] != "resolved" for ref in essential):
            return None
        signature = _signature(callee)
        # The row already exists independently of this optional lookup.
        parameter = self.variable(callee, callee.context, name=signature.get("kwarg"))
        collector, objects, full = self.object_rows(parameter)
        expanded: list[dict[str, object]] = []
        keywords: list[dict[str, object]] = []
        positional: list[CaptureReference] = []
        inputs_full = bool(self.complete.get("raw_heap_variables"))
        for variable in call.args:
            if not self.account(32):
                inputs_full = False
                break
            positional.append(
                self.owner_ref(self.variable(caller, edge.callsite.context, variable))
            )
        for name, variable in call.kwargs:
            if not self.account(48) or type(name) is not str or len(name) > 512:
                inputs_full = False
                break
            owner = self.variable(caller, edge.callsite.context, variable)
            ref = self.owner_ref(owner)
            keywords.append({"name": name, "value": ref})
            if name == "**":
                mappings, _, mapping_full = self.object_rows(owner)
                expanded.append(
                    {
                        "input": ref,
                        "objects": mappings,
                        "coverage": "complete" if mapping_full else "partial",
                    }
                )
        consumers, consumers_full = self.consumer_rows(objects)
        row: dict[str, object] = dict(
            zip(
                ("owner", "caller_scope", "callsite", "statement", "callee_scope", "callee_object"),
                (ref["ref"] for ref in essential),
                strict=False,
            )
        )
        row.update(
            invocation=invocation,
            call_kind=edge.kind.name.lower(),
            semantic_partition=self.ref(call.semantic_partition)
            if call.semantic_partition is not None
            else _unresolved("not_captured", "constraint_has_no_semantic_partition"),
            callee_input=self.owner_ref(self.variable(caller, edge.callsite.context, call.callee)),
            target=self.owner_ref(self.variable(caller, edge.callsite.context, call.target))
            if call.target
            else _unresolved("not_captured", "call_has_no_target"),
            positional=positional,
            keywords=keywords,
            signature=signature,
            collector={
                "parameter": self.owner_ref(parameter),
                "objects": collector,
                "coverage": "complete"
                if full and self.complete.get("raw_heap_variables")
                else "partial",
            },
            input_mappings=expanded,
            consumers=consumers,
            coverage={
                "inputs": "complete" if inputs_full else "partial",
                "signature": signature["coverage"],
                "collector": "complete"
                if full and self.complete.get("raw_heap_variables")
                else "partial",
                "channels": "complete" if full else "partial",
                "consumers": "complete" if consumers_full else "partial",
            },
        )
        return row


def _partition(context: object) -> object:
    while isinstance(context, SummaryContext):
        context = context.inner
    return context.partition if isinstance(context, ProtocolKeyContext) else None


def export_kwargs_model(
    solver: PointerSolver, projection: CaptureProjection, views: dict[str, CaptureView]
) -> CaptureView:
    """Export after base views, before descriptors are sealed by the adapter."""
    export = _Export(solver, projection, views)
    view: CaptureView = {
        "version": 1,
        "semantic_version": SEMANTIC_VERSION,
        "fidelity": "exact",
        "scope": "all_current_state",
        "operations": ["enumerate"],
        "dimensions": [
            "invocation",
            "caller_scope",
            "callsite",
            "callee_scope",
            "inputs",
            "signature",
            "collector",
            "channels",
            "consumers",
        ],
        "authority": "scoped ConstraintManager._constraints joined to PointerCallGraph.edges; existing raw owners",
        "dependencies": ["ctx_points", "pfg_edges", "call_edges"],
        "owner_fields": ["invocations"],
        "coverage": {
            "registry": "partial",
            "owners": {},
            "fields": {},
            "enumerated_owners": 0,
            "enumerated_rows": 0,
        },
        "rows": [],
    }
    export.scan()
    complete = True
    for edge in solver.state._call_graph.edges:
        if not export.account():
            complete = False
            break
        if not isinstance(edge.callsite, Ctx) or not isinstance(edge.callsite.scope, Scope):
            complete = False
            continue
        calls = export.calls.get((edge.callsite.scope, edge.callsite.content), ())
        for call in calls:
            if (
                call.semantic_partition is not None
                and _partition(edge.callee.context) != call.semantic_partition
            ):
                continue
            row = export.row(len(view["rows"]) + 1, edge, call)
            if row is None or not projection.row(view["rows"], row):
                complete = False
                break
            view["coverage"]["owners"][str(row["owner"])] = {"fields": {"invocations": "complete"}}
        if not complete:
            break
    export.complete["admitted_calls"] = complete
    complete = (
        complete and all(export.complete.values()) and not export.reason and not projection.stopped
    )
    view["coverage"]["fields"] = {
        name: "complete" if value else "partial" for name, value in export.complete.items()
    }
    view["coverage"]["registry"] = "complete" if complete else "partial"
    if not complete:
        for owner in view["coverage"]["owners"].values():
            owner["fields"]["invocations"] = "partial"
        projection.omit(
            VIEW, export.reason or projection.stopped or "model_registry_or_reference_partial"
        )
    view["coverage"]["enumerated_owners"] = len(view["coverage"]["owners"])
    view["coverage"]["enumerated_rows"] = len(view["rows"])
    view["capture"] = {
        "temporary_index_entries": export.index_entries,
        "temporary_index_entry_limit": projection.request.max_entities,
        "accounting": "temporary_references_and_nested_rows_charged_to_shared_capture_budget",
    }
    views[VIEW] = view
    return view
