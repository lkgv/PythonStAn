"""Independent finite keyword-collector claims over immutable DTOs.

This module deliberately imports no analyzer, publisher or scheduler. Source
binding and the adapter's exact typed coverage are explicit trust boundaries.
"""
import ast
import hashlib

from .contract import QueryRequest, REFERENCE_STATES, canonical_bytes
from .core import common_cut_admissible, envelope, owner_complete


GENERATION = "keyword_collector_generation_v1"
CHANNELS = "keyword_collector_channels_v1"
_DIMENSIONS = {"invocation", "caller_scope", "callsite", "callee_scope", "inputs",
               "signature", "collector", "channels", "consumers"}
_SIGNATURE_FIELDS = ("version", "posonly", "positional", "kwonly", "vararg", "kwarg",
                     "positional_defaults", "kwonly_defaults")


class OutsideSpecification(ValueError):
    """Source does not belong to the declared finite binding grammar."""


def _scalar(node):
    negative = isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
    value = node.operand if negative else node
    if not isinstance(value, ast.Constant) or type(value.value) not in (str, int, bool, type(None)):
        raise OutsideSpecification("finite_scalar_required")
    value = value.value
    if negative:
        if type(value) is not int:
            raise OutsideSpecification("negative_integer_required")
        value = -value
    if type(value) is str and len(value) > 256 or type(value) is int and abs(value) > 2 ** 53:
        raise OutsideSpecification("finite_scalar_limit")
    return value


def _signature(function):
    args = function.args
    names = [p.arg for p in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    names += [p.arg for p in (args.vararg, args.kwarg) if p is not None]
    if (function.decorator_list or function.returns or function.type_comment
            or getattr(function, "type_params", ()) or args.vararg
            or any(p.annotation or getattr(p, "type_comment", None)
                   for p in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                             *((args.kwarg,) if args.kwarg else ())))
            or len(names) > 32 or any(len(n) > 512 for n in names) or len(function.name) > 512
            or len(set(names)) != len(names)):
        raise OutsideSpecification("unsupported_finite_signature")
    for default in (*args.defaults, *(d for d in args.kw_defaults if d is not None)):
        _scalar(default)
    signature = {"version": 1, "posonly": [p.arg for p in args.posonlyargs],
                 "positional": [p.arg for p in args.args],
                 "kwonly": [p.arg for p in args.kwonlyargs], "vararg": None,
                 "kwarg": args.kwarg.arg if args.kwarg else None,
                 "positional_defaults": len(args.defaults),
                 "kwonly_defaults": [d is not None for d in args.kw_defaults]}
    signature["sha256"] = hashlib.sha256(canonical_bytes(signature)).hexdigest()
    return signature


def _same_ast(left, text):
    return ast.dump(left, include_attributes=False) == ast.dump(ast.parse(text).body[0], include_attributes=False)


def _collector_body(function, collector):
    """Recognize read-only bodies, rather than asserting arbitrary mutation safety."""
    if len(function.body) == 1:
        statement = function.body[0]
        if isinstance(statement, ast.Pass):
            return "read_only"
        if isinstance(statement, ast.Return):
            expression = statement.value
            if isinstance(expression, ast.Name) and expression.id == collector:
                return "read_only"
            for method in ("keys", "values"):
                if _same_ast(statement, f"return next(iter({collector}.{method}()))"):
                    return method
    # The finite forwarding body intentionally has no arbitrary alias/mutation
    # allowance. Alpha-renaming its local names is safe and remains finite.
    body = function.body
    if len(body) == 3 and isinstance(body[0], ast.FunctionDef):
        nested, selected, returned = body
        if isinstance(selected, ast.Assign) and len(selected.targets) == 1 and isinstance(selected.targets[0], ast.Name):
            selected_name = selected.targets[0].id
            if (_same_ast(selected, f"{selected_name} = next({nested.name}())")
                    and _same_ast(returned, f"return {collector}[{selected_name}]()")):
                synthetic = ast.parse(f"pending = {nested.name}()\n{selected_name} = next(pending)\nreturn {collector}[{selected_name}]()")
                body = [nested, *synthetic.body]
    if len(body) != 4 or not isinstance(body[0], ast.FunctionDef):
        raise OutsideSpecification("collector_body_not_channel_preserving")
    nested, pending, selected, returned = body
    _signature(nested)
    if (nested.args.args or nested.args.posonlyargs or nested.args.kwonlyargs
            or nested.args.vararg or nested.args.kwarg or nested.decorator_list
            or len(nested.body) != 1 or not _same_ast(nested.body[0], f"yield from {collector}.keys()")
            or not isinstance(pending, ast.Assign) or len(pending.targets) != 1
            or not isinstance(pending.targets[0], ast.Name)
            or not isinstance(selected, ast.Assign) or len(selected.targets) != 1
            or not isinstance(selected.targets[0], ast.Name)):
        raise OutsideSpecification("collector_body_not_channel_preserving")
    pending_name, selected_name = pending.targets[0].id, selected.targets[0].id
    if (len({nested.name, pending_name, selected_name, collector, "next"}) != 5
            or not _same_ast(pending, f"{pending_name} = {nested.name}()")
            or not _same_ast(selected, f"{selected_name} = next({pending_name})")
            or not _same_ast(returned, f"return {collector}[{selected_name}]()")):
        raise OutsideSpecification("collector_body_not_channel_preserving")
    return "trusted_leaf_forwarding"


