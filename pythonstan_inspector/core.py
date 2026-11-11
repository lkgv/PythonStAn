"""Bounded pure offline diagnostics. No engine imports or model execution.

The sealed snapshot is authoritative for current state. Trace is observed
history only. Named v1 relations have fixed column semantics; extra producer
claims cannot install new interpreters or extend supported dimensions.
"""
import base64
from collections import deque
import hashlib
import json
import time
from .contract import InspectorError, QueryLimits, QueryRequest, REFERENCE_STATES, canonical_bytes, digest
from .identity import (MODEL as IDENTITY_MODEL, OBJECT_FIELDS, CONTEXT_FIELDS,
                       SOURCE_CLASSES, recipe as identity_recipe)

_POINTS = frozenset(("node_points", "ctx_points"))
_COLUMNS = {"node_points": frozenset(("owner", "object", "lane")),
            "ctx_points": frozenset(("owner", "object", "lane")),
            "call_edges": frozenset(("callsite", "callee", "call_kind")),
            "pfg_edges": frozenset(("source", "target", "edge_kind"))}
_COMPARE = ("implementation_sha256", "inputs", "configuration", "entry_points",
            "dependency_policy", "models", "cache")
_MODEL_FILTERS = frozenset(("invocation", "caller_scope", "callsite", "callee_scope"))
_MODEL_DIMENSIONS = frozenset(("invocation", "caller_scope", "callsite", "callee_scope", "inputs",
                              "signature", "collector", "channels", "consumers"))
_NATIVE_DESCRIPTOR_KINDS = (frozenset(OBJECT_FIELDS) | frozenset(CONTEXT_FIELDS) | SOURCE_CLASSES
    | {"Ctx", "Scope", "CallSite", "AllocSite", "KeywordPartition", "NormalNode",
       "SummaryContext", "ProtocolKeyContext"})


def _request_shape(request):
    if not isinstance(request, QueryRequest) or type(request.args) is not dict:
        raise InspectorError("query requires QueryRequest with dictionary args")
    if type(request.kind) is not str or type(request.claim) is not str:
        raise InspectorError("query kind and requested claim must be strings")
    if request.cursor is not None and type(request.cursor) is not str:
        raise InspectorError("query cursor must be a string or None")
    if not isinstance(request.limits, QueryLimits):
        raise InspectorError("query limits must be QueryLimits")
    if any(type(key) is not str for key in request.args):
        raise InspectorError("query parameter names must be strings")


def validate_parameters(request, *, checker_parameters=None):
    """One closed parameter contract for Python, CLI, and named checkers.

    Return an unsupported diagnostic for an unimplemented selection. Malformed
    primitive types raise InspectorError. The checker supplies its named
    parameter schema; no checker implementation is imported into this module.
    Cursor binding precedes this function in paginated dispatch.
    """
    _request_shape(request)
    args, kind = request.args, request.kind
    if kind != "check" and request.claim:
        return "requested_claim_not_implemented_no_weaker_substitution"
    if kind == "check":
        if checker_parameters is None:
            return "unknown_checker"
        allowed = checker_parameters
        reason = "unsupported_checker_parameters"
        types = (checker_parameters if type(checker_parameters) is dict else
                 {key: str for key in checker_parameters})
    elif kind in ("summary", "profile"):
        allowed, types = (), {}
        reason = "unsupported_summary_filter_or_cursor" if kind == "summary" else "unsupported_profile_filter"
    elif kind == "show":
        if "view" in args:
            if type(args["view"]) is not str:
                raise InspectorError("view must be a string")
            columns = _MODEL_FILTERS if args["view"] == "kwargs_model" else _COLUMNS.get(args["view"])
            if columns is None:
                return "unsupported_view_semantics_or_operation"
            allowed = columns | {"view", "contains"}
            types = {key: int if key in ("owner", "object", "source", "target", "callsite", "callee",
                                         "invocation", "caller_scope", "callee_scope") else str
                     for key in columns}
            types.update(view=str, contains=bool)
            reason = "requested_dimensions_not_preserved"
        else:
            allowed = {"id", "kind", "label", "file", "line", "column", "scope", "qualname"}
            types = {key: int if key in ("id", "line", "column") else str for key in allowed}
            reason = "unsupported_entity_selector"
    elif kind == "events":
        allowed = {"kind", "work_kind", "stage", "sequence_min", "sequence_max", "file", "line", "column", "model"}
        types = {key: int if key.startswith("sequence_") or key in ("line", "column") else str for key in allowed}
        reason = "trace_entity_mapping_unavailable_or_unsupported_filter"
    elif kind == "explain":
        if "model" in args:
            if type(args["model"]) is not str:
                raise InspectorError("model must be a string")
            if args["model"] != "keyword_collector":
                return "unsupported_explanation_model"
            allowed = {"model", "source_text", "invocation"}
            types = {"model": str, "source_text": str, "invocation": int}
            reason = "unsupported_keyword_collector_explain_parameters"
            if "source_text" not in args:
                return "source_text_required_for_keyword_collector_explanation"
        else:
            allowed = {"owner", "object", "lane", "candidate_source"}
            types = {key: str if key == "lane" else int for key in allowed}
            reason = "explain_requires_owner_and_object_in_node_points"
            if not {"owner", "object"}.issubset(args):
                return reason
    elif kind == "diff":
        allowed, types = {"view", "intended_changes"}, {"view": str, "intended_changes": list}
        reason = "unsupported_diff_parameters"
    else:
        return "query_not_implemented"
    if set(args) - set(allowed):
        return reason
    for name, value in args.items():
        expected = types[name]
        if type(value) is not expected:
            if kind == "check" and name == "source_text":
                return "source_text_required_for_named_fixture_spec"
            if kind == "check" and name == "checker":
                raise InspectorError("checker name must be a string")
            raise InspectorError(name + " has an unsupported parameter type")
        if expected is int and not 0 <= value <= 2**63 - 1:
            raise InspectorError(name + " must be a bounded nonnegative integer")
    if kind == "events" and args.get("sequence_min", 0) > args.get("sequence_max", 2**63 - 1):
        raise InspectorError("sequence_min must not exceed sequence_max")
    if kind == "events" and "line" in args and args["line"] == 0:
        raise InspectorError("line must be a positive integer")
    if kind == "events" and args.get("model", "keyword_collector") != "keyword_collector":
        return "unsupported_trace_model"
    if kind == "diff" and "intended_changes" in args:
        changes = args["intended_changes"]
        if (any(type(value) is not str or value not in _COMPARE for value in changes)
                or len(set(changes)) != len(changes)):
            raise InspectorError("intended_changes must name distinct supported comparison assumptions")
    return None


