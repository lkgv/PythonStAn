"""Recompute cost/coverage/quality summaries from retained raw A-E records."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import sys


def aggregate(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "run_manifest.json").read_text())
    # A completed project snapshot can be large.  Keep one raw receipt in
    # memory at a time; the projected summary is intentionally bounded.
    record_paths = sorted(directory.glob("*/*/record.json"))
    expected = len(manifest["selected_cases"]) * len(manifest["selected_modes"])
    expected_pairs = {(case, mode) for case in manifest["selected_cases"] for mode in manifest["selected_modes"]}
    unique = set()
    protocol_cases = {case["id"]: case for case in manifest.get("protocol", {}).get("cases", [])}
    rows, modes = [], {}
    for path in record_paths:
        row=json.loads(path.read_text())
        unique.add((row["case_id"],row["mode"]))
        snapshot = row.get("result") or {}
        stats = snapshot.get("stats", {})
        cost = row.get("cost", {})
        receipt = row.get("process_receipt", {})
        phases = snapshot.get("phase_trace", [])
        native_phases = [phase for phase in phases if phase.get("phase") == "native"]
        oracle_stats = stats.get("oracle", {})
        expected_identity = {key: manifest["executable"][key] for key in ("git_commit", "code_sha256")}
        source_identity = manifest["input_identities"].get(row["case_id"], {}).get("digest")
        version_valid = (row.get("matched_version_valid", False)
                         and row.get("executable") == expected_identity
                         and row.get("executable_before") == row.get("executable_after") == expected_identity)
        input_valid = source_identity is not None and row.get("input_digest") == source_identity
        if row.get("process_status") == "valid_result":
            input_valid = input_valid and row.get("input_identity_matches", False) and row.get("observed_input_digest") == source_identity
        eligible = (row.get("process_status") == "valid_result" and version_valid and input_valid
                    and (row["case_id"], row["mode"]) in expected_pairs)
        # Stored supervisor flags remain raw evidence; derive comparison quality
        # again from the frozen answer and source-selected reference. A complete
        # JSON followed by a timeout/failed exit cannot become a useful result.
        answer = row.get("answer") or {}
        reference = protocol_cases.get(row["case_id"], {}).get("reference_fact", row.get("reference_fact")) or {}
        quality = {"query_status": answer.get("status", "no_data" if reference else "no_reference_query"),
                   "reference_kind": reference.get("kind"), "not_top": False,
                   "contains_reference": None, "exact_reference": None,
                   "useful_reference_hit": False, "proof_claim": False,
                   "eligible_for_comparison": eligible}
        if eligible and answer.get("status") == "answered" and reference:
            value = answer.get("value") or {}
            expected_value = reference.get("expected")
            constants = value.get("constants", [])
            contains = any(type(item) is type(expected_value) and item == expected_value for item in constants)
            not_top = (not value.get("top", True) and not value.get("bottom", True)
                       and not value.get("top_sorts") and not value.get("may_unbound")
                       and "unbound" not in value.get("types", []))
            exact = (contains and not_top and len(constants) == 1
                     and not value.get("objects") and not value.get("integer_interval")
                     and not value.get("string_patterns"))
            quality.update(not_top=not_top, contains_reference=contains,
                           exact_reference=exact,
                           useful_reference_hit=exact and reference.get("kind") in ("pure_module_literal", "controlled_semantics"))
        entry_contract=snapshot.get("entry_contract") or {}
        module_exits=snapshot.get("module_exits")
        record = {"case_id": row["case_id"], "mode": row["mode"], "category": row.get("category"),
                  "process_status": row["process_status"], "capture_status": row.get("capture_status"),
                  "solver_status": row.get("solver_status"), "coverage_status": row.get("coverage_status"),
                  "proof_status": row.get("proof_status"), "matched_version_valid": version_valid,
                  "matched_input_valid": input_valid,
                  "declared_inventory": row.get("expected_inventory_count"),
                  "actual_inventory": len(snapshot["inventory"]) if "inventory" in snapshot else None,
                  "declared_entry_scenarios": len(entry_contract["scenarios"]) if "scenarios" in entry_contract else None,
                  "expected_root_scenarios": row.get("expected_inventory_count"),
                  "discovered_units": stats.get("units"),
                  "analyzed_units": stats.get("analyzed_units"),
                  "initialized_roots": stats.get("initialized_roots"),
                  "published_module_exits": sum(len(exits) for exits in module_exits.values()) if isinstance(module_exits,dict) else None,
                  "call_count": len(snapshot["calls"]) if "calls" in snapshot else None,
                  "obligation_count": len(snapshot["obligations"]) if "obligations" in snapshot else None,
                  "wall_seconds_inclusive": receipt.get("outer_wall_seconds", cost.get("worker_inclusive_wall_seconds")),
                  "cpu_seconds_inclusive": None,
                  "cpu_seconds_before_final_json_export": sum(cost[k] for k in ("cpu_user_seconds_self", "cpu_system_seconds_self", "cpu_user_seconds_children", "cpu_system_seconds_children")) if all(isinstance(cost.get(k),(int,float)) for k in ("cpu_user_seconds_self", "cpu_system_seconds_self", "cpu_user_seconds_children", "cpu_system_seconds_children")) else None,
                  "cpu_measurement_scope": "Worker self plus waited-child CPU is collected before final result JSON serialization/printing; complete export-inclusive process CPU is unavailable.",
                  "sampled_peak_tree_rss_bytes": receipt.get("sampled_peak_process_tree_rss_bytes"),
                  "kernel_peak_self_rss_kib": cost.get("peak_self_rss_kib"),
                  "native_phase_seconds_inclusive": sum(p["seconds"] for p in native_phases) if native_phases and all(isinstance(p.get("seconds"),(int,float)) for p in native_phases) else None,
                  "native_work": stats.get("native_work", stats.get("pta_work")),
                  "legacy_native_work": stats.get("native_work"),
                  "native_prepass_work": stats.get("native_prepass_work"),
                  "native_selected_work": stats.get("native_selected_work",stats.get("selected_work")),
                  "pta_work": stats.get("pta_work"),
                  "total_charged_work": stats.get("total_work"),
                  "abstract_work": stats.get("ast_work", stats.get("work") if row["mode"] in ("D", "E") else None),
                  "oracle_work": stats.get("oracle_work", oracle_stats.get("work")),
                  "intrinsic_work": stats.get("ai_intrinsic_work"),
                  "intrinsic_bridge_work": stats.get("ai_intrinsic_bridge_work",stats.get("intrinsic_bridge_work")),
                  "source_preparation_seconds": stats.get("source_preparation_seconds"),
                  "phase_measurements": row.get("phase_measurements", {}),
                  "quality": quality,
                  "missing_counter_note": "Missing counters are null, never inferred zero; phase clocks can be nested and are not summed to invent total cost."}
        rows.append(record)
    for mode in manifest["selected_modes"]:
        selected = [row for row in rows if row["mode"] == mode]
        modes[mode] = {"rows": len(selected), "process_status": dict(Counter(row["process_status"] for row in selected)),
                       "solver_status": dict(Counter(str(row["solver_status"]) for row in selected)),
                       "coverage_status": dict(Counter(str(row["coverage_status"]) for row in selected)),
                       "useful_reference_hits": sum(row["quality"].get("useful_reference_hit", False) for row in selected),
                       "non_top_reference_matches": sum(row["quality"].get("not_top", False) and row["quality"].get("contains_reference", False) for row in selected),
                       "valid_same_version_rows": sum(row["matched_version_valid"] for row in selected)}
    summary = {"schema_version": 1, "stage": manifest["stage"], "executable": {k: manifest["executable"][k] for k in ("git_commit", "code_sha256")},
               "aggregation":{"raw_input":"one receipt at a time; retained projections only","missing_fields":"null rather than inferred zero"},
               "expected_rows": expected, "record_count": len(record_paths), "unique_rows": len(unique),
               "denominator_complete": len(record_paths) == expected == len(unique) and unique == expected_pairs,
               "missing_pairs": [list(pair) for pair in sorted(expected_pairs - unique)],
               "unexpected_pairs": [list(pair) for pair in sorted(unique - expected_pairs)],
               "same_version_valid": bool(rows) and all(row["matched_version_valid"] for row in rows),
               "same_input_valid": bool(rows) and all(row["matched_input_valid"] for row in rows),
               "modes": modes, "records": rows,
               "large_ai_primary_useful_paths": [row["case_id"] for row in rows if row["mode"] == "D" and (row.get("declared_inventory") or 0) >= 500 and row["quality"].get("useful_reference_hit")],
               "limits": ["Initialization roots are not arbitrary application-request coverage.", "Found files/parsed ASTs are distinct from executed abstract scopes or source coverage.", "Literal hits and finite witnesses do not establish full-Python soundness or whole-project call recall.", "All failures/timeouts/no-data retained; no speed ratios are inferred from censored observations."]}
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    lines = ["# A–E refactor experiments", "", f"Stage: {summary['stage']}. Executable: `{summary['executable']['git_commit']}`.", "",
             f"Retained {len(record_paths)}/{expected} expected rows; denominator complete: {summary['denominator_complete']}; same executable: {summary['same_version_valid']}; supplied/observed input identities valid: {summary['same_input_valid']}.", "",
             "| Mode | Rows | Valid result receipts | Useful reference hits | Solver states | Coverage states |",
             "| --- | ---: | ---: | ---: | --- | --- |"]
    for mode, data in modes.items():
        lines.append(f"| {mode} | {data['rows']} | {data['process_status'].get('valid_result', 0)} | {data['useful_reference_hits']} | {json.dumps(data['solver_status'], sort_keys=True)} | {json.dumps(data['coverage_status'], sort_keys=True)} |")
    lines += ["", "Export-inclusive wall clocks, RSS and typed per-phase counters are in summary.json; complete outputs, errors and receipts remain beside each record. CPU excludes final JSON export and is labeled separately; complete inclusive CPU remains null. Phase clocks and PTA/oracle counters may be nested and must not be added twice. Missing measurements remain null. Work units differ by engine; work-count reductions alone are not speed or precision evidence.", "",
              "| Case | Mode | Process | Solver | Coverage | Query | Inclusive wall (s) | Actual files / units |",
              "| --- | --- | --- | --- | --- | --- | ---: | --- |"]
    for row in rows:
        wall = row["wall_seconds_inclusive"]
        elapsed = f"{wall:.4f}" if isinstance(wall, (int, float)) else "unavailable"
        lines.append(f"| {row['case_id']} | {row['mode']} | {row['process_status']} | {row['solver_status']} | {row['coverage_status']} | {row['quality'].get('query_status', 'no data')} | {elapsed} | {row['actual_inventory']} / {row['analyzed_units']} |")
    lines += ["", "Large AI-primary useful paths: " + (", ".join(summary["large_ai_primary_useful_paths"]) or "none observed") + ".", ""]
    lines += ["- " + text for text in summary["limits"]]
    (directory / "REPORT.md").write_text("\n".join(lines) + "\n")
    return summary


if __name__ == "__main__":
    result = aggregate(sys.argv[1])
    print(json.dumps({k: result[k] for k in ("expected_rows", "record_count", "denominator_complete", "same_version_valid", "modes")}))
