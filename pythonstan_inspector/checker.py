"""Finite, independent checks over frozen inspector DTOs.

The registered-rule check enumerates obligations directly.  It shares neither
production transfer functions nor worklists, subscriptions, or delta cursors.
Its proof is about recorded ordinary PFG copy rules, not Python soundness or
whether all necessary rules were generated.  A separate, deliberately narrow
source fixture specification covers module-level Name-to-Name assignments.
"""
import ast
import hashlib
import time

from .contract import InspectorError, QueryRequest, canonical_bytes
from .core import (Budget, common_cut_admissible, envelope, owner_complete,
                   _supported, _request_shape, validate_parameters)
from .collector_checker import GENERATION, CHANNELS, check_collector


COPY_CLOSURE = "registered_normal_pfg_closure_v1"
MODULE_COPY_SPEC = "module_name_copy_spec_v1"
_LANES = frozenset(("objects", "classmethods", "instancemethods"))
_CHECKER_PARAMETERS = {
    COPY_CLOSURE: frozenset(("checker",)),
    MODULE_COPY_SPEC: frozenset(("checker", "source_text")),
    GENERATION: frozenset(("checker", "source_text")),
    CHANNELS: frozenset(("checker", "source_text")),
}


def _ref(snap, local_id):
    return {"snapshot": {"run_id": snap["run_id"], "snapshot_id": snap["snapshot_id"]},
            "local_id": local_id}


def _result(data, request, claim):
    result = envelope(data, request)
    result.update(claim=claim, answer="indeterminate", check_status="indeterminate",
                  specification={"name": claim, "version": 1},
                  premises=[], independence={"production_transfer_reused": False,
                  "production_scheduler_reused": False, "shared_code": [
                      "DTO contract", "query budget", "view and common-cut admission",
                      "owner coverage interpretation"],
                  "trusted": ["adapter fidelity and coverage declarations",
                              "frozen view semantic versions"]},
                  completion={"engine": data.get("run", {}).get("stop_reason", "unknown"),
                              "capture": data["snapshot"].get("capture", {}),
                              "check": "indeterminate"})
    return result


def _finish(out, budget, status, *diagnostics):
    if time.monotonic() > budget.end:
        budget.reason = "work_or_deadline_limit"
        if status == "passed":
            status = "indeterminate"
    out.update(answer=status, check_status=status)
    out["query_complete"] = budget.reason is None
    out["completion"]["check"] = status
    out["diagnostics"].extend(diagnostics)
    if budget.reason:
        out["diagnostics"].append(budget.reason)
    out["work"] = {"visited": budget.visited, "result_bytes": budget.bytes}
    if len(canonical_bytes(out)) > budget.limits.max_bytes:
        # The original request and claim may not silently disappear to make a
        # stronger conclusion fit.  Refusal is explicit and carries no pass.
        raise InspectorError("requested byte limit cannot fit the checker response")
    return out


def _view(data, name, dimensions):
    view = data["snapshot"].get("views", {}).get(name)
    if (not _supported(name, view)
            or view.get("fidelity") != "exact"
            or not set(dimensions).issubset(view.get("dimensions", ()))):
        return None
    return view


def _coverage_summary(view):
    """Bound report size without replacing any internal coverage obligation."""
    coverage = view.get("coverage", {})
    return {"registry": coverage.get("registry", "not_captured"),
            "view_version": view.get("version"),
            "semantic_version": view.get("semantic_version", "raw_native_storage_v1"),
            "fidelity": view.get("fidelity"),
            "captured_owner_count": len(coverage.get("owners", {})),
            "captured_rows": len(view.get("rows", [])),
            "owner_fields": "full captured map used by checker; negative witness states its target coverage"}


def _entities(data, budget):
    result = {}
    for entity in data["snapshot"].get("entities", ()):
        if not budget.visit():
            return None
        result[entity["id"]] = entity
    return result


def _normal(entity):
    return (isinstance(entity, dict) and entity.get("kind") == "node"
            and entity.get("attributes", {}).get("engine_class") == "NormalNode"
            and entity.get("attributes", {}).get("node_kind") == "normal")


def _known_other_node(entity):
    return (isinstance(entity, dict) and entity.get("kind") == "node"
            and entity.get("attributes", {}).get("engine_class") in
                ("GuardNode", "SelectorNode", "InheritanceSelectorNode"))


def _copy_edges(edges, entities, budget):
    """Derive the supported family from typed endpoints, not a trusted flag."""
    selected = []
    omitted = 0
    unknown = False
    for edge in edges.get("rows", ()):
        if not budget.visit():
            return None, omitted, True
        source, target = entities.get(edge.get("source")), entities.get(edge.get("target"))
        if edge.get("edge_kind") != "normal":
            omitted += 1
        elif _normal(source) and _normal(target):
            selected.append(edge)
        elif ((_normal(source) or _known_other_node(source))
              and (_normal(target) or _known_other_node(target))):
            omitted += 1
        else:
            unknown = True
    return selected, omitted, unknown