class Budget:
    def __init__(self, limits):
        self.limits = limits
        self.end = time.monotonic() + limits.deadline_seconds
        self.visited = 0
        self.bytes = 0
        self.reason = None

    def visit(self):
        self.visited += 1
        if self.visited > self.limits.max_visited or time.monotonic() > self.end:
            self.reason = "work_or_deadline_limit"
            return False
        return True

    def output(self, row):
        size = len(canonical_bytes(row))
        if self.bytes + size > self.limits.max_bytes:
            self.reason = "byte_limit"
            return False
        self.bytes += size
        return True

    def copy(self, value, depth=0):
        """Bound artifact metadata too, rather than only relation rows."""
        if not self.visit() or depth > self.limits.max_depth:
            self.reason = self.reason or "depth_limit"
            return {"omitted": self.reason}
        if type(value) is dict:
            result = {}
            for key, child in value.items():
                if self.reason:
                    result["_inspector_omitted"] = self.reason
                    break
                result[key] = self.copy(child, depth + 1)
            return result
        if type(value) is list:
            result = []
            for child in value:
                if self.reason:
                    result.append({"omitted": self.reason})
                    break
                result.append(self.copy(child, depth + 1))
            return result
        if not self.output(value):
            return {"omitted": "byte_limit"}
        return value


def envelope(data, request):
    snap = data["snapshot"]
    return {"query": request.kind, "requested_claim": request.claim,
            "parameters": dict(request.args),
            "snapshot_ref": {"run_id": snap["run_id"], "snapshot_id": snap["snapshot_id"]},
            "scope": snap.get("scope"), "answer": "unknown", "status": "ok",
            "selection_scope": {"kind": request.kind},
            "evidence": [], "diagnostics": [], "coverage": {},
            "trust_dependencies": ["captured_engine_state", "adapter_projection_v1"],
            "concrete_reachability_proved": False}


def owner_complete(view, owner, field="facts"):
    return view.get("coverage", {}).get("owners", {}).get(str(owner), {}).get("fields", {}).get(field) == "complete"


def common_cut_admissible(snapshot):
    """Shared necessary condition for snapshot membership and joined claims.

    Raw captured rows do not need a common cut. View semantics, dimensions,
    fidelity and claim-specific coverage remain separate prerequisites; in
    particular, an exact positive witness does not require complete capture.
    """
    return snapshot.get("boundary", {}).get("coherent") is True


def dimensions_preserved(view, args):
    return all(key in view.get("dimensions", []) for key in args)


def _supported(name, view, operation="enumerate"):
    if name == "kwargs_model":
        return (type(view) is dict and type(view.get("version")) is int and view.get("version") == 1
                and view.get("semantic_version") == "registered_keyword_collector_v1"
                and view.get("fidelity") == "exact" and operation == "enumerate"
                and type(view.get("operations")) is list and "enumerate" in view["operations"]
                and type(view.get("dimensions")) is list
                and all(type(value) is str for value in view["dimensions"])
                and _MODEL_DIMENSIONS.issubset(view["dimensions"])
                and type(view.get("rows")) is list and type(view.get("coverage")) is dict
                and type(view["coverage"].get("owners")) is dict)
    return (name in _COLUMNS and type(view) is dict and type(view.get("version")) is int and view.get("version") == 1
            and view.get("semantic_version", "raw_native_storage_v1") == "raw_native_storage_v1"
            and type(view.get("operations")) is list and type(view.get("dimensions")) is list
            and operation in view.get("operations", [])
            and _COLUMNS[name].issubset(view.get("dimensions", [])))


def _coverage(view, owner=None):
    coverage = view.get("coverage", {})
    if type(coverage) is not dict or type(coverage.get("owners", {})) is not dict:
        return {"registry": "unsupported", "view_version": view.get("version")}
    result = {"registry": coverage.get("registry", "not_captured"),
              "fidelity": view.get("fidelity"), "view_version": view.get("version"),
              "owner_count_captured": len(coverage.get("owners", {}))}
    if owner is not None:
        result["owner"] = owner
        result["fields"] = coverage.get("owners", {}).get(str(owner), {}).get("fields", {})
    return result


def _complete(view, budget, field="facts"):
    cov = view.get("coverage", {})
    if cov.get("registry") != "complete":
        return False
    for owner in cov.get("owners", {}):
        if not budget.visit() or not owner_complete(view, owner, field):
            return False
    return True


def _binding(bundle, data, request):
    # Hash already-owned encoded bytes, never canonicalize the whole live tree.
    # Decode/hash admission is bounded by ReadLimits and charged by byte blocks.
    return digest({"snapshot": bundle.snapshot.sha256, "kind": request.kind,
                   "trace": hashlib.sha256(bundle._trace).hexdigest() if request.kind == "events" else None,
                   "args": dict(request.args), "claim": request.claim,
                   "views": {k: v.get("version") for k, v in data["snapshot"]["views"].items()}})


def _offset(bundle, data, request, count):
    if request.cursor is None:
        return 0
    try:
        if len(request.cursor) > 4096:
            raise ValueError()
        value = json.loads(base64.b64decode(request.cursor.encode("ascii"), altchars=b"-_", validate=True))
        offset = value["offset"]
        if (value["binding"] != _binding(bundle, data, request) or type(offset) is not int
                or not 0 <= offset <= count):
            raise ValueError()
        return offset
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise InspectorError("cursor does not belong to this immutable query") from exc


def _cursor(bundle, data, request, offset):
    return base64.urlsafe_b64encode(canonical_bytes({"binding": _binding(bundle, data, request),
                                                   "offset": offset})).decode("ascii")


