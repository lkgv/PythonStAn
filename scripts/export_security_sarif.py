#!/usr/bin/env python3
"""Export source-audit candidates as SARIF notes, without vulnerability verdicts."""
import argparse
import json
from pathlib import Path


def export(reports):
    rules = {}
    results = []
    for report in reports:
        project = report["project_id"]
        for finding in report.get("findings", []):
            rule = finding["rule_id"]
            rules[rule] = {"id": rule, "shortDescription": {"text": "Review " + rule.replace("_", " ")},
                           "properties": {"tags": ["defensive-audit", "unverified-candidate"]}}
            results.append({"ruleId": rule, "level": "note", "kind": "review",
                "message": {"text": "Review candidate at " + finding["function"] + ": " +
                    (finding.get("resolved_symbol") or finding["callee_expression"]) +
                    ". API presence and local parameter dependence do not establish a vulnerability or safety."},
                "locations": [{"physicalLocation": {"artifactLocation": {
                    "uri": project + "/" + finding["source"]}, "region": {
                    "startLine": finding["line"], "startColumn": finding.get("column", 0) + 1}}}],
                "partialFingerprints": {"sourceHash": finding["source_sha256"], "auditIdentity": project + "/" + finding["id"]},
                "properties": {"evidenceOrigin": finding["origin"],
                    "apiResolution": finding.get("resolution"),
                    "parameterDependencies": finding.get("parameter_dependencies", []),
                    "policyEvidence": finding.get("policy_evidence", []),
                    "vulnerabilityConfirmed": False, "sanitizationProved": False}})
    return {"version": "2.1.0", "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{"tool": {"driver": {"name": "PythonStAn defensive source audit", "version": "round8",
                  "rules": [rules[k] for k in sorted(rules)]}}, "results": results,
                  "properties": {"securityComplete": False, "vulnerabilityVerdictsProvided": False}}]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    reports=[json.loads(p.read_text()) for p in sorted(args.reports.glob("*.json")) if p.name!="summary.json"]
    value=export(reports);args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(value,indent=2)+"\n")
    print(json.dumps({"audit_candidates":len(value["runs"][0]["results"]),"confirmed_vulnerability_verdicts":False}))


if __name__=="__main__":main()
