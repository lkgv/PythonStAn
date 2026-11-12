"""Integration checks for the quality tools' deliberate-error controls."""

from __future__ import annotations

import sys
from pathlib import Path

from scripts.quality.negative_guards import run_guards, validate_distribution


def test_quality_tools_reject_deliberate_errors(tmp_path: Path) -> None:
    report = run_guards(Path(sys.executable), tmp_path)
    assert report.passed, tuple(check for check in report.checks if not check.passed)
    assert {check.name for check in report.checks} == {
        "types_positive",
        "identity_swap",
        "raw_request",
        "flow_variant",
        "core_any",
        "unchecked_summary",
        "missing_reexport",
        "import_positive",
        "reverse_import",
        "ruff_positive",
        "ruff_annotation",
        "wheel_positive",
        "retired_wheel",
        "missing_py_typed",
    }
    assert all(Path(check.diagnostic_path).is_file() for check in report.checks)


def test_distribution_checks_exact_members() -> None:
    required = ("pythonstan/__init__.py", "pythonstan/py.typed")
    retired = ("pythonstan/analysis/retired.py",)
    assert validate_distribution(required, required, retired) == ()
    assert validate_distribution((*required, *retired), required, retired) == (
        "retired:pythonstan/analysis/retired.py",
    )
    assert validate_distribution((required[0],), required, retired) == (
        "missing:pythonstan/py.typed",
    )