def _page(bundle, data, request, rows, predicate, budget):
    result = []
    index = _offset(bundle, data, request, len(rows))
    while index < len(rows):
        if not budget.visit():
            return result, False, _cursor(bundle, data, request, index)
        row = rows[index]
        if predicate(row):
            if len(result) >= request.limits.max_rows or not budget.output(row):
                return result, False, _cursor(bundle, data, request, index)
            result.append(row)
        index += 1
    return result, True, None


def _unsupported(out, reason):
    out.update(status="unsupported", answer="unknown", diagnostics=[reason])
    return out


def _finish(data, request, out, budget):
    if time.monotonic() > budget.end:
        budget.reason = "work_or_deadline_limit"
    if budget.reason:
        out["diagnostics"].append(budget.reason)
        out["query_complete"] = False
        # A positive exact witness may survive a bounded incomplete scan.
        if out.get("answer") not in ("present_at_snapshot", "covered", "failed"):
            out["answer"] = "unknown"
        if out.get("check_status") == "passed":
            out["check_status"] = "indeterminate"
            if type(out.get("completion")) is dict:
                out["completion"]["check"] = "indeterminate"
    out["work"] = {"visited": budget.visited, "admission": "bounded_read_bytes_charged_in_4096_byte_blocks"}
    if len(canonical_bytes(out)) <= request.limits.max_bytes:
        return out
    if "check_status" in out:
        raise InspectorError("requested byte limit cannot fit the complete checker response")
    # No partial response can retain a stronger checker/absence conclusion.
    small = envelope(data, request)
    small.update(status="truncated", answer="unknown", query_complete=False,
                 diagnostics=["result_byte_limit"], work={"visited": budget.visited})
    if "check_status" in out:
        small["check_status"] = "indeterminate"
    if len(canonical_bytes(small)) > request.limits.max_bytes:
        raise InspectorError("requested byte limit cannot fit the query envelope")
    return small