def _leaf(function, events):
    signature = _signature(function)
    if any(signature[k] for k in ("posonly", "positional", "kwonly", "kwarg")):
        raise OutsideSpecification("callback_must_be_zero_argument_leaf")
    body = function.body
    if len(body) == 2 and isinstance(body[0], ast.Expr):
        call = body[0].value
        if (events is None or not isinstance(call, ast.Call) or call.keywords or len(call.args) != 1
                or not isinstance(call.func, ast.Attribute) or call.func.attr != "append"
                or not isinstance(call.func.value, ast.Name) or call.func.value.id != events):
            raise OutsideSpecification("callback_must_be_trusted_leaf")
        _scalar(call.args[0])
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        raise OutsideSpecification("callback_must_be_trusted_leaf")
    _scalar(body[0].value)


def finite_spec(source_text):
    """Derive finite binding obligations without inspecting producer output.

    FunctionRef operands refer to unique, trusted source leaf definitions; the
    checker never executes their bodies. The body grammar preserves collector
    channels, which is needed for a final-cut presence claim.
    """
    if type(source_text) is not str:
        raise OutsideSpecification("source_text_required")
    try:
        encoded = source_text.encode("utf-8")
        if len(encoded) > 16384:
            raise OutsideSpecification("source_limit")
        tree = ast.parse(source_text)
        compile(tree, "<finite-collector-spec>", "exec")
    except (UnicodeError, SyntaxError, ValueError, RecursionError) as exc:
        raise OutsideSpecification("invalid_finite_source") from exc
    functions = {s.name: s for s in tree.body if isinstance(s, ast.FunctionDef)}
    if not 1 <= len(functions) <= 16 or len(functions) != sum(isinstance(s, ast.FunctionDef) for s in tree.body):
        raise OutsideSpecification("unique_bounded_function_definitions_required")
    signatures = {name: _signature(function) for name, function in functions.items()}
    collectors = [name for name, signature in signatures.items() if signature["kwarg"] is not None]
    if len(collectors) != 1:
        raise OutsideSpecification("one_collector_function_required")
    name = collectors[0]
    function, signature = functions[name], signatures[name]
    if (any(n in ("next", "iter") for n in functions)
            or any(n in ("next", "iter") for n in
                   (*signature["posonly"], *signature["positional"], *signature["kwonly"], signature["kwarg"]))):
        raise OutsideSpecification("builtin_shadowing_outside_specification")
    mode = _collector_body(function, signature["kwarg"])
    values, assigned, available_functions, calls, events = {}, set(), set(), [], None

    def operand(node):
        if isinstance(node, ast.Name):
            if node.id in values:
                return values[node.id]
            if node.id in available_functions and node.id != name:
                leaf = functions[node.id]
                _leaf(leaf, events)
                return {"kind": "source_function", "name": leaf.name,
                        "definition_line": leaf.lineno, "definition_column": leaf.col_offset}
            raise OutsideSpecification("operand_name_not_unique_prior_source_value")
        return _scalar(node)

    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef):
            available_functions.add(statement.name)
            continue
        target = None
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            target = statement.targets[0].id
            if target in assigned or target in functions or target in ("next", "iter"):
                raise OutsideSpecification("top_level_rebinding_outside_specification")
            assigned.add(target)
            expression = statement.value
            if isinstance(expression, ast.List) and not expression.elts and events is None:
                events = target
                continue
            if not isinstance(expression, ast.Call):
                value = operand(expression)
                if isinstance(value, dict):
                    raise OutsideSpecification("function_alias_outside_specification")
                values[target] = value
                continue
        elif isinstance(statement, ast.Expr):
            expression = statement.value
        else:
            raise OutsideSpecification("unsupported_top_level_statement")
        if (not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name)
                or expression.func.id != name or name not in available_functions
                or any(isinstance(a, ast.Starred) for a in expression.args)):
            raise OutsideSpecification("direct_collector_call_required")
        positional = [operand(a) for a in expression.args]
        keywords, form = [], "explicit"
        if any(k.arg is None for k in expression.keywords):
            if len(expression.keywords) != 1:
                raise OutsideSpecification("single_pure_expansion_required")
            mapping = expression.keywords[0].value
            if not isinstance(mapping, ast.Dict) or any(k is None for k in mapping.keys):
                raise OutsideSpecification("pure_dict_literal_required")
            form = "expanded_literal"
            for key, value in zip(mapping.keys, mapping.values):
                key = _scalar(key)
                if type(key) is not str:
                    raise OutsideSpecification("string_keyword_required")
                keywords.append((key, operand(value)))
        else:
            keywords = [(k.arg, operand(k.value)) for k in expression.keywords]
        if len(keywords) > 32 or len({k for k, _ in keywords}) != len(keywords):
            raise OutsideSpecification("duplicate_or_excessive_keywords")
        positional_names = signature["posonly"] + signature["positional"]
        if len(positional) > len(positional_names):
            raise OutsideSpecification("invalid_excess_positional_arguments")
        bound = set(positional_names[:len(positional)])
        residual = []
        for key, value in keywords:
            if key in signature["positional"] or key in signature["kwonly"]:
                if key in bound:
                    raise OutsideSpecification("invalid_duplicate_parameter_binding")
                bound.add(key)
            else:
                residual.append((key, value))
        required = positional_names[:len(positional_names) - signature["positional_defaults"]]
        required += [n for n, default in zip(signature["kwonly"], signature["kwonly_defaults"]) if not default]
        if any(n not in bound for n in required):
            raise OutsideSpecification("invalid_missing_required_parameter")
        if mode == "trusted_leaf_forwarding" and any(not isinstance(v, dict) for _, v in residual):
            raise OutsideSpecification("forwarding_requires_trusted_source_leaf")
        calls.append({"function": name, "definition_line": function.lineno,
                      "definition_column": function.col_offset, "line": expression.lineno,
                      "column": expression.col_offset, "form": form, "keywords": keywords,
                      "remaining": residual, "positional_count": len(positional),
                      "signature": signature, "body_premise": mode})
        if len(calls) > 16:
            raise OutsideSpecification("source_call_limit")
    if not calls:
        raise OutsideSpecification("no_independently_enumerated_invocations")
    return {"source_sha256": hashlib.sha256(encoded).hexdigest(), "calls": calls,
            "body_premise": "finite read-only collector and trusted callback leaf bodies"}