def _registered_closure(data, request, out, budget):
    snap = data["snapshot"]
    out["specification"].update(
        rule_family="edge_kind=normal and both endpoints typed NormalNode",
        obligation="for each registered edge s->t and observed fact (s,o,lane), (t,o,lane) is represented",
        enumeration="one exhaustive pass over finite edge/fact obligations; no worklist",
        excluded=["missing rule generation", "inheritance/instance transforms", "guard/selector rules",
                  "exact delivery history", "unknown models", "concrete reachability", "Python soundness"])
    if not common_cut_admissible(snap):
        return _finish(out, budget, "indeterminate", "coherent_snapshot_required")
    points = _view(data, "node_points", ("owner", "object", "lane"))
    edges = _view(data, "pfg_edges", ("source", "target", "edge_kind"))
    if points is None or edges is None:
        return _finish(out, budget, "unsupported", "exact_v1_node_points_and_pfg_edges_required")
    out["coverage"] = {"node_points": _coverage_summary(points),
                       "pfg_edges": _coverage_summary(edges)}
    entities = _entities(data, budget)
    if entities is None:
        return _finish(out, budget, "indeterminate")
    rules, omitted, unknown_rules = _copy_edges(edges, entities, budget)
    if rules is None:
        return _finish(out, budget, "indeterminate")
    out["registered_rule_scope"] = {"selected": len(rules), "other_families": omitted,
                                     "unclassified": unknown_rules}
    facts, by_owner, opaque_owners = set(), {}, set()
    for row in points.get("rows", ()):
        if not budget.visit():
            return _finish(out, budget, "indeterminate")
        owner, obj, lane = row.get("owner"), row.get("object"), row.get("lane")
        entity = entities.get(obj)
        # A native summary is faithfully enumerated but has wider containment.
        # v1 has no independent semantic order for it, so it cannot be used to
        # prove a missing consequence, or to make a full closure claim pass.
        usable = (lane in _LANES and entity is not None and entity.get("kind") == "object"
                  and entity.get("attributes", {}).get("native_summary") is False)
        if not usable:
            opaque_owners.add(owner)
        facts.add((owner, obj, lane))
        by_owner.setdefault(owner, []).append((obj, lane, usable))
    all_complete = (points.get("coverage", {}).get("registry") == "complete"
                    and edges.get("coverage", {}).get("registry") == "complete"
                    and not unknown_rules)
    violations = 0
    obligations = 0
    for rule in rules:
        if not budget.visit():
            return _finish(out, budget, "failed" if violations else "indeterminate")
        source, target = rule["source"], rule["target"]
        target_complete = owner_complete(points, target)
        all_complete = (all_complete and owner_complete(points, source) and target_complete)
        # A summary in the target owner may cover an apparently missing tuple.
        target_opaque = target in opaque_owners
        all_complete = all_complete and not target_opaque
        for obj, lane, usable in by_owner.get(source, ()):
            if not budget.visit():
                return _finish(out, budget, "failed" if violations else "indeterminate")
            obligations += 1
            all_complete = all_complete and usable
            if (target, obj, lane) in facts:
                continue
            if not usable or not target_complete or target_opaque:
                all_complete = False
                continue
            witness = {"evidence_type": "checker-proof", "violation": "copy_closure",
                       "rule": {"source": _ref(snap, source), "target": _ref(snap, target),
                                "edge_kind": "normal"},
                       "input_fact": {"owner": _ref(snap, source), "object": _ref(snap, obj), "lane": lane},
                       "missing_consequence": {"owner": _ref(snap, target), "object": _ref(snap, obj), "lane": lane},
                       "target_field_coverage": "complete", "actual": "absent_at_snapshot",
                       "expected": "present_at_snapshot"}
            if len(out["evidence"]) >= request.limits.max_rows:
                budget.reason = "row_limit"
                return _finish(out, budget, "failed" if violations else "indeterminate")
            if not budget.output(witness):
                return _finish(out, budget, "failed" if violations else "indeterminate")
            out["evidence"].append(witness)
            violations += 1
    out["obligations_examined"] = obligations
    out["premises"] = ["single coherent cut", "supported identity-copy rule family",
                       "exact observed input facts", "complete target owner facts for each negative witness"]
    if violations:
        return _finish(out, budget, "failed", "closure_violation_not_a_delivery_history_claim")
    if not all_complete:
        return _finish(out, budget, "indeterminate", "full_registered_family_coverage_or_containment_unavailable")
    return _finish(out, budget, "passed", "only_registered_ordinary_copy_closure_proved")


