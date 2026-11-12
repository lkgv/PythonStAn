"""Manually freeze the initial debt once, only against an unchanged source ref.

CI only invokes check_quality.py, which never calls this command. A future debt
change requires a separately reviewed migration; this tool refuses overwrite.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .debt import (
    Diagnostic,
    JsonValue,
    SourceIndex,
    json_value,
    load_json,
    mapping,
    parse_diagnostics,
    sequence,
    string,
)
from .policy import configured_contracts, import_contracts_passed
from .tools import collect_tools, production_files, tool_versions


def capture(root: Path, source_ref: str, output: Path) -> None:
    target = root / "docs/development/next/quality_debt.json"
    frontier_path = target.with_name("quality_frontier.json")
    if target.exists() or frontier_path.exists():
        raise ValueError("Refusing to overwrite a reviewed quality baseline")
    paths = production_files(root)
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", source_ref, "--", "pythonstan", "pythonstan_inspector"],
        cwd=root,
        text=True,
    )
    untracked = subprocess.check_output(
        [
            "git",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "pythonstan",
            "pythonstan_inspector",
        ],
        cwd=root,
        text=True,
    )
    if changed.strip() or untracked.strip():
        raise ValueError("Initial capture requires production source identical to frozen git ref")
    output.mkdir(parents=True, exist_ok=True)
    versions = tool_versions()
    contract_count = configured_contracts(root, paths)
    indices = {path: SourceIndex.read(root, path) for path in paths}
    runs = collect_tools(root, output, paths)
    diagnostics: list[Diagnostic] = []
    for run in runs:
        if run.name in {"format", "ruff", "mypy"}:
            rows = parse_diagnostics(root, indices, run.name, run.stdout)
            if run.exit_code not in (0, 1) or (run.exit_code == 1 and not rows) or run.stderr:
                raise ValueError(f"Tool configuration failed: {run.name}")
            diagnostics.extend(rows)
        elif run.exit_code != 0:
            raise ValueError(f"Tool execution failed: {run.name}")
        if run.name == "imports" and not import_contracts_passed(run.stdout, contract_count):
            raise ValueError("Not all configured import contracts were checked")
    tree = subprocess.check_output(
        ["git", "rev-parse", f"{source_ref}^{{tree}}"], cwd=root, text=True
    ).strip()
    commit = subprocess.check_output(["git", "rev-parse", source_ref], cwd=root, text=True).strip()
    by_path: dict[str, list[Diagnostic]] = {path: [] for path in paths}
    for row in diagnostics:
        by_path[row.path].append(row)
    modules: list[JsonValue] = []
    for path in paths:
        index = indices[path]
        modules.append(
            {
                "path": path,
                "module_id": path.removesuffix(".py").replace("/", ".").removesuffix(".__init__"),
                "owner": "typing-architecture"
                if path.startswith("pythonstan/")
                else "inspector-owner",
                "removal_task": "N4-TYPE-02"
                if path.startswith("pythonstan/")
                else "INSPECTOR-DEBT-01",
                "reason": "Pre-existing production diagnostics captured before stage-2 implementation",
                "source_sha256": index.source_digest,
                "ast_sha256": index.ast_digest,
                "unit_digests": dict(index.unit_digests()),
                "diagnostics": [row.record() for row in by_path[path]],
            }
        )
    publication = mapping(load_json(root / "docs/development/next/publication_baseline.json"))
    retired = {string(mapping(item)["path"]) for item in sequence(publication["retired_paths"])}
    floor = sorted(path for path in paths if not by_path[path] and path not in retired)
    baseline: dict[str, JsonValue] = {
        "schema_version": 1,
        "source_commit": commit,
        "source_tree": tree,
        "tool_versions": dict(versions),
        "initial_frontier": json_value(floor),
        "modules": modules,
    }
    target.write_text(
        json.dumps(baseline, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    frontier: dict[str, JsonValue] = {
        "schema_version": 1,
        "version": 1,
        "modules": json_value(floor),
    }
    frontier_path.write_text(json.dumps(frontier, indent=2) + "\n", encoding="utf-8")
    receipt: dict[str, JsonValue] = {
        "source_commit": commit,
        "source_tree": tree,
        "tools": dict(versions),
        "scan_files": list(paths),
        "runs": [run.record() for run in runs],
        "diagnostics": {
            name: sum(row.tool == name for row in diagnostics)
            for name in ("format", "ruff", "mypy")
        },
        "strict_frontier_modules": len(floor),
    }
    (output / "capture-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/next/evidence/N4-QUAL-01/baseline")
    )
    args = parser.parse_args()
    capture(Path.cwd(), args.source_ref, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