def _ref(value):
    return value.get("ref") if type(value) is dict and value.get("status") == "resolved" else None


def _link(entity, name):
    return _ref(entity.get("links", {}).get(name, {}))


def _exact(entity):
    return (type(entity) is dict and entity.get("attributes", {}).get("description_coverage") == "complete"
            and entity.get("attributes", {}).get("native_summary") is False)


def _context_exact(identity, entities, seen=()):
    if identity in seen or len(seen) > 4:
        return False
    entity = entities.get(identity, {})
    attrs = entity.get("attributes", {})
    if (entity.get("kind") != "context" or attrs.get("description_coverage") != "complete"
            or attrs.get("native_summary") is True
            or attrs.get("engine_class") not in ("CallStringContext", "ObjectContext", "TypeContext",
                                                 "ReceiverContext", "ParamContext", "HybridContext", "ProtocolKeyContext")):
        return False
    if attrs.get("engine_class") == "ProtocolKeyContext":
        partition = entities.get(_link(entity, "partition"), {})
        return (_context_exact(_link(entity, "inner"), entities, (*seen, identity))
                and partition.get("kind") == "context_partition"
                and partition.get("attributes", {}).get("description_coverage") == "complete")
    return True


def _matches(entity, expected, entities, path):
    if (not _exact(entity) or entity.get("kind") != "object"
            or not _context_exact(_link(entity, "context"), entities)):
        return None
    attrs = entity["attributes"]
    if attrs.get("engine_class") == "ConstantObject" and "value" in attrs:
        if isinstance(expected, dict):
            return False
        return type(attrs["value"]) is type(expected) and attrs["value"] == expected
    if attrs.get("engine_class") == "FunctionObject":
        if not isinstance(expected, dict):
            return False
        origin = entity.get("origin", {})
        definition = entities.get(_link(entity, "ir"), {})
        defined = definition.get("origin", {})
        return (origin.get("file") == path and origin.get("line") == expected["definition_line"]
                and origin.get("name") == expected["name"]
                and definition.get("kind") == "origin"
                and definition.get("attributes", {}).get("engine_class") == "IRFunc"
                and defined.get("line") == expected["definition_line"]
                and defined.get("name") == expected["name"])
    return None


def _view(data, name, dimensions, semantics):
    view = data["snapshot"].get("views", {}).get(name)
    if (type(view) is not dict or type(view.get("version")) is not int
            or view.get("version") != 1 or view.get("semantic_version") != semantics
            or view.get("fidelity") != "exact" or type(view.get("operations")) is not list
            or type(view.get("dimensions")) is not list
            or len(view["operations"]) > 8 or len(view["dimensions"]) > 16
            or any(type(d) is not str for d in view["dimensions"])
            or "enumerate" not in view["operations"]
            or not dimensions.issubset(view["dimensions"])):
        return None
    return view


