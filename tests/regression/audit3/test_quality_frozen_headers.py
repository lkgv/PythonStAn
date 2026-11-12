"""Frozen header interpretation preserves byte binding and per-site debt counts."""

from __future__ import annotations

import ast
import copy
import hashlib
import subprocess
from pathlib import Path

import pytest

from scripts.quality.debt import (
    Diagnostic,
    JsonValue,
    SourceIndex,
    added_diagnostics,
    baseline_diagnostics,
    changed_unit_diagnostics,
    frozen_source_indices,
)


def _freeze(root: Path, source: str, diagnostics: tuple[Diagnostic, ...]) -> dict[str, JsonValue]:
    (root / "sample.py").write_text(source, encoding="utf-8")
    index = SourceIndex.read(root, "sample.py")
    for command in (("init", "-q"), ("add", "sample.py"), ("commit", "-qm", "source")):
        subprocess.run(
            ["git", "-c", "user.name=Review", "-c", "user.email=review@example.invalid", *command],
            cwd=root,
            capture_output=True,
            check=True,
        )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return {
        "source_commit": commit,
        "modules": [
            {
                "path": "sample.py",
                "module_id": "sample",
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "unit_digests": dict(index.unit_digests()),
                "diagnostics": [row.record() for row in diagnostics],
            }
        ],
    }


def _argument_row(index: SourceIndex, line: int) -> Diagnostic:
    symbol, anchor = index.anchor(line)
    return Diagnostic("ruff", "sample.py", symbol, "ANN001", anchor, "Missing annotation", line)


def test_original_multiline_header_debt_is_read_from_the_frozen_source(tmp_path: Path) -> None:
    original = "def f(first,\n      last):\n    return first\n"
    function = ast.parse(original).body[0]
    # The historical ledger used the whole function for a later header line.
    legacy_anchor = hashlib.sha256(
        ast.dump(function, include_attributes=False).encode()
    ).hexdigest()
    old = Diagnostic("ruff", "sample.py", "f", "ANN001", legacy_anchor, "Missing annotation", 2)
    baseline = _freeze(tmp_path, original, (old,))
    preserved = copy.deepcopy(baseline)
    (tmp_path / "sample.py").write_text("def f(first, last):\n    return first\n", encoding="utf-8")
    current = SourceIndex.read(tmp_path, "sample.py")

    rows = baseline_diagnostics(baseline, source_indices=frozen_source_indices(tmp_path, baseline))

    assert baseline == preserved
    assert len(rows) == 1
    assert rows[0].anchor != old.anchor
    assert added_diagnostics([_argument_row(current, 1)], rows) == []


def test_current_signature_cannot_reinterpret_the_frozen_header(tmp_path: Path) -> None:
    original = "def f(first,\n      last):\n    return first\n"
    function = ast.parse(original).body[0]
    legacy_anchor = hashlib.sha256(
        ast.dump(function, include_attributes=False).encode()
    ).hexdigest()
    old = Diagnostic("ruff", "sample.py", "f", "ANN001", legacy_anchor, "Missing annotation", 2)
    baseline = _freeze(tmp_path, original, (old,))
    (tmp_path / "sample.py").write_text(
        "def f(first, last) -> None:\n    return first\n", encoding="utf-8"
    )
    current = SourceIndex.read(tmp_path, "sample.py")
    rows = baseline_diagnostics(baseline, source_indices=frozen_source_indices(tmp_path, baseline))
    row = _argument_row(current, 1)

    assert added_diagnostics([row], rows) == [row]
    assert changed_unit_diagnostics(baseline, {"sample.py": current}, [row]) == [row]


def test_frozen_source_hash_mismatch_cannot_fall_back_to_checkout_bytes(tmp_path: Path) -> None:
    baseline = _freeze(tmp_path, "def f(first):\n    return first\n", ())
    modules = baseline["modules"]
    assert isinstance(modules, list) and isinstance(modules[0], dict)
    modules[0]["source_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="hash mismatch"):
        frozen_source_indices(tmp_path, baseline)


def test_duplicate_body_call_debt_remains_bound_to_each_distinct_call(tmp_path: Path) -> None:
    original = "def f(first, last):\n    bad(first)\n    bad(last)\n"
    index = SourceIndex.from_source("sample.py", original.encode())
    old_rows = tuple(
        Diagnostic(
            "mypy",
            "sample.py",
            index.anchor(line)[0],
            "arg-type",
            index.anchor(line)[1],
            "Bad argument",
            line,
        )
        for line in (2, 3)
    )
    baseline = _freeze(tmp_path, original, old_rows)
    (tmp_path / "sample.py").write_text(
        "def f(first, last):\n    bad(last)\n    bad(last)\n", encoding="utf-8"
    )
    current = SourceIndex.read(tmp_path, "sample.py")
    rows = baseline_diagnostics(baseline, source_indices=frozen_source_indices(tmp_path, baseline))
    new_rows = [
        Diagnostic(
            "mypy", "sample.py", "f", "arg-type", current.anchor(line)[1], "Bad argument", line
        )
        for line in (2, 3)
    ]

    assert len(added_diagnostics(new_rows, rows)) == 1
    assert changed_unit_diagnostics(baseline, {"sample.py": current}, new_rows) == new_rows


def test_reanchored_identical_header_rows_keep_their_exact_multiplicity(tmp_path: Path) -> None:
    original = "def f(first,\n      last):\n    return first\n"
    function = ast.parse(original).body[0]
    whole = hashlib.sha256(ast.dump(function, include_attributes=False).encode()).hexdigest()
    index = SourceIndex.from_source("sample.py", original.encode())
    first = _argument_row(index, 1)
    second = Diagnostic("ruff", "sample.py", "f", "ANN001", whole, "Missing annotation", 2)
    baseline = _freeze(tmp_path, original, (first, second))
    (tmp_path / "sample.py").write_text("def f(first, last):\n    return first\n", encoding="utf-8")
    current = _argument_row(SourceIndex.read(tmp_path, "sample.py"), 1)
    rows = baseline_diagnostics(baseline, source_indices=frozen_source_indices(tmp_path, baseline))

    assert len(rows) == 2
    assert added_diagnostics([current, current], rows) == []
    assert added_diagnostics([current, current, current], rows) == [current]


def test_first_nested_definition_decorators_are_body_locations() -> None:
    source = (
        "def f(first,\n"
        "      last):\n"
        "    @decorate(first)\n"
        "    def child():\n"
        "        return last\n"
        "    return child\n"
    )
    index = SourceIndex.from_source("sample.py", source.encode())

    assert index.function_header_anchors(2) is not None
    assert index.function_header_anchors(3) is None


def test_unrecognized_header_anchor_is_not_accepted_as_frozen_debt(tmp_path: Path) -> None:
    source = "def f(first,\n      last):\n    return first\n"
    unrelated = Diagnostic("ruff", "sample.py", "f", "ANN001", "0" * 64, "Missing annotation", 2)
    baseline = _freeze(tmp_path, source, (unrelated,))
    indices = frozen_source_indices(tmp_path, baseline)

    with pytest.raises(ValueError, match="does not match its source"):
        baseline_diagnostics(baseline, source_indices=indices)


def test_unavailable_frozen_commit_cannot_use_current_checkout_source(tmp_path: Path) -> None:
    baseline = _freeze(tmp_path, "def f(first):\n    return first\n", ())
    baseline["source_commit"] = "0" * 40

    with pytest.raises(ValueError, match="unavailable"):
        frozen_source_indices(tmp_path, baseline)
