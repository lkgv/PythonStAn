"""Mutate the actual completion union while preserving its live cleanup handler."""

from __future__ import annotations

import ast
import hashlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ExhaustivenessResult:
    positive_status: int
    negative_status: int
    positive_output: str
    negative_output: str
    handler_rejected: bool
    source_digest: str


def check_real_flow_exhaustiveness(root: Path) -> ExhaustivenessResult:
    source = (root / "pythonstan/analysis/state/flow.py").read_text()
    tree = ast.parse(source)
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "with_state"
    )
    marker = (
        "type Flow = NormalFlow | ReturnFlow | RaiseFlow | BreakFlow | ContinueFlow | SuspendFlow"
    )
    if source.count(marker) != 1:
        raise AssertionError(
            "The production completion union changed; revise this mutation explicitly"
        )
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--strict",
        "--disallow-any-expr",
        "--follow-imports=silent",
        "--no-incremental",
        "--show-error-codes",
        "pythonstan/analysis/state/flow.py",
        "pythonstan/analysis/state/handles.py",
    ]
    with tempfile.TemporaryDirectory(prefix="pythonstan-real-flow-") as directory:
        copied_root = Path(directory)
        shutil.copytree(
            root / "pythonstan",
            copied_root / "pythonstan",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copy2(root / "pyproject.toml", copied_root / "pyproject.toml")
        positive = subprocess.run(
            command, cwd=copied_root, text=True, capture_output=True, check=False
        )
        mutant = (
            source.replace(marker, marker + " | AuditExtraFlow")
            + """\n\n@dataclass(frozen=True, slots=True)\nclass AuditExtraFlow(_FlowView):\n    state: StateHandle\n    tail: FlowTail = field(default_factory=FlowTail)\n    kind: Literal["audit-extra"] = field(default="audit-extra", init=False)\n"""
        )
        (copied_root / "pythonstan/analysis/state/flow.py").write_text(mutant)
        negative = subprocess.run(
            command, cwd=copied_root, text=True, capture_output=True, check=False
        )
    diagnostics = negative.stdout + negative.stderr
    rejected = any(
        f"flow.py:{line}:" in diagnostic
        and "AuditExtraFlow" in diagnostic
        and "[arg-type]" in diagnostic
        for diagnostic in diagnostics.splitlines()
        for line in range(handler.lineno, (handler.end_lineno or handler.lineno) + 1)
    )
    return ExhaustivenessResult(
        positive.returncode,
        negative.returncode,
        positive.stdout + positive.stderr,
        diagnostics,
        rejected,
        hashlib.sha256(source.encode()).hexdigest(),
    )


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    result = check_real_flow_exhaustiveness(root)
    evidence = root / "artifacts/next/evidence/N4-TYPE-01/recovery"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "flow-exhaustiveness-positive.txt").write_text(result.positive_output)
    (evidence / "flow-exhaustiveness-mutant.txt").write_text(result.negative_output)
    print(
        f"actual_flow_sha256={result.source_digest}\npositive_status={result.positive_status}\nnew_variant_status={result.negative_status}\nunchanged_with_state_rejected={result.handler_rejected}"
    )
    return int(
        result.positive_status != 0 or result.negative_status == 0 or not result.handler_rejected
    )


if __name__ == "__main__":
    raise SystemExit(main())
