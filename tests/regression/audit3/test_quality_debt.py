"""Debt identity must survive movement, but cannot mask changed behavior."""

from __future__ import annotations

from pathlib import Path

from scripts.quality.debt import (
    Diagnostic,
    JsonValue,
    SourceIndex,
    added_diagnostics,
    changed_unit_diagnostics,
    check_frontier,
)
from scripts.quality.policy import import_contracts_passed


def diagnostic(index: SourceIndex, line: int) -> Diagnostic:
    symbol, anchor = index.anchor(line)
    return Diagnostic(
        "mypy", index.path, symbol, "no-untyped-def", anchor, "Missing annotation", line
    )


def test_line_movement_preserves_counted_diagnostic_identity(tmp_path: Path) -> None:
    file = tmp_path / "sample.py"
    file.write_text("def f(x):\n    return x\n", encoding="utf-8")
    before = diagnostic(SourceIndex.read(tmp_path, "sample.py"), 1)
    file.write_text("\n\ndef f(x):\n    return x\n", encoding="utf-8")
    after = diagnostic(SourceIndex.read(tmp_path, "sample.py"), 3)
    assert not added_diagnostics([after], [before])
    assert added_diagnostics([after, after], [before]) == [after]


def test_semantic_edit_cannot_reuse_old_signature_debt(tmp_path: Path) -> None:
    file = tmp_path / "sample.py"
    file.write_text("def f(x):\n    return x\n", encoding="utf-8")
    before = SourceIndex.read(tmp_path, "sample.py")
    baseline: dict[str, JsonValue] = {
        "modules": [{"path": "sample.py", "unit_digests": dict(before.unit_digests())}]
    }
    file.write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    after = SourceIndex.read(tmp_path, "sample.py")
    existing = diagnostic(after, 1)
    assert not added_diagnostics([existing], [diagnostic(before, 1)])
    assert changed_unit_diagnostics(baseline, {"sample.py": after}, [existing]) == [existing]


def test_frontier_requires_whole_module_and_existence() -> None:
    frontier: dict[str, JsonValue] = {"modules": ["sample.py"]}
    row = Diagnostic("mypy", "sample.py", "f", "return-value", "anchor", "wrong return", 1)
    assert check_frontier(frontier, ["sample.py"], [row])
    assert check_frontier(frontier, [], [])
    assert not check_frontier(frontier, ["sample.py"], [])


def test_success_exit_without_checked_contracts_is_rejected() -> None:
    assert not import_contracts_passed("Nothing checked; exit zero", 2)
    assert not import_contracts_passed("Contracts: 1 kept, 0 broken.", 2)
    assert not import_contracts_passed("Contracts: 1 kept, 1 broken.", 2)
    assert import_contracts_passed("Contracts: 2 kept, 0 broken.", 2)