def model_shape_supported(model, budget):
    """Validate only the optional DTO fields this module actually interprets.

    Legacy storage deliberately preserves unknown views. That compatibility is
    not permission to interpret arbitrary JSON as this versioned model schema.
    """
    states = {"complete", "partial", "unsupported", "not_captured", "opaque", "unknown"}

    def state(value):
        return type(value) is str and value in states

    def identity(value):
        return value is None or type(value) is int and value > 0

    def reference(value):
        return (budget.visit() and type(value) is dict and type(value.get("status")) is str
                and value["status"] in REFERENCE_STATES
                and (type(value.get("ref")) is int and value["ref"] > 0
                     if value.get("status") == "resolved" else value.get("ref") is None))

    def coverage(value):
        return (type(value) is dict and len(value) <= 32
                and all(budget.visit() and type(k) is str and state(v) for k, v in value.items()))

    def channels(value):
        if type(value) is not list:
            return False
        for channel in value:
            if not budget.visit():
                return False
            if (type(channel) is not dict or channel.get("kind") not in
                    ("key", "aggregate", "native_keys", "native_nonscalar_keys")
                    or channel.get("key") is not None and type(channel.get("key")) is not str
                    or not reference(channel.get("owner")) or type(channel.get("field_exists")) is not bool):
                return False
        return True

    def objects(value):
        if type(value) is not list:
            return False
        for obj in value:
            if (not budget.visit() or type(obj) is not dict or not identity(obj.get("object"))
                    or not channels(obj.get("channels"))):
                return False
        return True

    top = model.get("coverage")
    if (type(model.get("rows")) is not list or type(top) is not dict
            or not state(top.get("registry")) or not coverage(top.get("fields"))
            or type(top.get("owners")) is not dict):
        return False
    for row in model["rows"]:
        if not budget.visit() or type(row) is not dict:
            return False
        if (type(row.get("invocation")) is not int or row["invocation"] < 1
                or any(not identity(row.get(k)) for k in ("owner", "caller_scope", "callsite", "statement", "callee_scope", "callee_object"))
                or type(row.get("call_kind")) is not str
                or any(not reference(row.get(k)) for k in ("semantic_partition", "callee_input", "target"))
                or type(row.get("positional")) is not list or not all(reference(p) for p in row["positional"])
                or type(row.get("keywords")) is not list
                or not all(type(k) is dict and type(k.get("name")) is str and reference(k.get("value")) for k in row["keywords"])
                or type(row.get("signature")) is not dict or not coverage(row.get("coverage"))
                or type(row.get("collector")) is not dict or not reference(row["collector"].get("parameter"))
                or not state(row["collector"].get("coverage")) or not objects(row["collector"].get("objects"))
                or type(row.get("input_mappings")) is not list or type(row.get("consumers")) is not list
                or not all(budget.visit() and type(c) is dict for c in row["consumers"])):
            return False
        signature = row["signature"]
        if not state(signature.get("coverage")):
            return False
        if signature.get("coverage") == "complete":
            if (type(signature.get("version")) is not int or signature.get("version") != 1
                    or any(type(signature.get(k)) is not list or len(signature[k]) > 64
                           or not all(type(n) is str and len(n) <= 512 for n in signature[k])
                           for k in ("posonly", "positional", "kwonly"))
                    or any(signature.get(k) is not None and (type(signature[k]) is not str or len(signature[k]) > 512)
                           for k in ("vararg", "kwarg"))
                    or type(signature.get("positional_defaults")) is not int
                    or not 0 <= signature["positional_defaults"] <= 64
                    or type(signature.get("kwonly_defaults")) is not list
                    or len(signature["kwonly_defaults"]) > 64
                    or not all(type(b) is bool for b in signature["kwonly_defaults"])
                    or type(signature.get("sha256")) is not str or len(signature["sha256"]) != 64):
                return False
        for mapping in row["input_mappings"]:
            if (not budget.visit() or type(mapping) is not dict or not reference(mapping.get("input"))
                    or not state(mapping.get("coverage")) or not objects(mapping.get("objects"))):
                return False
    return True