def _admit(bundle, budget, trace=False):
    size = len(bundle.snapshot.encoded) + (len(bundle._trace) if trace else 0)
    for _ in range(size // 4096 + 1):
        if not budget.visit():
            return False
    return True


def _summary_owners(data, view, budget):
    """Unknown native object compression blocks unsupported negative claims."""
    exact_objects = set()
    for entity in data["snapshot"]["entities"]:
        if not budget.visit():
            return None
        if (entity.get("kind") == "object"
                and entity.get("attributes", {}).get("native_summary") is False):
            exact_objects.add(entity["id"])
    owners = set()
    for row in view.get("rows", []):
        if not budget.visit():
            return None
        if row.get("object") not in exact_objects:
            owners.add(row["owner"])
    return owners


def _show(bundle, data, request, out, budget):
    args = dict(request.args)
    snap = data["snapshot"]
    name = args.pop("view", None)
    if name is None:
        allowed = {"id", "kind", "label", "file", "line", "column", "scope", "qualname"}
        if set(args) - allowed:
            return _unsupported(out, "unsupported_entity_selector")
        for key, value in args.items():
            if key in ("id", "line", "column"):
                if type(value) is not int or not 0 <= value <= 2**63 - 1:
                    raise InspectorError("numeric entity selector must be a bounded integer")
            elif type(value) is not str:
                raise InspectorError("text entity selector must be a string")
        def selected(row):
            return all((row.get(k) if k in ("id", "kind", "label") else row.get("origin", {}).get(k)) == v
                       for k, v in args.items())
        rows, complete, cursor = _page(bundle, data, request, snap["entities"], selected, budget)
        out.update(answer="entities", evidence=rows, query_complete=complete, cursor=cursor,
                   selection_scope={"kind": "entities", "filters": args})
        return out
    if type(name) is not str:
        raise InspectorError("view must be a string")
    contains = args.pop("contains", False)
    if type(contains) is not bool:
        raise InspectorError("contains must be boolean")
    view = snap["views"].get(name)
    if not _supported(name, view, "contains" if contains else "enumerate"):
        return _unsupported(out, "unsupported_view_semantics_or_operation")
    if name == "kwargs_model":
        from .collector_checker import model_shape_supported
        if not model_shape_supported(view, budget):
            return out if budget.reason else _unsupported(out, "kwargs_model_row_encoding_unsupported")
        rows, complete, cursor = _page(bundle, data, request, view["rows"],
            lambda row: all(type(row.get(key)) is int and row[key] == value for key, value in args.items()), budget)
        out.update(answer="captured_rows", evidence=rows, query_complete=complete, cursor=cursor,
                   coverage=_coverage(view), selection_scope={"kind": "relation", "view": name,
                       "operation": "enumerate", "filters": args},
                   interpretation="raw_registered_model_links_not_independent_semantic_obligations")
        return out
    if set(args) - _COLUMNS[name] or not dimensions_preserved(view, args):
        return _unsupported(out, "requested_dimensions_not_preserved")
    for key, value in args.items():
        if key in ("owner", "object", "source", "target", "callsite", "callee"):
            if type(value) is not int or not 0 <= value <= 2**63 - 1:
                raise InspectorError("relation reference must be a bounded integer")
        elif type(value) is not str:
            raise InspectorError("relation tag must be a string")
    owner_key = "callsite" if name == "call_edges" else "owner"
    field = "edges" if name == "call_edges" else "facts"
    out["coverage"] = _coverage(view, args.get(owner_key))
    out["selection_scope"] = {"kind": "relation", "view": name,
        "operation": "contains" if contains else "enumerate", "filters": args}
    rows, complete, cursor = _page(bundle, data, request, view.get("rows", []),
        lambda r: all(r.get(k) == v for k, v in args.items()), budget)
    out.update(evidence=rows, query_complete=complete, cursor=cursor)
    if not contains:
        out["answer"] = "captured_rows"
        return out
    if not common_cut_admissible(snap):
        # Preserve the matching forensic rows without interpreting them as
        # membership or containment at one common analysis state.
        out["diagnostics"].append("coherent_snapshot_required")
        return out
    # Partial witnesses establish only existence, never uniqueness or totals.
    if view.get("fidelity") == "exact" and rows:
        out["answer"] = "present_at_snapshot"
    elif view.get("fidelity") == "native_summary" and view.get("containment") == "finite_top_v1":
        # Fixed pure DTO operator: explicitly tagged top records cover requested
        # objects. Display summaries and arbitrary domain methods are not used.
        for row in view.get("rows", []):
            if not budget.visit():
                break
            if row.get("summary") == "top" and all(k == "object" or row.get(k) == v for k, v in args.items()):
                out.update(answer="covered", evidence=[row])
                break
    elif (view.get("fidelity") == "exact" and complete and not request.cursor
          and (owner_complete(view, args[owner_key], field) if owner_key in args else _complete(view, budget, field))):
        summary_owners = _summary_owners(data, view, budget) if name in _POINTS else set()
        if summary_owners is not None and (args[owner_key] not in summary_owners if owner_key in args else not summary_owners):
            out["answer"] = "absent_at_snapshot"
    return out


def _events(bundle, data, request, out, budget):
    args = dict(request.args)
    source_selectors = {"file", "line", "column", "model"}
    allowed = {"kind", "work_kind", "stage", "sequence_min", "sequence_max"} | source_selectors
    if set(args) - allowed:
        return _unsupported(out, "trace_entity_mapping_unavailable_or_unsupported_filter")
    for key in ("sequence_min", "sequence_max"):
        if key in args and (type(args[key]) is not int or args[key] < 0):
            raise InspectorError("event sequence bounds must be nonnegative integers")
    if any(type(args[k]) is not str for k in args if k not in ("sequence_min", "sequence_max", "line", "column")):
        raise InspectorError("event tag filters must be strings")
    descriptor_refs = {}

    def native_ref(value):
        if (type(value) is dict and set(value) == {"run_id", "epoch", "ref"}
                and type(value.get("run_id")) is str and value["run_id"]
                and type(value.get("epoch")) is int and type(value.get("ref")) is int
                and 0 <= value["epoch"] <= 2**63 - 1 and 1 <= value["ref"] <= 2**63 - 1):
            return value["run_id"], value["epoch"], value["ref"]
        return None

    def selected(row):
        if row.get("kind") == "native_descriptor" and type(row.get("native_descriptor")) is dict:
            descriptor = row["native_descriptor"]
            ref = native_ref(descriptor.get("ref"))
            descriptor_kind = descriptor.get("kind")
            source = descriptor.get("source")
            source_valid = ("source" not in descriptor or type(source) is dict
                and set(source) == {"file", "line", "column", "call_index"}
                and type(source["file"]) is str and type(source["line"]) is int and source["line"] > 0
                and all(type(source[name]) is int and source[name] >= 0 for name in ("column", "call_index")))
            if (ref is not None and set(descriptor).issubset({"ref", "kind", "source"})
                    and type(descriptor_kind) is str and descriptor_kind in _NATIVE_DESCRIPTOR_KINDS
                    and source_valid):
                if ref in descriptor_refs and descriptor_refs[ref] != descriptor_kind:
                    descriptor_refs[ref] = None
                else:
                    descriptor_refs[ref] = descriptor_kind
            elif ref is not None:
                descriptor_refs[ref] = None
        if not all(row.get(k) == v for k, v in args.items()
                   if k not in source_selectors | {"sequence_min", "sequence_max"}):
            return False
        if source_selectors.intersection(args):
            event = row.get("native_event")
            if row.get("kind") != "targeted_operation" or type(event) is not dict:
                return False
            source = event.get("source")
            for key in source_selectors.intersection(args):
                actual = event.get("model") if key == "model" else source.get(key) if type(source) is dict else None
                if type(actual) is not type(args[key]) or actual != args[key]:
                    return False
        if "sequence_min" not in args and "sequence_max" not in args:
            return True
        sequence = row.get("sequence")
        return (type(sequence) is int and 0 <= sequence <= 2**63 - 1
                and sequence >= args.get("sequence_min", 0)
                and sequence <= args.get("sequence_max", 2**63 - 1))
    events = bundle.trace
    rows, complete, cursor = _page(bundle, data, request, events, selected, budget)
    decoded = {"scope": "selected_native_event_refs_and_descriptors_in_scanned_page_input_range",
               "reference_occurrences": 0, "descriptor_missing": 0, "malformed_references": 0,
               "snapshot_mapping_uninterpreted": 0, "snapshot_mapping_missing": 0,
               "snapshot_mapped": 0, "snapshot_mapping_conflicts": 0,
               "mapping_present_but_descriptor_unavailable": 0,
               "descriptor_mapping_kind_mismatch": 0, "complete": True}
    selected_refs = []
    for row in rows:
        if not budget.visit():
            decoded["complete"] = False
            break
        event = row.get("native_event")
        if row.get("kind") != "targeted_operation" or type(event) is not dict:
            continue
        refs = event.get("refs", {})
        if type(refs) is not dict:
            decoded["malformed_references"] += 1
            continue
        for value in refs.values():
            if not budget.visit():
                decoded["complete"] = False
                break
            decoded["reference_occurrences"] += 1
            ref = native_ref(value)
            if ref is not None and ref[:2] != (event.get("run_id"), event.get("epoch")):
                ref = None
            if ref is None:
                decoded["malformed_references"] += 1
            elif descriptor_refs.get(ref) is None:
                decoded["descriptor_missing"] += 1
            if ref is not None:
                selected_refs.append((ref, value))
    mapping = data["snapshot"].get("trace_reference_mapping")
    mapping_known = (type(mapping) is dict and type(mapping.get("version")) is int and mapping.get("version") == 1
        and mapping.get("semantic_version") == "native_weak_identity_to_snapshot_v1"
        and mapping.get("scope") == "already_projected_exact_objects"
        and type(mapping.get("rows")) is list and type(mapping.get("coverage")) is dict
        and mapping["coverage"].get("registry") in ("complete", "partial"))
    qualified, mapped_refs = {}, []
    if mapping_known and selected_refs:
        ids = {}
        for entity in data["snapshot"]["entities"]:
            if not budget.visit():
                mapping_known = False
                break
            ids[entity["id"]] = entity.get("attributes", {}).get("engine_class")
        for entry in mapping["rows"] if mapping_known else ():
            if not budget.visit():
                mapping_known = False
                break
            if type(entry) is not dict:
                mapping_known = False
                break
            ref, local_id = native_ref(entry.get("run_ref")), entry.get("local_id")
            if (ref is None or ref[0] != data["snapshot"]["run_id"]
                    or type(local_id) is not int or local_id not in ids):
                mapping_known = False
                break
            if ref in qualified and qualified[ref] != local_id:
                qualified[ref] = None
            else:
                qualified[ref] = local_id
    for ref, value in selected_refs:
        if not budget.visit():
            decoded["complete"] = False
            break
        if not mapping_known:
            decoded["snapshot_mapping_uninterpreted"] += 1
        elif ref not in qualified:
            decoded["snapshot_mapping_missing"] += 1
        elif qualified[ref] is None:
            decoded["snapshot_mapping_conflicts"] += 1
        elif descriptor_refs.get(ref) is None:
            decoded["mapping_present_but_descriptor_unavailable"] += 1
        elif descriptor_refs[ref] != ids[qualified[ref]]:
            decoded["descriptor_mapping_kind_mismatch"] += 1
        else:
            decoded["snapshot_mapped"] += 1
            if len(mapped_refs) < request.limits.max_rows:
                mapped_refs.append({"run_ref": value, "entity_ref": {
                    "snapshot": out["snapshot_ref"], "local_id": qualified[ref]}})
    decoded["mapped_reference_rows_complete"] = decoded["snapshot_mapped"] == len(mapped_refs)
    out.update(answer="observed_events", evidence=rows, query_complete=complete, cursor=cursor,
               selection_scope={"kind": "observed_trace", "filters": args},
               mapped_references=mapped_refs,
               coverage={"reader": bundle.trace_health, "producer": data["run"].get("trace_health", {}),
                         "entity_mapping": "explicit_snapshot_map_only" if mapping_known else "unsupported",
                         "native_reference_decode": decoded})
    out["diagnostics"].append("absence_of_record_does_not_prove_absence_of_execution")
    if "sequence_min" in args or "sequence_max" in args:
        out["diagnostics"].append("unknown_or_missing_sequence_records_cannot_satisfy_sequence_filter")
    return out


def _explain(bundle, data, request, out, budget):
    args = dict(request.args)
    if "model" in args:
        from .collector_checker import explain_keyword_collector
        return explain_keyword_collector(data, request, out, budget)
    if set(args) - {"owner", "object", "lane", "candidate_source"} or not all(k in args for k in ("owner", "object")):
        return _unsupported(out, "explain_requires_owner_and_object_in_node_points")
    if any(type(args[k]) is not int or not 0 <= args[k] <= 2**63 - 1 for k in args if k != "lane") or ("lane" in args and type(args["lane"]) is not str):
        raise InspectorError("explain requires bounded local references and a string lane")
    points, edges = (data["snapshot"]["views"].get(name) for name in ("node_points", "pfg_edges"))
    if not _supported("node_points", points) or not _supported("pfg_edges", edges) or points.get("fidelity") != "exact" or edges.get("fidelity") != "exact":
        return _unsupported(out, "reconstructed_copy_support_views_unavailable")
    if not common_cut_admissible(data["snapshot"]):
        return _unsupported(out, "coherent_snapshot_required")
    lane = args.get("lane", "objects")
    facts = set()
    facts_scanned = True
    for row in points["rows"]:
        if not budget.visit():
            facts_scanned = False
            break
        facts.add((row["owner"], row["object"], row["lane"]))
    normals = set()
    for entity in data["snapshot"]["entities"]:
        if not budget.visit():
            break
        attrs = entity.get("attributes", {})
        if entity.get("kind") == "node" and attrs.get("engine_class") == "NormalNode" and attrs.get("node_kind") == "normal":
            normals.add(entity["id"])
    incoming = {}
    for edge in edges["rows"]:
        if not budget.visit():
            break
        if edge.get("edge_kind") == "normal" and edge.get("source") in normals and edge.get("target") in normals:
            incoming.setdefault(edge["target"], []).append(edge)
    target = (args["owner"], args["object"], lane)
    summary_owners = _summary_owners(data, points, budget)
    def represented(fact):
        if fact in facts:
            return True
        if (facts_scanned and owner_complete(points, fact[0]) and summary_owners is not None
                and fact[0] not in summary_owners):
            return False
        return None
    pending = deque([(target[0], 0)])
    seen = set()
    evidence = []
    while pending and len(evidence) < request.limits.max_rows:
        owner, depth = pending.popleft()
        if not budget.visit():
            break
        if owner in seen:
            # Global deduplication observes revisits, not a path back-edge.
            evidence.append({"kind": "revisited_reference", "owner": owner})
            continue
        seen.add(owner)
        fact = (owner, target[1], lane)
        alternatives = []
        for edge in incoming.get(owner, []):
            if not budget.visit() or len(alternatives) >= request.limits.max_rows:
                if len(alternatives) >= request.limits.max_rows:
                    budget.reason = "row_limit"
                break
            if "candidate_source" in args and owner == target[0] and edge["source"] != args["candidate_source"]:
                continue
            premise = (edge["source"], target[1], lane)
            alternatives.append({"evidence_kind": "reconstructed_support", "rule": edge,
                "premises": [{"operator": "AND", "fact": {"owner": premise[0], "object": premise[1], "lane": premise[2]},
                              "represented": represented(premise)}], "consequence_represented": represented(fact)})
            if premise in facts and depth < request.limits.max_depth:
                pending.append((premise[0], depth + 1))
        evidence.append({"kind": "reconstructed_support", "owner": owner,
                         "operator": "OR", "alternatives": alternatives,
                         "fact_represented": represented(fact), "depth": depth})
        if depth >= request.limits.max_depth and incoming.get(owner):
            budget.reason = "depth_limit"
    out.update(answer="reconstructed_support", evidence=evidence,
        selection_scope={"kind": "reconstructed_normal_copy_support", "view": "node_points",
                         "owner": args["owner"], "object": args["object"], "lane": lane,
                         "candidate_source": args.get("candidate_source")},
        query_complete=not pending and budget.reason is None,
        coverage={"points": _coverage(points, args["owner"]), "rules": _coverage(edges)},
        generated_history="unsupported", global_why_not="unsupported")
    out["diagnostics"].append("possible_snapshot_support_is_not_original_producer_history")
    return out


def _profile(bundle, data, request, out, budget):
    if request.args:
        return _unsupported(out, "unsupported_profile_filter")
    counts = {}
    for row in bundle.trace:
        if not budget.visit():
            break
        if row.get("kind") == "work_step":
            key = row.get("work_kind", "unknown")
            if type(key) is not str:
                key = "unknown"
            counts[key] = counts.get(key, 0) + 1
    out.update(answer="recorded_profile", phases=budget.copy(data["run"].get("phases", {})),
               statistics=budget.copy(data["snapshot"].get("stats", {})), observed_work_counts=counts,
               coverage={"trace": bundle.trace_health}, query_complete=budget.reason is None,
               timing_interpretation="phase_wall_measurements; do_not_sum_inclusive_and_exclusive_values",
               downstream_attribution="unsupported")
    return out


def query(bundle, request):
    """One bounded dispatcher shared by Python clients and the JSON CLI."""
    _request_shape(request)
    if request.kind == "check":
        # The named checker shares Budget/envelope but owns this operation's
        # single admission, decode, deadline and final response guard.
        from .checker import check
        return check(bundle, request)
    budget = Budget(request.limits)
    admitted = _admit(bundle, budget, trace=request.kind in ("events", "profile"))
    if not admitted:
        raise InspectorError("query budget admission refused before snapshot decoding")
    data = bundle.data
    out = envelope(data, request)
    if time.monotonic() > budget.end:
        budget.reason = "work_or_deadline_limit"
        return _finish(data, request, out, budget)
    if request.cursor is not None:
        # Reject cross-query reuse even when the new query requests an
        # unsupported selector before it reaches a paginated relation.
        _offset(bundle, data, request, 2**63 - 1)
    invalid = validate_parameters(request)
    if invalid:
        return _finish(data, request, _unsupported(out, invalid), budget)
    if request.kind == "summary":
        if request.args or request.cursor:
            return _finish(data, request, _unsupported(out, "unsupported_summary_filter_or_cursor"), budget)
        views = {}
        for name, view in data["snapshot"]["views"].items():
            if not budget.visit():
                break
            known = _supported(name, view)
            views[name] = (budget.copy({"version": view.get("version"), "fidelity": view.get("fidelity"),
                "dimensions": view.get("dimensions"), "operations": view.get("operations"),
                "coverage": _coverage(view), "captured_rows": len(view.get("rows", [])),
                "known_semantics": True}) if known else
                {"version": view.get("version"), "known_semantics": False,
                 "coverage": {"registry": "unsupported"}, "diagnostic": "optional_unknown_view_retained_opaque"})
        out.update(answer="snapshot_summary", run=budget.copy(data["run"]),
            boundary=budget.copy(data["snapshot"]["boundary"]), views=views,
            statistics=budget.copy(data["snapshot"].get("stats", {})),
            omissions=budget.copy(data["snapshot"].get("omissions", [])), query_complete=budget.reason is None)
    elif request.kind == "show":
        out = _show(bundle, data, request, out, budget)
    elif request.kind == "events":
        out = _events(bundle, data, request, out, budget)
    elif request.kind == "explain":
        if request.cursor:
            out = _unsupported(out, "explain_cursor_not_supported_use_bounded_direct_expansion")
        else:
            out = _explain(bundle, data, request, out, budget)
    elif request.kind == "profile":
        out = (_unsupported(out, "profile_cursor_not_supported") if request.cursor else
               _profile(bundle, data, request, out, budget))
    elif request.kind == "diff":
        out = _unsupported(out, "diff_requires_explicit_other_bundle_use_diff_query")
    else:
        out = _unsupported(out, "query_not_implemented")
    return _finish(data, request, out, budget)


def _alignment_key(entity, registry, budget, path=(), depth=0):
    """Conservative described-origin candidates; ties stay ambiguous.

    These are alignment hypotheses, never persistent entity identities. Unknown
    origins cannot be rescued by local ID or a pretty printed label.
    """
    origin = entity.get("origin", {})
    attrs = entity.get("attributes", {})
    if (not budget.visit() or depth >= budget.limits.max_depth or entity["id"] in path
            or origin.get("description_coverage") in ("partial", "not_captured", "opaque")
            # Legacy v1 producers clipped descriptions without recording the
            # loss. Absence of a marker cannot certify an alignment key.
            or attrs.get("description_coverage") != "complete"
            or any(k.endswith("_coverage") and v in ("opaque", "partial", "not_captured")
                   for k, v in attrs.items())):
        return None
    recipe = identity_recipe(entity)
    if recipe is None:
        return None
    scalar_names, needed = recipe
    links = entity.get("links", {})
    linked = {}
    for name in needed:
        link = links.get(name, {})
        if link.get("status") != "resolved" or link.get("ref") not in registry:
            return None
        key = _alignment_key(registry[link["ref"]], registry, budget,
                             path + (entity["id"],), depth + 1)
        if key is None:
            return None
        linked[name] = key
    return digest({"model": IDENTITY_MODEL, "kind": entity["kind"],
                   "origin": origin, "links": linked,
                   "attributes": {name: attrs[name] for name in scalar_names}})


def _align(left, right, same, budget):
    lmap, rmap = {}, {}
    if same:
        mapping = {}
        for entity in left:
            if not budget.visit():
                break
            mapping[entity["id"]] = entity["id"]
        return mapping, [], [], []
    unresolved_left, unresolved_right = [], []
    registries = []
    for rows, table, unknown in ((left, lmap, unresolved_left), (right, rmap, unresolved_right)):
        registry = {}
        for entity in rows:
            if not budget.visit():
                break
            registry[entity["id"]] = entity
        registries.append(registry)
        for entity in rows:
            if not budget.visit():
                break
            key = _alignment_key(entity, registry, budget)
            if key is None:
                unknown.append(entity["id"])
            else:
                table.setdefault(key, []).append(entity["id"])
    mapping, ambiguous = {}, []
    for key in lmap.keys() | rmap.keys():
        if not budget.visit():
            break
        lvals, rvals = lmap.get(key, []), rmap.get(key, [])
        if len(lvals) == len(rvals) == 1:
            mapping[lvals[0]] = rvals[0]
        elif lvals and rvals:
            ambiguous.append({"left": lvals, "right": rvals, "basis": "nonunique_identity_description"})
        else:
            unresolved_left.extend(lvals)
            unresolved_right.extend(rvals)
    # A unique parent description cannot rescue an ambiguous required child.
    # Restrict only the explicitly audited identity dependencies, then remove
    # parents whose referenced entities are not uniquely aligned. There are no
    # admitted recursive keys, and the query budget bounds these finite passes.
    left_registry, right_registry = registries
    changed = True
    while changed and mapping:
        changed = False
        for left_id, right_id in tuple(mapping.items()):
            if not budget.visit():
                unresolved_left.extend(mapping)
                unresolved_right.extend(mapping.values())
                return {}, unresolved_left, unresolved_right, ambiguous
            lentity, rentity = left_registry[left_id], right_registry[right_id]
            required = identity_recipe(lentity)[1]
            qualified = True
            for name in required:
                if not budget.visit():
                    unresolved_left.extend(mapping)
                    unresolved_right.extend(mapping.values())
                    return {}, unresolved_left, unresolved_right, ambiguous
                lref = lentity["links"][name]["ref"]
                rref = rentity["links"][name]["ref"]
                if mapping.get(lref) != rref:
                    qualified = False
                    break
            if not qualified:
                del mapping[left_id]
                unresolved_left.append(left_id)
                unresolved_right.append(right_id)
                changed = True
    return mapping, unresolved_left, unresolved_right, ambiguous


def _invalid_identity_evidence(run):
    """Content instability cannot be waived as an intended configuration diff."""
    invalid = []
    if run.get("source_validation") == "changed_during_analysis":
        invalid.append("source_changed_during_analysis")
    if run.get("implementation_stable") is False:
        invalid.append("implementation_changed_during_analysis")
    inputs = run.get("inputs", [])
    if type(inputs) is list:
        for item in inputs:
            if type(item) is not dict:
                invalid.append("unrecognized_input_identity_row")
                break
            if type(item) is dict and "sha256_before" in item and "sha256_after" in item and item["sha256_before"] != item["sha256_after"]:
                invalid.append("input_pre_post_digest_mismatch")
                break
    elif type(inputs) is dict:
        # Whole-project identities bind potentially thousands of consumed
        # source rows by content digest, with the exact rows in a companion.
        # Unknown future identity encodings cannot authorize comparisons.
        if inputs.get("identity_algorithm") != "sha256_ascii_canonical_json_rows_v1":
            invalid.append("unsupported_input_identity_catalog")
        else:
            for field in ("inventory_sha256", "consumed_sha256"):
                value = inputs.get(field)
                if (type(value) is not str or len(value) != 64
                        or any(c not in "0123456789abcdef" for c in value)):
                    invalid.append("invalid_input_catalog_digest")
                    break
            inventory, consumed = inputs.get("inventory_count"), inputs.get("consumed_count")
            if (type(inventory) is not int or type(consumed) is not int
                    or not 0 <= consumed <= inventory <= 2**63 - 1):
                invalid.append("invalid_input_catalog_counts")
    else:
        invalid.append("unsupported_input_identity_encoding")
    return invalid


def diff_query(bundle, other, request):
    _request_shape(request)
    if request.kind != "diff":
        raise InspectorError("diff_query requires kind='diff'")
    budget = Budget(request.limits)
    if not _admit(bundle, budget) or not _admit(other, budget):
        raise InspectorError("diff budget admission refused before snapshot decoding")
    data, rhs = bundle.data, other.data
    out = envelope(data, request)
    out["other_snapshot_ref"] = {k: rhs["snapshot"][k] for k in ("run_id", "snapshot_id")}
    args = dict(request.args)
    if request.cursor:
        return _finish(data, request, _unsupported(out, "diff_cursor_not_supported"), budget)
    invalid = validate_parameters(request)
    if invalid:
        return _finish(data, request, _unsupported(out, invalid), budget)
    if set(args) - {"view", "intended_changes"}:
        return _finish(data, request, _unsupported(out, "unsupported_diff_parameters"), budget)
    intended = args.get("intended_changes", [])
    if type(intended) is not list or any(type(x) is not str or x not in _COMPARE for x in intended):
        raise InspectorError("intended_changes must name supported comparison assumptions")
    changed, missing = [], []
    for key in _COMPARE:
        if not budget.visit():
            break
        if key not in data["run"] or key not in rhs["run"]:
            missing.append(key)
        elif data["run"][key] != rhs["run"][key]:
            changed.append(key)
    name = args.get("view", "node_points")
    if type(name) is not str:
        raise InspectorError("diff view must be a string")
    if name not in _COLUMNS:
        return _finish(data, request, _unsupported(out, "unsupported_diff_view"), budget)
    left, right = data["snapshot"]["views"].get(name), rhs["snapshot"]["views"].get(name)
    invalid_identities = {"left": _invalid_identity_evidence(data["run"]),
                          "right": _invalid_identity_evidence(rhs["run"])}
    comparable = (not missing and not (set(changed) - set(intended))
        and not invalid_identities["left"] and not invalid_identities["right"]
        and data["snapshot"]["scope"] == rhs["snapshot"]["scope"]
        and data["snapshot"]["boundary"].get("stage") == rhs["snapshot"]["boundary"].get("stage")
        and data["snapshot"]["boundary"].get("coherent") is True
        and rhs["snapshot"]["boundary"].get("coherent") is True
        and _supported(name, left) and _supported(name, right)
        and left.get("fidelity") == right.get("fidelity") == "exact")
    out["comparability"] = {"comparable": comparable, "changed_assumptions": changed,
        "missing_assumptions": missing, "intended_changes": intended,
        "invalid_identity_evidence": invalid_identities,
        "interpretation": "conditional_structural_comparison_not_precision_or_soundness"}
    if not comparable or budget.reason:
        out.update(answer="incomparable", status="incomparable")
        return _finish(data, request, out, budget)
    mapping, unl, unr, ambiguous = _align(data["snapshot"]["entities"], rhs["snapshot"]["entities"],
        bundle.snapshot.encoded == other.snapshot.encoded, budget)
    alignment = {"matched": [{"left": a, "right": b} for a, b in mapping.items()],
                 "unmatched_left": unl, "unmatched_right": unr, "ambiguous": ambiguous}
    columns = (("owner", "object", "lane") if name in _POINTS else
               ("callsite", "callee", "call_kind") if name == "call_edges" else
               ("source", "target", "edge_kind"))
    ref_columns = columns[:2]
    inverse = {right_id: left_id for left_id, right_id in mapping.items()}
    ambiguous_ids = {side: {value for group in ambiguous for value in group[side]}
                     for side in ("left", "right")}
    compared_owner_sets = {"left": set(), "right": set()}

    def scan_rows(view, translation, side):
        counts = {"captured_rows": len(view["rows"]), "scanned_rows": 0,
                  "aligned_rows": 0, "unmatched_reference_rows": 0,
                  "ambiguous_reference_rows": 0, "compared_rows": 0,
                  "compared_owner_ids": [], "owner_ids_complete": True,
                  "count_scope": "scanned_captured_rows"}
        actual, aligned = set(), []
        for row in view["rows"]:
            if not budget.visit():
                break
            counts["scanned_rows"] += 1
            actual.add(tuple(row[key] for key in columns))
            if all(row[key] in translation for key in ref_columns):
                counts["aligned_rows"] += 1
                translated = tuple(translation[row[key]] if key in ref_columns else row[key]
                                   for key in columns)
                aligned.append((row, translated))
            elif any(row[key] in ambiguous_ids[side] for key in ref_columns):
                counts["ambiguous_reference_rows"] += 1
            else:
                counts["unmatched_reference_rows"] += 1
        counts["unscanned_rows"] = counts["captured_rows"] - counts["scanned_rows"]
        counts["scan_complete"] = counts["unscanned_rows"] == 0
        return counts, actual, aligned

    right_scope, right_rows, right_aligned = scan_rows(right, inverse, "right")
    left_scope, _, comparable_rows = scan_rows(left, mapping, "left")

    def compared(scope, row, side):
        scope["compared_rows"] += 1
        owner = row[ref_columns[0]]
        owners = compared_owner_sets[side]
        if owner not in owners:
            if len(owners) < request.limits.max_rows:
                owners.add(owner)
                scope["compared_owner_ids"].append(owner)
            else:
                scope["owner_ids_complete"] = False

    complete_right = (_complete(right, budget) if name in _POINTS else
                      _complete(right, budget, "edges") if name == "call_edges" else
                      right["coverage"].get("registry") == "complete")
    complete_right = complete_right and right_scope["scan_complete"]
    if name in _POINTS:
        summary_owners = _summary_owners(rhs, right, budget)
        complete_right = complete_right and summary_owners is not None and not summary_owners
    changes = []
    translated_left = {row for _, row in comparable_rows}
    for row, translated in comparable_rows:
        if not budget.visit() or len(changes) >= request.limits.max_rows:
            if len(changes) >= request.limits.max_rows:
                budget.reason = "row_limit"
            break
        compared(left_scope, row, "left")
        if translated not in right_rows:
            changes.append({"left_row": row, "aligned_right_tuple": list(translated),
                "classification": "absent_in_right_snapshot" if complete_right else "not_observed_in_partial_right"})
    complete_left = (_complete(left, budget) if name in _POINTS else
                     _complete(left, budget, "edges") if name == "call_edges" else
                     left["coverage"].get("registry") == "complete")
    complete_left = complete_left and left_scope["scan_complete"]
    if name in _POINTS:
        summary_owners = _summary_owners(data, left, budget)
        complete_left = complete_left and summary_owners is not None and not summary_owners
    for row, aligned in right_aligned:
        if not budget.visit() or len(changes) >= request.limits.max_rows:
            if len(changes) >= request.limits.max_rows:
                budget.reason = "row_limit"
            break
        compared(right_scope, row, "right")
        actual = tuple(row[k] for k in columns)
        if actual not in translated_left:
            changes.append({"right_row": row, "aligned_left_tuple": list(aligned),
                "classification": "absent_in_left_snapshot" if complete_left else "not_observed_in_partial_left"})
    for scope in (left_scope, right_scope):
        scope["comparison_scan_complete"] = (scope["scan_complete"]
            and scope["compared_rows"] == scope["aligned_rows"])
    out.update(answer="structural_diff", alignment=alignment, evidence=changes,
        selection_scope={"kind": "relation_comparison", "view": name,
                         "filters": "all_captured_rows", "intended_changes": intended},
        query_complete=budget.reason is None, coverage={"left": _coverage(left), "right": _coverage(right)},
        comparison_coverage={"view": name, "identity_model": IDENTITY_MODEL,
            "left": left_scope, "right": right_scope,
            "left_absence_coverage_complete": complete_left,
            "right_absence_coverage_complete": complete_right,
            "interpretation": "only_aligned_rows_actually_compared; no_global_equivalence_claim"},
        empty_diff_meaning=("no_difference_observed_in_compared_aligned_rows" if not changes else None),
        precision_change="not_inferred", diagnostics=["unmatched_or_ambiguous_entities_do_not_establish_deletions"])
    # Diff cursors bind both snapshots by refusing an unsupported continuation;
    # callers can request a larger explicit bounded comparison instead.
    if request.cursor:
        out = _unsupported(out, "diff_cursor_not_supported")
    return _finish(data, request, out, budget)