def _module_copy_spec(data, request, out, budget):
    """A narrow, separately named source/registration specification.

    Only a module consisting of simple assignments to names is supported.
    Constants seed it; Name RHS assignments require an ordinary registered PFG
    edge.  This intentionally does not infer a Python-wide constraint IR.
    """
    out["specification"].update(
        language="module statements: single Name target, Constant or Name RHS",
        obligation="every Name-to-Name source assignment registers an ordinary PFG copy edge",
        excluded=["nested scopes", "calls", "destructuring", "attribute/subscript assignments",
                  "general Python front-end completeness", "runtime reachability"])
    source = request.args.get("source_text")
    if type(source) is not str:
        return _finish(out, budget, "unsupported", "source_text_required_for_named_fixture_spec")
    try:
        encoded = source.encode("utf-8")
    except UnicodeEncodeError:
        # A Python caller can supply strings that JSON's UTF-8 artifact format
        # cannot represent. Preserve their exact code points in a declared
        # escaped description, while refusing the original source claim.
        out["parameters"]["source_text"] = {"representation": "python_unicode_escape_v1",
            "text": source.encode("unicode_escape").decode("ascii")}
        return _finish(out, budget, "unsupported", "source_text_not_valid_utf8")
    if len(encoded) > request.limits.max_bytes or not budget.visit():
        budget.reason = "specification_input_budget"
        return _finish(out, budget, "indeterminate")
    checksum = hashlib.sha256(encoded).hexdigest()
    inputs = data.get("run", {}).get("inputs", ())
    if type(inputs) is dict:
        # A whole-project catalog binds a companion inventory, not a per-file
        # source row available to this narrowly specified offline checker.
        # Never load that companion implicitly or substitute a weaker claim.
        return _finish(out, budget, "unsupported", "source_spec_requires_per_file_identity_rows_not_catalog")
    if type(inputs) is not list:
        return _finish(out, budget, "unsupported", "source_input_identity_encoding_unsupported")
    matched = []
    for entry in inputs:
        if not budget.visit():
            return _finish(out, budget, "indeterminate")
        if (type(entry) is not dict or type(entry.get("path")) is not str
                or any(type(entry.get(field)) is not str or len(entry[field]) != 64
                       or any(character not in "0123456789abcdef" for character in entry[field])
                       for field in ("sha256_before", "sha256_after"))):
            return _finish(out, budget, "unsupported", "source_input_identity_row_malformed")
        if entry.get("sha256_before") == checksum == entry.get("sha256_after"):
            matched.append(entry)
    if len(matched) != 1 or data.get("run", {}).get("source_validation") != "pre_post_verified":
        return _finish(out, budget, "indeterminate", "source_not_bound_to_captured_input")
    if type(matched[0].get("path")) is not str or len(matched[0]["path"]) > 512:
        return _finish(out, budget, "unsupported", "source_path_outside_exact_adapter_description_limit")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return _finish(out, budget, "unsupported", "source_outside_narrow_fixture_language")
    expected = []
    for statement in tree.body:
        if not budget.visit():
            return _finish(out, budget, "indeterminate")
        if (not isinstance(statement, ast.Assign) or len(statement.targets) != 1
                or not isinstance(statement.targets[0], ast.Name)
                or not isinstance(statement.value, (ast.Name, ast.Constant))):
            return _finish(out, budget, "unsupported", "source_outside_narrow_fixture_language")
        if (len(statement.targets[0].id) > 512
                or isinstance(statement.value, ast.Name) and len(statement.value.id) > 512):
            return _finish(out, budget, "unsupported", "source_names_outside_exact_adapter_description_limit")
        if isinstance(statement.value, ast.Name) and statement.value.id != statement.targets[0].id:
            expected.append((statement.value.id, statement.targets[0].id, statement.lineno))
    if not expected:
        return _finish(out, budget, "inapplicable", "no_nontrivial_name_copy_obligations")
    snap = data["snapshot"]
    if not common_cut_admissible(snap):
        return _finish(out, budget, "indeterminate", "coherent_snapshot_required")
    edges = _view(data, "pfg_edges", ("source", "target", "edge_kind"))
    if edges is None:
        return _finish(out, budget, "unsupported", "exact_v1_pfg_required")
    entities = _entities(data, budget)
    if entities is None:
        return _finish(out, budget, "indeterminate")
    rules, _, unknown = _copy_edges(edges, entities, budget)
    if rules is None:
        return _finish(out, budget, "indeterminate")
    edge_keys = set()
    for rule in rules:
        if not budget.visit():
            return _finish(out, budget, "indeterminate")
        edge_keys.add((rule["source"], rule["target"]))
    names = {}
    file = matched[0].get("path")
    descriptions_complete = True
    for entity in entities.values():
        if not budget.visit():
            return _finish(out, budget, "indeterminate")
        if entity.get("kind") in ("opaque", "pending"):
            descriptions_complete = False
        if not _normal(entity) or entity.get("origin", {}).get("file") != file:
            continue
        variable = entities.get(entity.get("links", {}).get("variable", {}).get("ref"), {})
        scope = entities.get(variable.get("links", {}).get("scope", {}).get("ref"), {})
        definition = entities.get(scope.get("links", {}).get("definition", {}).get("ref"), {})
        if definition.get("attributes", {}).get("engine_class") != "IRModule":
            continue
        names.setdefault(entity.get("attributes", {}).get("name"), []).append(entity["id"])
    full = edges.get("coverage", {}).get("registry") == "complete" and not unknown and descriptions_complete
    failures, unresolved = 0, False
    for source_name, target_name, line in expected:
        if not budget.visit():
            return _finish(out, budget, "failed" if failures else "indeterminate")
        source_ids, target_ids = names.get(source_name, ()), names.get(target_name, ())
        if len(source_ids) > 1 or len(target_ids) > 1:
            unresolved = True
            continue
        if source_ids and target_ids and (source_ids[0], target_ids[0]) in edge_keys:
            continue
        if not full:
            unresolved = True
            continue
        witness = {"evidence_type": "checker-proof", "violation": "missing_required_copy_rule",
                   "source_obligation": {"file": file, "line": line,
                                         "source_name": source_name, "target_name": target_name},
                   "source": _ref(snap, source_ids[0]) if source_ids else None,
                   "target": _ref(snap, target_ids[0]) if target_ids else None,
                   "expected": "ordinary_registered_copy_edge", "actual": "absent_at_snapshot"}
        if len(out["evidence"]) >= request.limits.max_rows:
            budget.reason = "row_limit"
            return _finish(out, budget, "failed" if failures else "indeterminate")
        if not budget.output(witness):
            return _finish(out, budget, "failed" if failures else "indeterminate")
        out["evidence"].append(witness)
        failures += 1
    out["coverage"] = {"pfg_edges": _coverage_summary(edges)}
    out["source_specification_sha256"] = checksum
    out["premises"] = ["source digest bound to captured pre/post input",
                       "narrow source assignment specification independent of production lowering",
                       "complete ordinary PFG registry for negative rule claims"]
    if failures:
        return _finish(out, budget, "failed")
    if unresolved or not full:
        return _finish(out, budget, "indeterminate", "source_identity_or_rule_registry_incomplete")
    return _finish(out, budget, "passed", "only_narrow_module_name_copy_registration_proved")