def check_collector(data, request, out, budget, finish):
    """Check the selected finite claim without changing its requested meaning."""
    claim, snap, run = out["claim"], data["snapshot"], data.get("run", {})
    out["specification"].update(
        scope="all independently admitted finite source invocations",
        obligation="required collector binding and key/value/native-key facts" if claim == GENERATION else
                   "bound collector channels retain actual finite value-input facts",
        excluded=["arbitrary Mapping", "consumer result", "callback/effect completeness", "delivery history",
                  "collector precision upper bound", "general Python soundness"])
    out.update(obligations_expected=0, obligations_examined=0, obligations_unexamined=0,
               selected_invocations=[], frontiers=[])

    def stop(status, reason):
        out["obligations_unexamined"] = max(0, out["obligations_expected"] - out["obligations_examined"])
        return finish(out, budget, status, reason)

    try:
        spec = finite_spec(request.args.get("source_text"))
    except OutsideSpecification as exc:
        source = request.args.get("source_text")
        if type(source) is str:
            try:
                source.encode("utf-8")
            except UnicodeError:
                out["parameters"]["source_text"] = {"representation": "python_unicode_escape_v1",
                    "text": source.encode("unicode_escape").decode("ascii")}
        return stop("unsupported", str(exc))
    out["source_specification_sha256"] = spec["source_sha256"]
    out["premises"] = ["source bytes pre/post bound to input", "independent finite source binding",
                       spec["body_premise"], "single coherent completed cut",
                       "typed actual admission and full callee signature", "exact relevant owner coverage"]
    out["obligations_expected"] = sum((1 if claim == GENERATION else 0) + 3 * len(c["remaining"]) for c in spec["calls"])
    if not budget.visit():
        return stop("indeterminate", "specification_budget")
    inputs = run.get("inputs")
    if type(inputs) is list:
        matched = [i for i in inputs if type(i) is dict
                   and i.get("sha256_before") == spec["source_sha256"] == i.get("sha256_after")]
        if len(matched) != 1 or run.get("source_validation") != "pre_post_verified":
            return stop("indeterminate", "source_binding_not_verified")
    elif type(inputs) is dict:
        binding = run.get("selected_source_binding")
        if (type(binding) is not dict or type(binding.get("version")) is not int or binding.get("version") != 1
                or binding.get("semantic_version") != "native_pre_consumed_post_source_v1"
                or binding.get("scope") != "declared_targeted_source_file"
                or type(binding.get("rows")) is not list or len(binding["rows"]) != 1):
            return stop("unsupported", "per_file_source_identity_required")
        observation = run.get("observation")
        observation = observation if type(observation) is dict else {}
        selector, selection = observation.get("selector"), observation.get("selector_source")
        selector = selector if type(selector) is dict else {}
        selection = selection if type(selection) is dict else {}
        matched = binding["rows"]
        row = matched[0]
        if (type(row) is not dict or row.get("source_stable") is not True
                or run.get("source_validation") != "pre_post_verified_with_consumed_hashes"
                or inputs.get("identity_algorithm") != "sha256_ascii_canonical_json_rows_v1"
                or selector.get("model") != "keyword_collector"
                or row.get("path") != selector.get("file")
                or selection.get("native_source_admission") != "consumed"
                or selection.get("consumed_source_binding") != "matched_initial_selection"
                or not (row.get("sha256_before") == row.get("consumed_sha256")
                        == row.get("sha256_after") == selection.get("source_sha256") == spec["source_sha256"])):
            return stop("indeterminate", "selected_source_binding_not_verified")
        out["source_binding_scope"] = "verified_targeted_file_not_entire_project_source_spec"
    else:
        return stop("unsupported", "per_file_source_identity_required")
    path = matched[0].get("path")
    if type(path) is not str or len(path) > 512:
        return stop("unsupported", "exact_source_path_required")
    if not common_cut_admissible(snap):
        return stop("indeterminate", "coherent_snapshot_required")
    if (snap.get("boundary", {}).get("stage") != "stopped" or snap.get("stats", {}).get("converged") != 1
            or snap.get("stats", {}).get("pending_work") != 0 or run.get("stop_reason") != "completed"):
        return stop("indeterminate", "completed_fixpoint_required")
    if data.get("schema", {}).get("major") != 1 or snap.get("scope") != "all_current_state":
        return stop("unsupported", "unsupported_snapshot_schema_or_scope")
    model = _view(data, "kwargs_model", _DIMENSIONS, "registered_keyword_collector_v1")
    points = _view(data, "ctx_points", {"owner", "object", "lane"}, "raw_native_storage_v1")
    edges = _view(data, "call_edges", {"callsite", "callee", "call_kind"}, "raw_native_storage_v1")
    if any(v is None for v in (model, points, edges)):
        return stop("unsupported", "supported_exact_model_points_and_call_views_required")
    if not model_shape_supported(model, budget):
        return stop("indeterminate" if budget.reason else "unsupported", "model_view_shape_or_budget_unsupported")
    coverage = model.get("coverage", {})
    fields = coverage.get("fields", {})
    full = (coverage.get("registry") == "complete" and all(fields.get(k) == "complete"
            for k in ("constraints", "admitted_calls", "raw_heap_variables", "field_slots")))
    out["coverage"] = {"kwargs_model": {"registry": coverage.get("registry"), "fields": fields},
                       "ctx_points": {"registry": points.get("coverage", {}).get("registry")},
                       "call_edges": {"registry": edges.get("coverage", {}).get("registry")}}
    entities, by_owner = {}, {}
    for entity in snap.get("entities", ()):
        if not budget.visit():
            return stop("indeterminate", "entity_budget")
        entities[entity["id"]] = entity
    for fact in points.get("rows", ()):
        if not budget.visit():
            return stop("indeterminate", "fact_budget")
        by_owner.setdefault(fact["owner"], []).append(fact)
    actual_edges = set()
    for edge in edges.get("rows", ()):
        if not budget.visit():
            return stop("indeterminate", "call_edge_budget")
        actual_edges.add((edge.get("callsite"), edge.get("callee"), edge.get("call_kind")))
    failed, unresolved = False, not full

    def evidence(item, status):
        nonlocal failed, unresolved
        if not budget.visit():
            unresolved = True
            return False
        if len(out["evidence"]) >= request.limits.max_rows:
            budget.reason = "row_limit"
            unresolved = True
            return False
        item["actual"] = status
        if not budget.output(item):
            unresolved = True
            return False
        out["evidence"].append(item)
        if status == "absent_at_snapshot":
            failed = True
        if status == "unknown":
            unresolved = True
        else:
            out["obligations_examined"] += 1
        return True

    def channel_fact(owner, expected, actual_input=None):
        if owner is None:
            return "unknown", None
        opaque, retained = False, []
        for fact in by_owner.get(owner, ()):
            if not budget.visit():
                return "unknown", None
            entity = entities.get(fact.get("object"), {})
            match = _matches(entity, expected, entities, path)
            if fact.get("lane") != "objects" or match is None:
                opaque = True
            elif (match if actual_input is None else fact["object"] in actual_input):
                retained.append(fact["object"])
        if retained:
            return "present_at_snapshot", retained[0]
        if opaque or not owner_complete(points, owner):
            return "unknown", None
        return "absent_at_snapshot", None

    for source_call in spec["calls"]:
        selected = []
        for row in model.get("rows", ()):
            if not budget.visit():
                return stop("failed" if failed else "indeterminate", "invocation_budget")
            site, callee = entities.get(row.get("owner"), {}), entities.get(row.get("callee_scope"), {})
            origin, definition = site.get("origin", {}), callee.get("origin", {})
            if (origin.get("file") == path and origin.get("line") == source_call["line"]
                    and origin.get("column") == source_call["column"]
                    and definition.get("file") == path and definition.get("line") == source_call["definition_line"]
                    and definition.get("name") == source_call["function"]):
                selected.append(row)
        if not selected:
            unresolved = True
            out["frontiers"].append({"source_call": {"line": source_call["line"], "column": source_call["column"]},
                                     "reason": "independent_admitted_invocation_not_identified"})
            continue
        if len(selected) > 1:
            out["obligations_expected"] += (len(selected) - 1) * ((1 if claim == GENERATION else 0) + 3 * len(source_call["remaining"]))
        seen = set()
        for row in selected:
            scope, owner = row.get("callee_scope"), row.get("owner")
            invocation = {"invocation": row.get("invocation"), "callsite_owner": owner,
                          "callee_scope": scope, "source_line": source_call["line"],
                          "source_column": source_call["column"], "collector_inputs": len(source_call["remaining"])}
            out["selected_invocations"].append(invocation)
            site, callee = entities.get(owner, {}), entities.get(scope, {})
            signature = row.get("signature", {})
            signature_body = {k: signature.get(k) for k in _SIGNATURE_FIELDS}
            callee_input = _ref(row.get("callee_input"))
            input_functions = by_owner.get(callee_input, [])
            source_function = {"kind": "source_function", "name": source_call["function"],
                               "definition_line": source_call["definition_line"],
                               "definition_column": source_call["definition_column"]}
            keyword_names = [k.get("name") for k in row.get("keywords", ())]
            expected_names = [k for k, _ in source_call["keywords"]] if source_call["form"] == "explicit" else ["**"]
            typed = (site.get("kind") == "ctx" and site.get("attributes", {}).get("content_kind") == "callsite"
                     and site.get("attributes", {}).get("description_coverage") == "complete"
                     and callee.get("kind") == "scope" and callee.get("attributes", {}).get("description_coverage") == "complete"
                     and _link(site, "content") == row.get("callsite")
                     and _context_exact(_link(site, "context"), entities)
                     and _context_exact(_link(callee, "context"), entities)
                     and _link(callee, "object") == row.get("callee_object")
                     and signature.get("coverage") == "complete"
                     and signature.get("sha256") == source_call["signature"]["sha256"]
                     and hashlib.sha256(canonical_bytes(signature_body)).hexdigest() == signature.get("sha256")
                     and len(row.get("positional", ())) == source_call["positional_count"]
                     and sorted(keyword_names) == sorted(expected_names)
                     and owner_complete(points, callee_input) and len(input_functions) == 1
                     and input_functions[0].get("object") == row.get("callee_object")
                     and input_functions[0].get("lane") == "objects"
                     and _matches(entities.get(row.get("callee_object"), {}), source_function, entities, path) is True
                     and (owner, scope, "function") in actual_edges and row.get("call_kind") == "function")
            if (owner, scope) in seen or not typed:
                invocation["admission"] = "unresolved"
                unresolved = True
                out["frontiers"].append({**invocation, "reason": "admission_signature_or_identity_unresolved"})
                continue
            seen.add((owner, scope))
            invocation["admission"] = "established"
            row_coverage = row.get("coverage", {})
            if any(row_coverage.get(field) != "complete"
                   for field in ("inputs", "signature", "collector", "channels")):
                unresolved = True
            collector = row.get("collector", {})
            if collector.get("coverage") != "complete":
                unresolved = True
            parameter = _ref(collector.get("parameter"))
            candidates = collector.get("objects", [])
            parameter_entity = entities.get(parameter, {})
            parameter_context = _link(parameter_entity, "context")
            binding_known = (full and row_coverage.get("collector") == "complete"
                             and collector.get("coverage") == "complete")
            missing_parameter = (parameter is None and collector.get("parameter", {}).get("status") == "missing_in_engine")
            empty_binding = (parameter is not None and not candidates and owner_complete(points, parameter)
                             and not by_owner.get(parameter))
            if (missing_parameter or empty_binding) and claim == GENERATION and binding_known:
                evidence({**invocation, "violation": "required_collector_binding_missing",
                          "expected": "bound_collector", "coverage": "complete"}, "absent_at_snapshot")
                continue
            if (parameter is None or len(candidates) != 1 or not owner_complete(points, parameter)
                    or _link(parameter_entity, "scope") != scope
                    or parameter_entity.get("attributes", {}).get("name") != source_call["signature"]["kwarg"]
                    or parameter_entity.get("attributes", {}).get("name_coverage") != "exact"
                    or parameter_entity.get("attributes", {}).get("variable_kind") not in ("local", "parameter")
                    or parameter_context is None or parameter_context != _link(callee, "context")):
                unresolved = True
                out["frontiers"].append({**invocation, "reason": "actual_collector_binding_unresolved"})
                continue
            candidate = candidates[0]
            dictionary = candidate.get("object")
            dictionary_entity = entities.get(dictionary, {})
            dictionary_facts = by_owner.get(parameter, [])
            if (dictionary_entity.get("attributes", {}).get("engine_class") != "DictObject"
                    or not _exact(dictionary_entity)
                    or _link(dictionary_entity, "context") != parameter_context
                    or len(dictionary_facts) != 1 or dictionary_facts[0].get("object") != dictionary
                    or dictionary_facts[0].get("lane") != "objects"):
                unresolved = True
                out["frontiers"].append({**invocation, "reason": "single_exact_collector_required"})
                continue
            invocation.update(collector_parameter_owner=parameter, collector_object=dictionary)
            if claim == GENERATION:
                evidence({**invocation, "expected": "bound_collector"}, "present_at_snapshot")
            channels = candidate.get("channels", [])
            for key, expected in source_call["remaining"]:
                actual_input = None
                if claim == CHANNELS:
                    input_owner = None
                    if source_call["form"] == "explicit":
                        matches = [k for k in row.get("keywords", ()) if k.get("name") == key]
                        if len(matches) == 1:
                            input_owner = _ref(matches[0].get("value"))
                    else:
                        mappings = row.get("input_mappings", [])
                        if len(mappings) == 1 and mappings[0].get("coverage") == "complete" and len(mappings[0].get("objects", [])) == 1:
                            mapping = mappings[0]["objects"][0]
                            expansion = [k for k in row.get("keywords", ()) if k.get("name") == "**"]
                            expansion_owner = _ref(expansion[0].get("value")) if len(expansion) == 1 else None
                            expansion_facts = by_owner.get(expansion_owner, [])
                            mapping_entity = entities.get(mapping.get("object"), {})
                            mapping_bound = (mappings[0].get("input") == expansion[0].get("value")
                                if len(expansion) == 1 else False)
                            mapping_bound = (mapping_bound and owner_complete(points, expansion_owner)
                                and len(expansion_facts) == 1 and expansion_facts[0].get("lane") == "objects"
                                and expansion_facts[0].get("object") == mapping.get("object")
                                and _exact(mapping_entity) and mapping_entity.get("attributes", {}).get("engine_class") == "DictObject")
                            inputs_for_key = [c for c in mapping.get("channels", []) if c.get("kind") == "key" and c.get("key") == key]
                            if mapping_bound and len(inputs_for_key) == 1:
                                input_owner = _ref(inputs_for_key[0].get("owner"))
                                if _link(entities.get(input_owner, {}), "object") != mapping.get("object"):
                                    input_owner = None
                    status, input_object = channel_fact(input_owner, expected)
                    input_facts = by_owner.get(input_owner, [])
                    exact_inputs = [f["object"] for f in input_facts if f.get("lane") == "objects"
                                    and _matches(entities.get(f.get("object"), {}), expected, entities, path) is not None]
                    if (row_coverage.get("inputs") != "complete" or status != "present_at_snapshot"
                            or not owner_complete(points, input_owner) or len(exact_inputs) != len(input_facts)):
                        unresolved = True
                        out["frontiers"].append({**invocation, "key": key, "value_input_owner": input_owner,
                                                 "reason": "actual_value_input_not_independently_established"})
                        continue
                    actual_input = set(exact_inputs)
                    out["obligations_expected"] += 2 * (len(actual_input) - 1)
                obligations = ([(kind, expected, {obj}) for obj in sorted(actual_input)
                                for kind in ("key", "aggregate")] if actual_input is not None else
                               [("key", expected, None), ("aggregate", expected, None)])
                obligations.append(("native_keys", key, None))
                for kind, wanted, required_input in obligations:
                    matching = [c for c in channels if c.get("kind") == kind and
                                (c.get("key") == key if kind == "key" else c.get("key") is None)]
                    description = {**invocation, "channel": kind, "key": key, "expected_value": wanted}
                    if required_input is not None:
                        description["value_input_owner"] = input_owner
                        description["required_input_object"] = next(iter(required_input))
                    target_owner = _ref(matching[0].get("owner")) if len(matching) == 1 else None
                    if target_owner is not None:
                        field_entity = entities.get(target_owner, {})
                        attrs = field_entity.get("attributes", {})
                        expected_field = {"key": ("key", key), "aggregate": ("elem", None),
                                          "native_keys": ("class_metadata", "native_mapping_keys")}[kind]
                        if (field_entity.get("kind") != "ctx" or attrs.get("content_kind") != "field"
                                or attrs.get("description_coverage") != "complete"
                                or (attrs.get("field_kind"), attrs.get("field_name")) != expected_field
                                or attrs.get("field_index") is not None or attrs.get("field_key_type") is not None
                                or _link(field_entity, "object") != dictionary
                                or _link(field_entity, "context") != _link(dictionary_entity, "context")):
                            target_owner = None
                    channels_known = full and row_coverage.get("channels") == "complete"
                    if not matching and channels_known:
                        status, obj = "absent_at_snapshot", None
                    elif len(matching) != 1:
                        status, obj = "unknown", None
                    elif target_owner is None and matching[0].get("owner", {}).get("status") == "missing_in_engine" and channels_known:
                        status, obj = "absent_at_snapshot", None
                    else:
                        status, obj = channel_fact(target_owner, wanted, required_input)
                    if status == "absent_at_snapshot" and not channels_known:
                        status = "unknown"
                    description.update(field_owner=target_owner, object=obj)
                    if status == "absent_at_snapshot":
                        description["violation"] = "required_collector_channel_missing"
                    if not evidence(description, status):
                        return stop("failed" if failed else "indeterminate", "result_budget")
    status = "failed" if failed else "indeterminate" if unresolved else "passed"
    if out["obligations_examined"] != out["obligations_expected"] and status == "passed":
        status = "indeterminate"
    return stop(status, "finite_required_collector_claim_" + status)


