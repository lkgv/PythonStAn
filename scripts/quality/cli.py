"""Check reviewed legacy debt while enforcing zero diagnostics in the frontier."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .debt import (
    Diagnostic,
    JsonValue,
    SourceIndex,
    added_diagnostics,
    baseline_diagnostics,
    changed_unit_diagnostics,
    check_frontier,
    digest,
    json_value,
    load_json,
    mapping,
    sequence,
    string,
)
from .negative_guards import run_guards
from .policy import (
    compare_reviewed_policy,
    configured_contracts,
    import_contracts_passed,
    reject_source_suppressions,
)
from .tools import ToolRun, collect_tools, production_files, run_tool, tool_versions


def check(root: Path, output: Path, comparison_ref: str | None, *, negatives: bool) -> int:
    output.mkdir(parents=True, exist_ok=True)
    baseline = mapping(load_json(root / "docs/development/next/quality_debt.json"))
    frontier = mapping(load_json(root / "docs/development/next/quality_frontier.json"))
    baseline_rows = baseline_diagnostics(baseline)
    versions = tool_versions()
    if baseline["tool_versions"] != dict(versions):
        raise ValueError("Frozen debt and current tool identities disagree")
    paths = production_files(root)
    expected_contracts = configured_contracts(root, paths)
    indices = {path: SourceIndex.read(root, path) for path in paths}
    runs = collect_tools(root, output, paths)
    diagnostics: list[Diagnostic] = []
    failures: list[str] = []
    for run in runs:
        if run.name in {"format", "ruff", "mypy"}:
            rows = parse_run(root, indices, run.name, run.stdout)
            diagnostics.extend(rows)
            if run.exit_code not in (0, 1) or (run.exit_code == 1 and not rows) or run.stderr:
                failures.append(f"Tool configuration/execution failed: {run.name}")
        elif run.exit_code != 0:
            failures.append(f"Tool execution failed: {run.name}")
        if run.name == "imports" and not import_contracts_passed(run.stdout, expected_contracts):
            failures.append("Import Linter did not verify every configured contract")
    added = added_diagnostics(diagnostics, baseline_rows)
    failures.extend(
        f"New diagnostic: {row.path}:{row.line}: {row.tool}/{row.code}" for row in added
    )
    failures.extend(check_frontier(frontier, paths, diagnostics))
    changed_rows = changed_unit_diagnostics(baseline, indices, diagnostics)
    failures.extend(
        f"Modified semantic unit has diagnostics: {row.path}:{row.symbol}: {row.tool}/{row.code}"
        for row in changed_rows
    )
    floor = {string(item) for item in sequence(baseline["initial_frontier"])}
    current_frontier = {string(item) for item in sequence(frontier["modules"])}
    if not floor <= current_frontier:
        failures.append("Typed frontier retreated behind the frozen initial frontier")
    failures.extend(reject_source_suppressions(root, current_frontier, paths))
    if comparison_ref is not None:
        failures.extend(compare_reviewed_policy(root, comparison_ref, baseline, frontier))
    new_runs = check_gate_implementation(root, output)
    failures.extend(
        f"Gate implementation has diagnostics: {run.name}" for run in new_runs if run.exit_code
    )
    negative_passed: bool | None = None
    if negatives:
        guard_report = run_guards(Path(sys.executable), output / "negative-guards")
        negative_passed = guard_report.passed
        if not negative_passed:
            failures.append(
                "A quality positive/negative control did not produce its expected result"
            )
    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    report: dict[str, JsonValue] = {
        "schema_version": 1,
        "passed": not failures,
        "source_commit": source_commit,
        "baseline_source_commit": baseline["source_commit"],
        "pyproject_sha256": digest((root / "pyproject.toml").read_bytes()),
        "tools": dict(versions),
        "scan_files": list(paths),
        "scan_source_sha256": {path: index.source_digest for path, index in indices.items()},
        "production_tools": [run.record() for run in runs],
        "gate_tools": [run.record() for run in new_runs],
        "diagnostic_counts": {
            name: sum(row.tool == name for row in diagnostics)
            for name in ("format", "ruff", "mypy")
        },
        "added_diagnostics": len(added),
        "modified_unit_diagnostics": len(changed_rows),
        "resolved_diagnostics": len(added_diagnostics(baseline_rows, diagnostics)),
        "frontier_modules": json_value(sorted(current_frontier)),
        "negative_controls_passed": negative_passed,
        "whole_tree_mypy_clean": not any(row.tool == "mypy" for row in diagnostics),
        "failures": json_value(failures),
    }
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "passed",
                    "diagnostic_counts",
                    "added_diagnostics",
                    "whole_tree_mypy_clean",
                    "failures",
                )
            }
        )
    )
    return 0 if not failures else 1


def parse_run(
    root: Path, indices: dict[str, SourceIndex], name: str, stdout: str
) -> list[Diagnostic]:
    # The import is local only to keep the main orchestration list readable.
    from .debt import parse_diagnostics

    return parse_diagnostics(root, indices, name, stdout)


def check_gate_implementation(root: Path, output: Path) -> tuple[ToolRun, ...]:
    paths = (
        "scripts/quality",
        "scripts/check_quality.py",
        *sorted(
            str(path.relative_to(root))
            for path in (root / "tests/regression/audit3").glob("test_quality*.py")
        ),
    )
    python = sys.executable
    commands = (
        ("gate-format", (python, "-m", "ruff", "format", "--check", *paths)),
        ("gate-lint", (python, "-m", "ruff", "check", *paths)),
        (
            "gate-types",
            (python, "-m", "mypy", "--explicit-package-bases", "--no-incremental", *paths),
        ),
    )
    return tuple(run_tool(root, output, name, command) for name, command in commands)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/next/evidence/N4-QUAL-01/check")
    )
    parser.add_argument(
        "--comparison-ref",
        default="HEAD",
        help="Review base for frontier/debt monotonicity (default: HEAD)",
    )
    parser.add_argument(
        "--skip-negative-controls",
        action="store_true",
        help="Local iteration only; CI runs controls",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        return check(
            root, args.output, args.comparison_ref, negatives=not args.skip_negative_controls
        )
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"Quality gate configuration failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