def check(bundle, request):
    """Return a bounded claim-preserving query envelope without engine imports."""
    _request_shape(request)
    if request.kind != "check":
        raise InspectorError("check requires a check QueryRequest")
    if type(request.args) is not dict:
        raise InspectorError("check requires QueryRequest with dictionary args")
    budget = Budget(request.limits)
    # Admission precedes JSON decoding.  The storage reader owns the hard
    # artifact-byte limit; direct check additionally charges every 4 KiB block.
    snapshot = getattr(bundle, "snapshot", bundle)
    encoded = getattr(snapshot, "encoded", None)
    if type(encoded) is not bytes:
        raise InspectorError("check requires an owned sealed snapshot or bundle")
    for _ in range(len(encoded) // 4096 + 1):
        if not budget.visit():
            raise InspectorError("checker artifact decode admission exceeded query budget")
    data = bundle.data
    if not budget.visit():
        raise InspectorError("checker artifact decode exceeded query budget")
    claim = request.args.get("checker", COPY_CLOSURE)
    if type(claim) is not str:
        raise InspectorError("checker name must be a string")
    out = _result(data, request, claim)
    if request.cursor is not None:
        return _finish(out, budget, "unsupported", "checker_continuation_not_supported")
    if request.claim and request.claim != claim:
        return _finish(out, budget, "unsupported", "requested_claim_not_implemented_no_weaker_substitution")
    allowed = _CHECKER_PARAMETERS.get(claim)
    if allowed is None:
        return _finish(out, budget, "unsupported", "unknown_checker")
    invalid = validate_parameters(request, checker_parameters=allowed)
    if invalid:
        return _finish(out, budget, "unsupported", invalid)
    if claim == COPY_CLOSURE:
        return _registered_closure(data, request, out, budget)
    if claim == MODULE_COPY_SPEC:
        return _module_copy_spec(data, request, out, budget)
    if claim in (GENERATION, CHANNELS):
        return check_collector(data, request, out, budget, _finish)
    return _finish(out, budget, "unsupported", "unknown_checker")