def explain_keyword_collector(data, request, out, budget):
    """Expose finite OR candidates and their AND premises, never a unique cause.

    Named checks keep their full finite-source scope. An invocation selector
    filters the displayed candidate paths only; it does not weaken a claim.
    This reconstructs a snapshot frontier, not a producer execution history.
    """
    checks = {}

    def finished(result, _budget, status, *diagnostics):
        result.update(answer=status, query_complete=budget.reason is None)
        result["diagnostics"].extend(diagnostics)
        return result

    for claim in (GENERATION, CHANNELS):
        if not budget.visit():
            break
        check_request = QueryRequest("check", {"checker": claim,
            "source_text": request.args.get("source_text")}, claim=claim, limits=request.limits)
        result = envelope(data, check_request)
        result.update(claim=claim, specification={"name": claim, "version": 1})
        checks[claim] = check_collector(data, check_request, result, budget, finished)
    out.update(model="keyword_collector", answer="model_frontier", query_complete=budget.reason is None,
               logic={"candidates": "OR", "premises_within_candidate": "AND"}, candidates=[],
               explanation_scope="finite source calls; optional invocation filters candidate display",
               causal_claim="local snapshot candidate frontier; no unique root cause or delivery history",
               named_checks={name: {key: value for key, value in result.items() if key in
                   ("answer", "obligations_expected", "obligations_examined", "obligations_unexamined",
                    "source_specification_sha256", "diagnostics")} for name, result in checks.items()})
    out["selection_scope"] = {"model": "keyword_collector",
        "candidate_display": {"invocation": request.args.get("invocation", "all independently identified source invocations")},
        "named_checks": "whole finite source input"}
    if not checks:
        return out
    generation = checks.get(GENERATION, {})
    channels = checks.get(CHANNELS, {})
    out["coverage"] = generation.get("coverage", {})
    if generation.get("answer") == "unsupported":
        described_source = generation.get("parameters", {}).get("source_text")
        if type(described_source) is dict:
            out["parameters"]["source_text"] = described_source
        out.update(status="unsupported", answer="unsupported")
        out["diagnostics"].extend(generation.get("diagnostics", ()))
        return out
    rows = {}
    model = data["snapshot"].get("views", {}).get("kwargs_model", {})
    if type(model) is not dict:
        out.update(status="unsupported", answer="unsupported")
        return out
    for row in model.get("rows", ()):
        if not budget.visit():
            return out
        if type(row) is dict:
            rows[row.get("invocation")] = row
    selected = request.args.get("invocation")
    for invocation in generation.get("selected_invocations", ()):
        if not budget.visit():
            break
        identity = invocation.get("invocation")
        if selected is not None and identity != selected:
            continue
        generation_evidence = [e for e in generation.get("evidence", ()) if e.get("invocation") == identity]
        channel_evidence = [e for e in channels.get("evidence", ()) if e.get("invocation") == identity]
        frontiers = [e for result in checks.values() for e in result.get("frontiers", ())
                     if e.get("invocation") == identity]
        binding = next((e for e in generation_evidence if e.get("expected") == "bound_collector"), None)
        binding_status = binding.get("actual") if binding else "unknown"
        input_evidence = [e for e in channel_evidence if "required_input_object" in e]
        input_status = ("independently_empty" if invocation.get("collector_inputs") == 0 else
                        "unknown" if not input_evidence else "present_at_snapshot")
        required = [e for e in generation_evidence if "channel" in e]
        premises = [{"name": "source_admission", "actual": invocation.get("admission", "unresolved")},
                    {"name": "collector_binding", "actual": binding_status,
                     "owner": invocation.get("collector_parameter_owner"), "object": invocation.get("collector_object")},
                    {"name": "actual_value_inputs", "actual": input_status,
                     "facts": [{"owner": e["value_input_owner"], "object": e["required_input_object"]}
                               for e in input_evidence if e.get("channel") == "key"]}]
        for kind in ("key", "aggregate", "native_keys"):
            represented = [e for e in required if e.get("channel") == kind]
            premises.append({"name": kind, "required": [{k: e.get(k) for k in
                ("key", "expected_value", "field_owner", "object", "actual")} for e in represented]})
        if invocation.get("admission") != "established":
            frontier = "admission_or_signature_unresolved"
        elif binding_status == "absent_at_snapshot":
            frontier = "collector_generation_missing"
        elif binding_status != "present_at_snapshot":
            frontier = "collector_binding_unresolved"
        elif input_status == "unknown":
            frontier = "value_input_evidence_missing"
        elif any(e.get("actual") == "absent_at_snapshot" for e in required):
            frontier = "required_channel_missing"
        elif any(e.get("actual") == "unknown" for e in required):
            frontier = "channel_evidence_unresolved"
        elif generation.get("answer") == "indeterminate" or channels.get("answer") == "indeterminate":
            frontier = "finite_claim_range_or_coverage_unresolved"
        else:
            frontier = "required_channels_represented"
        candidate = {**invocation, "logic": "AND", "premises": premises,
                     "frontier": frontier, "unresolved_evidence": frontiers,
                     "consumers": budget.copy(rows.get(identity, {}).get("consumers", []))}
        if len(out["candidates"]) >= request.limits.max_rows:
            budget.reason = "row_limit"
            break
        if not budget.output(candidate):
            break
        out["candidates"].append(candidate)
    out["frontiers"] = [e for result in checks.values() for e in result.get("frontiers", ())
                        if "invocation" not in e]
    if not out["candidates"]:
        out["diagnostics"].append("requested_invocation_or_admission_not_observed_in_finite_range")
    out["diagnostics"].append("frontier_order_is_logical_premise_order_not_execution_time")
    out["query_complete"] = budget.reason is None
    return out
