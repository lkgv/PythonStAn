"""Bounded, passive projection of the current k-CFA engine.

Only this module understands engine objects. The result owns encoded bytes;
no reader, index, query implementation, engine callback or live handle crosses
the capture boundary. Raw storage is read instead of nominal query helpers,
which may create variables, fields, edges, or abstract objects.
"""
import ast
from collections import deque
import math
import time
import uuid

from .contract import CaptureRequest, InspectorError, SealedSnapshot, canonical_bytes
from .native_identity_projection import project_identity
from .collector_view import export_kwargs_model

from pythonstan.analysis.pointer.kcfa import points_to_set as pts_storage
from pythonstan.analysis.pointer.kcfa.context import (
    AbstractContext, CallSite, Ctx, Scope, SummaryContext,
)
from pythonstan.analysis.pointer.kcfa.object import AbstractObject, AllocSite
from pythonstan.analysis.pointer.kcfa.protocol_context import KeywordPartition, ProtocolKeyContext
from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import (
    PointerFlowNode, NormalNode, SelectorNode, PointerFlowKind,
)
from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind, FieldAccess
from pythonstan.ir.ir_statements import IRStatement, IRScope, IRModule


_VIEWS = ("node_points", "ctx_points", "pfg_edges", "call_edges")
_LANES = (("objects", "objs_mask"), ("classmethods", "classmethods_mask"),
          ("instancemethods", "instancemethods_mask"))


def _text(value, limit=512):
    """Descriptions never invoke repr/str on engine or user values."""
    if type(value) is not str:
        return None
    bounded = value[:limit]
    try:
        bounded.encode("utf-8")
    except UnicodeError:
        return None
    return bounded


def _scalar(value):
    if value is None or type(value) in (bool, int):
        # Avoid materializing arbitrarily large integers in diagnostic JSON.
        return value if value is None or type(value) is bool or -(2 ** 63) <= value < 2 ** 63 else None
    if type(value) is float:
        return value if math.isfinite(value) else None
    return _text(value)


def _described_text(target, name, value):
    target[name] = _text(value)
    if value is not None:
        if target[name] is None:
            target[name + "_coverage"] = "opaque"
        elif len(value) > 512:
            target[name + "_coverage"] = "partial"


def _described_scalar(target, name, value):
    target[name] = _scalar(value)
    if value is not None:
        if target[name] is None:
            target[name + "_coverage"] = "opaque"
        elif type(value) is str and len(value) > 512:
            target[name + "_coverage"] = "partial"


def _variable_description(variable):
    # Translator temporaries can embed id(stmt). Their native names are neither
    # durable identities nor safe alignment keys; local entity refs preserve
    # distinctions without exporting those process addresses.
    opaque = variable.kind == VariableKind.TEMPORARY or not variable.name.isidentifier()
    projected_name = "<synthetic>" if opaque else _text(variable.name)
    return {"name": projected_name,
            "variable_kind": variable.kind.value,
            "name_coverage": "opaque" if opaque or projected_name is None else
                             "partial" if len(variable.name) > 512 else "exact"}


