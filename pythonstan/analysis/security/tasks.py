"""Score source-bound audit properties; these are not vulnerability labels."""


def evaluate_tasks(tasks, report):
    inventory = {row["source"]: row for row in report["inventory"]}
    results = []
    functions = {(f["source"],f["qualified_name"]):f for f in report["functions"]}
    for task in tasks:
        file = inventory.get(task["source"], {})
        function = functions.get((task["source"],task["qualname"]))
        if (file.get("status") != "parsed" or file.get("sha256") != task["source_sha256"]
                or function is None):
            results.append({"id":task["id"],"status":"unmeasured",
                            "reason":"source/version/parse/function binding unavailable"})
            continue
        assertion = task["assertion"]
        candidates = [f for f in report["findings"] if f["source"] == task["source"]
                      and f["function"] == task["qualname"]
                      and ("line" not in task or f["line"] == task["line"])]
        if "rule_id" in assertion: candidates=[f for f in candidates if f["rule_id"] == assertion["rule_id"]]
        kind=assertion["kind"]
        if kind == "resolved_sink":
            actual=any(f["resolution"]=="source_import_binding" and f["resolved_symbol"]==assertion["symbol"] for f in candidates)
        elif kind == "operation_candidate": actual=bool(candidates)
        elif kind == "local_parameter_flow": actual=any(assertion["parameter"] in f["parameter_dependencies"] for f in candidates)
        elif kind == "literal_policy":
            actual=any(p["name"]==assertion["keyword"] and p["status"]=="literal"
                       and type(p.get("value")) is type(assertion["value"]) and p.get("value")==assertion["value"]
                       for f in candidates for p in f["policy_evidence"])
        elif kind == "unresolved_policy":
            actual=any(p["name"]==assertion["keyword"] and p["status"]=="unresolved_expression"
                       for f in candidates for p in f["policy_evidence"])
        elif kind == "restricted_api":
            actual=any(p["status"] in {"known_restricted_api","known_safe_loader_binding"}
                       for f in candidates for p in f["policy_evidence"])
        elif kind == "function_guard":
            actual=any(g["test"]==assertion["test"] for g in function["syntactic_guard_conditions"])
        else: raise ValueError("unknown task property: "+kind)
        expected=task["expected"]
        if type(expected) is not bool: raise ValueError("audit-property labels must be Boolean")
        results.append({"id":task["id"],"status":"measured","property_prediction":actual,
                        "expected_property":expected,"matches":actual is expected,
                        "property_kind":kind,"vulnerability_label":None})
    measured=[r for r in results if r["status"]=="measured"]
    tp=sum(r["expected_property"] and r["property_prediction"] for r in measured)
    fp=sum(not r["expected_property"] and r["property_prediction"] for r in measured)
    fn=sum(r["expected_property"] and not r["property_prediction"] for r in measured)
    tn=sum(not r["expected_property"] and not r["property_prediction"] for r in measured)
    return {"unit":"hand_reviewed_source_audit_property_not_vulnerability", "tasks":results,
            "declared":len(tasks),"measured":len(measured),"unmeasured":len(tasks)-len(measured),
            "true_property_positives":tp,"false_property_positives":fp,
            "false_property_negatives":fn,"true_property_negatives":tn,
            "property_precision":tp/(tp+fp) if tp+fp else None,
            "property_recall_among_measured":tp/(tp+fn) if tp+fn else None,
            "vulnerability_precision":None,"vulnerability_recall":None,
            "interpretation":"Task properties describe APIs, parameter dependencies and policy syntax. They do not label exploitable vulnerabilities, safe applications or complete taint proofs."}
