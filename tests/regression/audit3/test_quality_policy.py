"""Independent mutants for policy weakening and semantic-debt concealment."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from scripts.quality.debt import Diagnostic, JsonValue, SourceIndex, changed_unit_diagnostics
from scripts.quality.policy import compare_reviewed_policy, configured_contracts


def _reviewed_repository(root: Path) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    baseline: dict[str, JsonValue] = {
        "source_commit": "frozen-source",
        "source_tree": "frozen-tree",
        "initial_frontier": [],
        "tool_versions": {},
        "modules": [
            {
                "path": "sample.py",
                "module_id": "sample",
                "source_sha256": "frozen-source-hash",
                "unit_digests": {"f": "frozen-function-hash"},
                "diagnostics": [
                    {
                        "tool": "mypy",
                        "symbol": "f",
                        "code": "no-untyped-def",
                        "anchor": "frozen-anchor",
                        "message": "Missing annotation",
                        "line": 1,
                    }
                ],
            }
        ],
    }
    frontier: dict[str, JsonValue] = {"modules": []}
    directory = root / "docs/development/next"
    directory.mkdir(parents=True)
    for name, content in (("quality_debt.json", baseline), ("quality_frontier.json", frontier)):
        (directory / name).write_text(json.dumps(content), encoding="utf-8")
    for command in (("init", "-q"), ("add", "."), ("commit", "-qm", "freeze")):
        subprocess.run(
            ["git", "-c", "user.name=Review", "-c", "user.email=review@example.invalid", *command],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    return baseline, frontier


def test_reviewed_baseline_cannot_replace_units_or_increase_count(tmp_path: Path) -> None:
    baseline, frontier = _reviewed_repository(tmp_path)
    changed = copy.deepcopy(baseline)
    modules = changed["modules"]
    assert isinstance(modules, list) and isinstance(modules[0], dict)
    modules[0]["unit_digests"] = {"f": "edited-function-hash"}
    assert compare_reviewed_policy(tmp_path, "HEAD", changed, frontier)

    changed = copy.deepcopy(baseline)
    modules = changed["modules"]
    assert isinstance(modules, list) and isinstance(modules[0], dict)
    rows = modules[0]["diagnostics"]
    assert isinstance(rows, list)
    rows.append(copy.deepcopy(rows[0]))
    assert compare_reviewed_policy(tmp_path, "HEAD", changed, frontier)


def test_invalid_comparison_ref_cannot_be_treated_as_initial_addition(tmp_path: Path) -> None:
    baseline, frontier = _reviewed_repository(tmp_path)
    with pytest.raises(ValueError):
        compare_reviewed_policy(tmp_path, "nonexistent-ref", baseline, frontier)


def test_production_only_type_waiver_is_rejected(tmp_path: Path) -> None:
    config = (Path(__file__).resolve().parents[3] / "pyproject.toml").read_text(encoding="utf-8")
    config += (
        '\n[[tool.mypy.overrides]]\nmodule = ["pythonstan.api"]\n'
        "disallow_untyped_defs = false\ncheck_untyped_defs = false\n"
    )
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    paths = ("pythonstan/api.py", "pythonstan/analysis/domains/values.py")
    with pytest.raises(ValueError):
        configured_contracts(tmp_path, paths)


def test_additive_ruff_ignore_is_rejected(tmp_path: Path) -> None:
    config = (Path(__file__).resolve().parents[3] / "pyproject.toml").read_text(encoding="utf-8")
    config = config.replace("[tool.ruff.lint]\n", '[tool.ruff.lint]\nextend-ignore = ["ANN"]\n')
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    paths = ("pythonstan/api.py", "pythonstan/analysis/domains/values.py")
    with pytest.raises(ValueError):
        configured_contracts(tmp_path, paths)


def test_import_contract_count_cannot_replace_protected_scope(tmp_path: Path) -> None:
    config = (Path(__file__).resolve().parents[3] / "pyproject.toml").read_text(encoding="utf-8")
    config = config.replace(
        'source_modules = ["pythonstan.analysis.domains", "pythonstan.analysis.contracts"]',
        'source_modules = ["pythonstan.frontend"]',
    ).replace(
        'source_modules = ["pythonstan.analysis.verification"]',
        'source_modules = ["pythonstan.frontend"]',
    )
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    paths = ("pythonstan/api.py", "pythonstan/analysis/domains/values.py")
    with pytest.raises(ValueError):
        configured_contracts(tmp_path, paths)


def test_duplicate_qualname_cannot_conceal_first_definition_edit(tmp_path: Path) -> None:
    source = "def f(x):\n    return x\n\n\nold = f\n\n\ndef f(x):\n    return x\n"
    file = tmp_path / "sample.py"
    file.write_text(source, encoding="utf-8")
    before = SourceIndex.read(tmp_path, "sample.py")
    baseline: dict[str, JsonValue] = {
        "modules": [{"path": "sample.py", "unit_digests": dict(before.unit_digests())}]
    }
    file.write_text(source.replace("return x", "return x + 1", 1), encoding="utf-8")
    after = SourceIndex.read(tmp_path, "sample.py")
    symbol, anchor = after.anchor(1)
    row = Diagnostic("mypy", "sample.py", symbol, "no-untyped-def", anchor, "Missing annotation", 1)
    assert before.ast_digest != after.ast_digest
    assert before.unit_digests() != after.unit_digests()
    assert changed_unit_diagnostics(baseline, {"sample.py": after}, [row]) == [row]