def _metadata_tree(value, request, deadline):
    """Own a bounded caller DTO, never walk arbitrary objects or mappings.

    The byte budget includes JSON punctuation/escaping. Cooperative deadline
    checks bound traversal; this is not an OS-level preemptive timeout.
    """
    if value is None:
        return {}
    if type(value) is not dict:
        raise InspectorError("capture metadata must be a primitive dict tree")
    byte_limit = min(65536, request.max_bytes // 4)
    used_bytes = 0
    nodes = 0

    def account(size):
        nonlocal used_bytes
        used_bytes += size
        if used_bytes > byte_limit:
            raise InspectorError("capture metadata byte budget exhausted")

    def own(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 4096 or depth > 32:
            raise InspectorError("capture metadata node or depth budget exhausted")
        if time.monotonic() > deadline:
            raise InspectorError("capture metadata deadline exhausted")
        kind = type(item)
        if kind is dict:
            if len(item) > 4096:
                raise InspectorError("capture metadata node budget exhausted")
            account(2 + max(0, len(item) - 1))
            result = {}
            for key, child in item.items():
                if type(key) is not str or len(key) > byte_limit:
                    raise InspectorError("capture metadata keys must be bounded strings")
                try:
                    key.encode("utf-8")
                    encoded_key = canonical_bytes(key)
                except UnicodeError as exc:
                    raise InspectorError("capture metadata strings must be valid UTF-8") from exc
                account(len(encoded_key) + 1)
                result[key] = own(child, depth + 1)
            return result
        if kind in (list, tuple):
            if len(item) > 4096:
                raise InspectorError("capture metadata node budget exhausted")
            account(2 + max(0, len(item) - 1))
            return [own(child, depth + 1) for child in item]
        if not (item is None or kind in (str, int, bool, float)):
            raise InspectorError("capture metadata contains a non-primitive value")
        if kind is str and len(item) > byte_limit:
            raise InspectorError("capture metadata string byte budget exhausted")
        if kind is int and not -(2 ** 63) <= item < 2 ** 63:
            raise InspectorError("capture metadata integer exceeds signed64 range")
        if kind is float and not math.isfinite(item):
            raise InspectorError("capture metadata float must be finite")
        try:
            if kind is str:
                item.encode("utf-8")
            encoded_item = canonical_bytes(item)
        except UnicodeError as exc:
            raise InspectorError("capture metadata strings must be valid UTF-8") from exc
        account(len(encoded_item))
        return item

    return own(value, 0)


def _ast_of(stmt):
    """Known IR wrappers keep their already-lowered AST in .stmt."""
    node = vars(stmt).get("stmt") if isinstance(stmt, (IRStatement, IRScope)) else None
    return node if isinstance(node, ast.AST) else None


def _origin(stmt, scope=None):
    result = {}
    if isinstance(stmt, (IRScope, IRStatement)):
        raw = vars(stmt)
        for name in ("qualname", "name"):
            if type(raw.get(name)) is str:
                _described_text(result, name, raw[name])
        node = _ast_of(stmt)
        if node is not None:
            for source, target in (("lineno", "line"), ("col_offset", "column"),
                                   ("end_lineno", "end_line"), ("end_col_offset", "end_column")):
                value = vars(node).get(source)
                if type(value) is int:
                    _described_scalar(result, target, value)
        if isinstance(stmt, IRModule):
            _described_text(result, "file", raw.get("filename"))
    if isinstance(scope, Scope):
        module = scope._module or scope
        if isinstance(module.stmt, IRModule):
            filename = vars(module.stmt).get("filename")
            _described_text(result, "file", filename)
        qualname = vars(scope.stmt).get("qualname")
        _described_text(result, "scope", qualname)
    losses = [value for key, value in result.items() if key.endswith("_coverage") and value in ("partial", "opaque")]
    if losses:
        result["description_coverage"] = "opaque" if "opaque" in losses else "partial"
    return result


class _Projection:
    """Capture-lifetime identity map, never retained by the engine or DTO."""

    def __init__(self, request):
        self.request = request
        self.deadline = time.monotonic() + request.deadline_seconds
        self.entities = []
        self.ids = {}
        self.pending = deque()
        self.bytes_used = 2048
        self.rows_used = 0
        self.omissions = []
        self.stopped = None

    def available(self, size=0):
        if self.stopped:
            return False
        if time.monotonic() > self.deadline:
            self.stopped = "deadline"
        elif self.bytes_used + size > self.request.max_bytes:
            self.stopped = "byte_budget"
        return self.stopped is None

    def omit(self, view, reason, owner=None):
        # A bounded reason list suffices; it is not a second relation ledger.
        if len(self.omissions) < 32:
            self.omissions.append({"view": view, "reason": reason, "owner": owner})

    def entity(self, obj):
        if obj is None:
            return None
        if not isinstance(obj, (PointerFlowNode, Ctx, AbstractObject, Scope,
                                AbstractContext, KeywordPartition, AllocSite, CallSite, IRScope, IRStatement)):
            return None
        existing = self.ids.get((type(obj), obj))
        if existing is not None:
            return existing
        if len(self.entities) >= self.request.max_entities or not self.available(256):
            self.omit("entities", self.stopped or "entity_budget")
            return None
        local_id = len(self.entities) + 1
        self.ids[(type(obj), obj)] = local_id
        self.entities.append({"id": local_id, "kind": "pending", "label": "",
                              "origin": {}, "attributes": {}, "links": {}})
        self.pending.append((local_id, obj))
        self.bytes_used += 256
        return local_id

    def link(self, record, name, obj):
        ref = self.entity(obj)
        record["links"][name] = ({"ref": ref, "status": "resolved"} if ref is not None else
            {"ref": None, "status": "omitted_by_capture" if obj is not None else "not_captured",
             "reason": self.stopped or ("entity_budget" if obj is not None else "no_native_reference")})

    def row(self, target, row):
        if self.rows_used >= self.request.max_rows or not self.available(128):
            return False
        target.append(row)
        self.rows_used += 1
        self.bytes_used += len(canonical_bytes(row))
        return True

    def finish_entities(self):
        # IDs can participate in cycles; descriptors are filled iteratively.
        while self.pending:
            local_id, obj = self.pending.popleft()
            record = self.entities[local_id - 1]
            _described_text(record["attributes"], "engine_class", type(obj).__name__)
            record["label"] = record["attributes"]["engine_class"] or "entity"
            if not self.available():
                record["kind"] = "opaque"
                record["attributes"]["description_coverage"] = "not_captured"
                continue
            self.describe(record, obj)
            project_identity(self, record, obj)
            # Source and attribute descriptions share the same loss markers.
            # Alignment must not turn a bounded prefix into an exact identity.
            losses = [value for mapping in (record["origin"], record["attributes"])
                      for key, value in mapping.items()
                      if key.endswith("_coverage") and value in ("partial", "opaque", "not_captured")]
            if losses:
                record["attributes"]["description_coverage"] = (
                    "not_captured" if "not_captured" in losses else "opaque" if "opaque" in losses else "partial")
            else:
                record["attributes"].setdefault("description_coverage", "complete")
            self.bytes_used += max(0, len(canonical_bytes(record)) - 256)

    def describe(self, record, obj):
        attrs = record["attributes"]
        if isinstance(obj, PointerFlowNode):
            record["kind"] = "node"
            _described_text(attrs, "node_kind", "normal" if isinstance(obj, NormalNode) else type(obj).__name__)
            if isinstance(obj, NormalNode):
                self.link(record, "variable", obj.var)
                if isinstance(obj.var.scope, Scope):
                    record["origin"] = _origin(obj.var.scope.stmt, obj.var.scope)
                if isinstance(obj.var.content, Variable):
                    attrs.update(_variable_description(obj.var.content))
                    record["label"] = "node:" + (attrs["name"] or "")
            else:
                attrs["transfer_semantics"] = "opaque"
                if isinstance(obj, SelectorNode):
                    _described_scalar(attrs, "least_index", obj.least_index)
            return
        if isinstance(obj, Ctx):
            record["kind"] = "ctx"
            self.link(record, "scope", obj.scope)
            self.link(record, "context", obj.context)
            record["origin"] = _origin(obj.scope.stmt, obj.scope) if isinstance(obj.scope, Scope) else {}
            if isinstance(obj.content, Variable):
                attrs.update(_variable_description(obj.content))
                record["label"] = attrs["name"] or "variable"
            elif isinstance(obj.content, FieldAccess):
                attrs["content_kind"] = "field"
                self.link(record, "object", obj.content.obj)
                field = obj.content.field
                attrs["field_kind"] = field.kind.value
                _described_text(attrs, "field_name", field.name)
                _described_scalar(attrs, "field_index", field.index)
                _described_text(attrs, "field_key_type", field.key_type)
                record["label"] = "field:" + (attrs["field_name"] or attrs["field_kind"])
            elif isinstance(obj.content, CallSite):
                attrs["content_kind"] = "callsite"
                record["label"] = "callsite"
                record["origin"] = _origin(obj.content.statement, obj.scope)
                self.link(record, "content", obj.content)
            else:
                self.link(record, "content", obj.content)
            return
        if isinstance(obj, AbstractObject):
            record["kind"] = "object"
            attrs["alloc_kind"] = obj.alloc_site.kind.value
            attrs["native_summary"] = isinstance(obj.context, SummaryContext)
            self.link(record, "context", obj.context)
            self.link(record, "allocation", obj.alloc_site)
            raw = vars(obj)
            for name in ("builtin_name", "method_name", "function_name", "value"):
                if name in raw:
                    _described_scalar(attrs, name, raw[name])
            for name in ("container_scope", "class_obj", "instance_obj", "ir"):
                if name in raw:
                    self.link(record, name, raw[name])
            scope = raw.get("container_scope")
            record["origin"] = _origin(obj.alloc_site.stmt, scope)
            if record["origin"].get("qualname"):
                record["label"] = record["origin"]["qualname"]
            return
        if isinstance(obj, Scope):
            record["kind"] = "scope"
            _described_text(attrs, "qualname", vars(obj.stmt).get("qualname"))
            record["label"] = attrs["qualname"] or "scope"
            record["origin"] = _origin(obj.stmt, obj)
            for name, value in (("definition", obj.stmt), ("object", obj.obj),
                                ("context", obj.context), ("parent", obj._parent), ("module", obj._module)):
                if value is not None:
                    self.link(record, name, value)
            return
        if isinstance(obj, KeywordPartition):
            record["kind"] = "context_partition"
            attrs["context_model"] = "receiver_key_v1"
            _described_text(attrs, "key_kind", obj.key_kind)
            _described_text(attrs, "key_value", obj.key_value)
            self.link(record, "operation", obj.operation)
            if obj.receiver_source is None:
                attrs["receiver_source_coverage"] = "opaque"
            else:
                self.link(record, "receiver_source", obj.receiver_source)
            return
        if isinstance(obj, AbstractContext):
            record["kind"] = "context"
            raw = vars(obj)
            for name in ("k", "depth", "call_k", "obj_depth"):
                if name in raw:
                    _described_scalar(attrs, name, raw[name])
            # Only audited finite context dimensions; no generic object walker.
            for name in ("call_sites", "alloc_sites", "types", "receivers", "params"):
                if name not in raw:
                    continue
                sequence = raw[name]
                attrs[name + "_length"] = len(sequence)
                for index, value in enumerate(sequence[:64]):
                    if type(value) is tuple:
                        for subindex, subvalue in enumerate(value[:64]):
                            self.link(record, f"{name}:{index}:{subindex}", subvalue)
                        if len(value) > 64:
                            attrs["description_coverage"] = "partial"
                    else:
                        self.link(record, f"{name}:{index}", value)
                if len(sequence) > 64:
                    attrs["description_coverage"] = "partial"
            if isinstance(obj, SummaryContext):
                attrs["native_summary"] = True
                self.link(record, "inner", obj.inner)
            if isinstance(obj, ProtocolKeyContext):
                attrs["context_model"] = "receiver_key_v1"
                self.link(record, "inner", obj.inner)
                self.link(record, "partition", obj.partition)
            return
        if isinstance(obj, (AllocSite, CallSite)):
            record["kind"] = "origin"
            stmt = obj.stmt if isinstance(obj, AllocSite) else obj.statement
            record["origin"] = _origin(stmt)
            self.link(record, "statement", stmt)
            if isinstance(obj, AllocSite):
                attrs["alloc_kind"] = obj.kind.value
                if type(stmt) is str:
                    attrs["token_coverage"] = "opaque"
            else:
                _described_text(attrs, "scope_name", obj.scope_name)
                _described_scalar(attrs, "index", obj.index)
            return
        record["kind"] = "origin"
        record["origin"] = _origin(obj)
        record["label"] = record["origin"].get("qualname") or attrs["engine_class"] or "origin"


def _descriptor(name):
    points = name in ("node_points", "ctx_points")
    return {"version": 1, "fidelity": "exact", "dimensions":
            ["owner", "object", "lane"] if points else ["callsite", "callee", "call_kind"]
            if name == "call_edges" else ["source", "target", "edge_kind"],
            "operations": ["enumerate", "contains"] if points or name == "call_edges" else ["enumerate"],
            "semantic_version": "raw_native_storage_v1",
            "authority": ("PointerAnalysisState._env[PointerFlowNode] plus existing PFG node owners"
                          if name == "node_points" else
                          "PointerAnalysisState._env[Ctx] plus existing HeapModel.heap variable owners"
                          if name == "ctx_points" else "PointerCallGraph.edges" if name == "call_edges"
                          else "PointerFlowGraph.edges"),
            "owner_fields": ["facts"] if points else ["edges"] if name == "call_edges"
                            else ["source", "target", "edge_kind"],
            "dependencies": ["current_object_table_generation", "single_threaded_owned_boundary"],
            "coverage": {"registry": "complete", "owners": {},
                         "enumerated_owners": 0, "enumerated_rows": 0}, "rows": []}


def _trace_reference_mapping(solver, projection, run_id, generation):
    """Join only existing capture objects to live, arena-qualified weak refs.

    Native equality and coincident integers are insufficient for this join.
    The temporary index is bounded by the port's reference limit and charged
    to capture; it never makes an otherwise uncaptured object into an entity.
    """
    port = getattr(solver, "_diagnostics", None)
    if port is None:
        return None
    from pythonstan.analysis.pointer.kcfa.diagnostics import NativeDiagnostics
    result = {"version": 1, "semantic_version": "native_weak_identity_to_snapshot_v1",
              "scope": "already_projected_exact_objects", "rows": [],
              "coverage": {"registry": "complete", "examined": 0, "mapped": 0,
                           "unmapped": 0, "reason_counts": {}}}
    coverage = result["coverage"]

    def missing(reason):
        coverage["unmapped"] += 1
        reasons = coverage["reason_counts"]
        reasons[reason] = reasons.get(reason, 0) + 1

    if type(port) is not NativeDiagnostics:
        coverage.update(registry="unsupported")
        coverage["reason_counts"]["unrecognized_native_port"] = 1
        return result
    wanted = {}
    bindings = port.reference_bindings()
    coverage["registered_refs"] = len(bindings)
    for run_ref, weak, epoch in bindings:
        if not projection.available(256):
            coverage["registry"] = "partial"
            projection.omit("trace_reference_mapping", projection.stopped or "mapping_budget")
            break
        projection.bytes_used += 256
        coverage["examined"] += 1
        obj = weak()
        if run_ref.get("run_id") != run_id or epoch != generation:
            missing("run_or_arena_mismatch")
        elif obj is None:
            missing("weak_reference_expired")
        elif solver.resolve_inspector_event_ref(run_ref) is not obj:
            missing("native_reference_not_current")
        else:
            wanted.setdefault(id(obj), []).append((run_ref, weak))
        del obj
    # The canonical keys are already strongly owned by this capture. No new
    # strong native references escape it; id cannot be reused within this scan.
    for (_kind, obj), local_id in projection.ids.items():
        if not wanted:
            break
        if not projection.available():
            coverage["registry"] = "partial"
            break
        candidates = wanted.pop(id(obj), ())
        for run_ref, weak in candidates:
            if weak() is not obj:
                missing("object_identity_mismatch")
            elif projection.row(result["rows"], {"run_ref": run_ref, "local_id": local_id}):
                coverage["mapped"] += 1
            else:
                coverage["registry"] = "partial"
                missing("mapping_row_budget")
    for candidates in wanted.values():
        for _ in candidates:
            missing("projection_scan_incomplete" if coverage["registry"] == "partial"
                    else "not_captured_as_same_native_object")
    coverage["unexamined"] = coverage["registered_refs"] - coverage["examined"]
    return result


def capture(solver, request=None, run_metadata=None):
    """Capture a completed single-threaded solver-control boundary.

    Callers must own solver control while capturing; the engine has no parallel
    work protocol. Boundary and logical-clock checks also reject reentrant or
    concurrent advances. Arbitrary external writes to private state are outside
    this control contract. Stable invariant violations are intentionally valid.
    """
    request = request or CaptureRequest()
    if len(request.views) > 64 or any(type(view) is not str or len(view) > 128 for view in request.views):
        raise InspectorError("unsupported capture view request")
    if not getattr(solver, "_inspector_enabled", False):
        raise InspectorError("unsupported: solver boundary observation was not enabled before solving")
    if getattr(solver, "_inspector_tainted", False):
        raise InspectorError("unsafe_boundary: an earlier aborted work item tainted this run")
    if request.scope != "all_current_state":
        raise InspectorError("unsupported capture scope")
    stage = getattr(solver, "_inspector_boundary", None)
    if getattr(solver, "_inspector_step_depth", 0) or stage == "running":
        raise InspectorError("busy: solver work item is active")
    if stage not in ("between_work_items", "stopped"):
        raise InspectorError("unsafe_boundary: no completed semantic cut")
    generation = getattr(solver, "_inspector_table_generation", None)
    if generation is None or generation != pts_storage._object_table_generation:
        raise InspectorError("unsafe_boundary: points-to table belongs to another run")
    started = time.monotonic()
    clock = solver._iteration
    metadata = _metadata_tree(run_metadata, request, started + request.deadline_seconds)
    projection = _Projection(request)
    projection.deadline = started + request.deadline_seconds
    views = {}
    state = solver.state
    table = pts_storage._global_table

    # Raw fact owners already distinguish node storage from its qualified Ctx
    # mirror. PFG/variable registries also enumerate owners with empty facts.
    for name in request.views:
        if name == "kwargs_model":
            # The model view depends on the completed raw-view scans, including
            # when a caller lists it before ctx_points in the request.
            continue
        if name not in _VIEWS:
            views[name] = {"version": 1, "fidelity": "opaque", "dimensions": [],
                           "operations": [], "coverage": {"registry": "unsupported", "owners": {}},
                           "rows": [], "diagnostics": ["unsupported view"]}
            continue
        view = views[name] = _descriptor(name)
        complete = True
        if name in ("node_points", "ctx_points"):
            owner_type = PointerFlowNode if name == "node_points" else Ctx
            # Never create a NormalNode, Ctx, field or variable to complete a view.
            def owners():
                for owner in state._env:
                    if isinstance(owner, owner_type):
                        yield owner
                if name == "node_points":
                    yield from state._pointer_flow_graph.nodes
                else:
                    for registers in state._heap.heap.values():
                        yield from registers.values()

            seen = set()
            for owner in owners():
                if owner in seen:
                    continue
                if not projection.available():
                    complete = False
                    break
                seen.add(owner)
                owner_id = projection.entity(owner)
                if owner_id is None:
                    complete = False
                    break
                coverage = {"fields": {"facts": "complete"}}
                view["coverage"]["owners"][str(owner_id)] = coverage
                points = state._env.get(owner)
                if points is None:
                    continue
                owner_complete = True
                for lane, field in _LANES:
                    mask = getattr(points, field)
                    if type(mask) is not int or mask < 0:
                        projection.omit(name, "invalid_engine_bitmask", owner_id)
                        owner_complete = False
                        break
                    while mask:
                        if not projection.available():
                            owner_complete = False
                            break
                        low = mask & -mask
                        index = low.bit_length() - 1
                        mask ^= low
                        if table is None or index >= len(table._id_to_obj):
                            projection.omit(name, "missing_in_engine_object_table", owner_id)
                            owner_complete = False
                            continue
                        obj_id = projection.entity(table._id_to_obj[index])
                        if obj_id is None or not projection.row(view["rows"], {
                                "owner": owner_id, "object": obj_id, "lane": lane}):
                            owner_complete = False
                            break
                    if not owner_complete:
                        break
                if not owner_complete:
                    coverage["fields"]["facts"] = "partial"
                    complete = False
                    if not projection.available() or projection.rows_used >= request.max_rows:
                        break
        elif name == "pfg_edges":
            for edge in state._pointer_flow_graph.edges:
                if not projection.available():
                    complete = False
                    break
                source = projection.entity(edge.source)
                target = projection.entity(edge.target)
                if source is None or target is None or not projection.row(view["rows"], {
                    "source": source, "target": target, "edge_kind": edge.kind.value,
                    "identity_copy": edge.kind == PointerFlowKind.NORMAL and
                        isinstance(edge.source, NormalNode) and isinstance(edge.target, NormalNode)}):
                    complete = False
                    break
        else:
            view["call_kind_encoding"] = "CallKind.name.lower()"
            for edge in state._call_graph.edges:
                if not projection.available():
                    complete = False
                    break
                callsite = projection.entity(edge.callsite)
                callee = projection.entity(edge.callee)
                if callsite is None or callee is None or not projection.row(view["rows"], {
                    "callsite": callsite, "callee": callee, "call_kind": edge.kind.name.lower()}):
                    complete = False
                    break
                view["coverage"]["owners"][str(callsite)] = {"fields": {"edges": "complete"}}
            if not complete:
                # Set enumeration has no per-owner frontier: an unvisited edge
                # might share any observed callsite, so no local negative yet.
                for owner in view["coverage"]["owners"].values():
                    owner["fields"]["edges"] = "partial"
        if not complete:
            view["coverage"]["registry"] = "partial"
            projection.omit(name, projection.stopped or "capture_budget_or_missing_reference")
        view["coverage"]["enumerated_owners"] = len(view["coverage"]["owners"])
        view["coverage"]["enumerated_rows"] = len(view["rows"])

    if "kwargs_model" in request.views:
        export_kwargs_model(solver, projection, views)
    projection.finish_entities()
    run_id = metadata.setdefault("run_id", uuid.uuid4().hex)
    if type(run_id) is not str or not run_id or len(run_id) > 128:
        raise InspectorError("run_id must be a bounded nonempty string")
    trace_mapping = _trace_reference_mapping(solver, projection, run_id, generation)
    metadata.setdefault("observer_mode", "capture")
    metadata["stop_reason"] = ("completed" if stage == "stopped" and solver._stats.get("converged") else
                               "iteration_limit" if stage == "stopped" else "paused_between_work_items")
    # Only audited scalar stats and the four finite queue lengths cross the cut.
    stats = {}
    for key, value in solver._stats.items():
        if type(key) is not str or _text(key) != key:
            projection.omit("stats", "unsupported_stat_name")
            continue
        if type(value) in (bool, int, float, str):
            _described_scalar(stats, key, value)
    stats["pending_work_by_kind"] = solver._pending_work_by_kind()
    stats["capture_projection_seconds"] = time.monotonic() - started
    stats["capture_timing_scope"] = "projection_before_sealing; full capture measured by runner"
    stats["capture_accounted_bytes"] = projection.bytes_used
    stats["capture_entities"] = len(projection.entities)
    stats["capture_rows"] = projection.rows_used
    result = {"schema": {"major": 1, "minor": 0}, "run": metadata, "snapshot": {
        "run_id": run_id, "snapshot_id": uuid.uuid4().hex,
        "boundary": {"coherent": True, "stage": stage,
                     "control_contract": "single_threaded_owned_solver_boundary", "iteration": clock},
        "scope": request.scope, "entities": projection.entities, "views": views,
        "stats": stats, "omissions": projection.omissions,
        "capture": {"status": "sealed", "requested_scope": request.scope,
                    "requested_views": list(request.views),
                    "requested_fields": {name: list(views[name].get("owner_fields", [])) for name in request.views},
                    "limits": {"entities": request.max_entities, "rows_total_across_views": request.max_rows,
                               "bytes": request.max_bytes, "deadline_seconds": request.deadline_seconds,
                               "caller_metadata_bytes": min(65536, request.max_bytes // 4),
                               "caller_metadata_nodes": 4096, "caller_metadata_depth": 32},
                    "relation_coverage": "exact_for_named_relations" if all(
                        view["coverage"]["registry"] == "complete" for view in views.values()) else "partial",
                    "ownership": "all_descriptions_and_rows_encoded_before_leaving_boundary"}}}
    if trace_mapping is not None:
        result["snapshot"]["trace_reference_mapping"] = trace_mapping
    if (solver._iteration != clock or solver._inspector_step_depth or solver._inspector_boundary != stage
            or pts_storage._object_table_generation != generation):
        raise InspectorError("unsafe_boundary: solver changed during capture")
    sealed = SealedSnapshot.seal(result)
    if len(sealed.encoded) > request.max_bytes:
        raise InspectorError("capture byte budget exhausted before snapshot could be sealed")
    return sealed
