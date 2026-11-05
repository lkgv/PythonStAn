#!/usr/bin/env python3
"""Build a markdown audit report from kcfa unknown report JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def classify_unknown(case_name: str, item: Dict[str, Any]) -> Tuple[str, str]:
    kind = str(item.get("kind", ""))
    location = str(item.get("location", ""))
    message = str(item.get("message", ""))
    context = str(item.get("context", ""))

    if kind == "missing_argument":
        if "CustomRange" in location and "Fibonacci.__init__" in context:
            return "FP", "Call to CustomRange mapped to Fibonacci.__init__"
        if "Fibonacci" in location and "CustomRange.__init__" in context:
            return "FP", "Call to Fibonacci mapped to CustomRange.__init__"
        return "Improve", "Argument arity mismatch for candidate callee"

    if kind == "callee_empty":
        if "descriptor_" in message or "descriptor_" in location:
            return "TP", "Descriptor protocol not supported"
        if "$getattribute" in message or "$getattr" in message:
            return "Improve", "Default attribute lookup not modeled"
        if "$setattr" in message:
            return "Improve", "Default attribute writes not modeled"
        if "range" in message or "iter" in message or "next" in message:
            if "[cell]" in context:
                return "TP", "Closure/comp scope for builtins not modeled"
            return "Improve", "Builtin should resolve but callee empty"
        if "$new@" in message:
            return "Improve", "Class instantiation target unresolved"
        if case_name == "dynamic_features" and "exercise_generator:32" in location:
            return "Improve", "Generator.send() not modeled"
        if case_name == "dataflow" and "complex_assignments:63" in location:
            return "TP", "List comprehension scope/closure not modeled"
        return "Improve", "Unresolved callee at fixpoint"

    if kind == "call_no_dispatch":
        return "Improve", "Callable targets but no successful dispatch"

    return "Improve", "Unhandled unknown kind"


def render_case(case: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"## Case: {case['case']}")
    lines.append("")
    lines.append(f"- Entry: `{case['filename']}`")
    lines.append(f"- Project: `{case['project_path']}`")
    cfg = case.get("config", {})
    lines.append(
        "- Config: "
        f"`context_policy={cfg.get('context_policy')}`, "
        f"`index_sensitive={cfg.get('index_sensitive')}`, "
        f"`enable_ai_intrinsics={cfg.get('enable_ai_intrinsics')}`, "
        f"`track_unknowns={cfg.get('track_unknowns')}`, "
        f"`log_unknown_details={cfg.get('log_unknown_details')}`, "
        f"`import_level={cfg.get('import_level')}`, "
        f"`lazy_ir_construction={cfg.get('lazy_ir_construction')}`"
    )
    lines.append("")
    lines.append("### Unknown Summary")
    lines.append("")
    lines.append("```")
    lines.append(json.dumps(case.get("unknown_summary", {}), indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    lines.append("### Unknown Details + Audit")
    lines.append("")
    details = case.get("unknown_details", [])
    if not details:
        lines.append("_No unknowns reported._")
        lines.append("")
        return "\n".join(lines)

    lines.append("| Kind | Location | Message | Context | Class | Evidence |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for item in details:
        classification, evidence = classify_unknown(case["case"], item)
        kind = str(item.get("kind", "")).replace("|", "\\|")
        location = str(item.get("location", "")).replace("|", "\\|")
        message = str(item.get("message", "")).replace("|", "\\|")
        context = str(item.get("context", "")).replace("|", "\\|")
        evidence = evidence.replace("|", "\\|")
        lines.append(
            f"| {kind} | {location} | {message} | {context} | {classification} | {evidence} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build kcfa unknown audit report.")
    parser.add_argument(
        "--input-json",
        default="benchmark/reports/kcfa_unknown_report.json",
        help="Path to JSON report from run_kcfa_unknown_report.py",
    )
    parser.add_argument(
        "--output-md",
        default="benchmark/reports/kcfa_unknown_audit.md",
        help="Output markdown audit report path",
    )
    args = parser.parse_args()

    input_path = Path(args.input_json)
    report = json.loads(input_path.read_text())

    lines: List[str] = []
    lines.append("# KCFA Unknown Audit")
    lines.append("")
    lines.append(f"Source: `{input_path}`")
    lines.append("")
    for case in report:
        lines.append(render_case(case))

    output_path = Path(args.output_md)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    print(f"Wrote audit report to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
