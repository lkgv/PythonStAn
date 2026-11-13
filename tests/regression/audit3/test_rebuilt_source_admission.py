"""Fresh source admission witnesses for the restored execution workspace."""

from __future__ import annotations

import ast
import io
from pathlib import Path

import pytest

from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.config import resolve_config
from pythonstan.inputs import Project, Source, snapshot_target
from pythonstan.inputs.snapshot import _read


def test_source_with_no_work_never_enters_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    target = Source("result=17\n", module="sample")
    config = resolve_config(target)
    calls = 0

    def parse(*args: object, **kwargs: object) -> ast.Module:
        nonlocal calls
        calls += 1
        raise AssertionError("unfunded parser")

    monkeypatch.setattr(ast, "parse", parse)
    with pytest.raises(ResourceStop, match="max_work"):
        snapshot_target(target, config, resource_ledger=RunLedger(0, 10, 0))
    assert calls == 0


@pytest.mark.parametrize("limit,reads", [(1, 0), (2, 1), (3, 2)])
def test_file_read_attempts_are_prepaid(
    monkeypatch: pytest.MonkeyPatch, limit: int, reads: int
) -> None:
    sizes: list[int] = []

    class Spool(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            sizes.append(-1 if size is None else size)
            return super().read(size)

    def open_source(path: Path, mode: str) -> Spool:
        assert mode == "rb"
        return Spool(b"x" * 16384)

    monkeypatch.setattr(Path, "open", open_source)
    with pytest.raises(ResourceStop, match="max_work"):
        _read(Path("sample.py"), "sample", False, "root", resource_ledger=RunLedger(limit, 10, 0))
    assert sizes == [1024] * reads


def test_project_inventory_exhaustion_does_not_read_modules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "sample.py").write_text("result=17\n")
    target = Project(tmp_path)
    config = resolve_config(target)
    calls: list[Path] = []

    def open_source(path: Path, mode: str) -> io.BytesIO:
        calls.append(path)
        raise AssertionError("unfunded module")

    monkeypatch.setattr(Path, "open", open_source)
    with pytest.raises(ResourceStop, match="max_work"):
        snapshot_target(target, config, resource_ledger=RunLedger(1, 10, 0))
    assert calls == []


def test_unmatched_directory_entries_are_not_free(tmp_path: Path) -> None:
    for index in range(10):
        (tmp_path / f"note{index}.txt").write_text("irrelevant")
    target = Project(tmp_path)
    ledger = RunLedger(2, 10, 0)
    with pytest.raises(ResourceStop, match="max_work"):
        snapshot_target(target, resolve_config(target), resource_ledger=ledger)
    assert ledger.work.used == 2
    assert ledger.work.by_kind == {"source_inventory": 2}


def test_import_and_entry_inspection_reuses_the_owned_ast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = Source("def f():\n    return 17\n", module="sample")
    config = resolve_config(target, {"inputs": {"entries": ["sample:f"]}})
    original = ast.parse
    calls = 0

    def parse(
        source: str,
        filename: str = "<unknown>",
        mode: str = "exec",
        *,
        type_comments: bool = False,
        feature_version: tuple[int, int] | None = None,
    ) -> ast.Module:
        nonlocal calls
        calls += 1
        return original(
            source, filename, mode, type_comments=type_comments, feature_version=feature_version
        )

    monkeypatch.setattr(ast, "parse", parse)
    ledger = RunLedger(10000, 10, 0)
    result = snapshot_target(target, config, resource_ledger=ledger)
    assert result.scenarios[0].entry_qualname == "f"
    assert calls == 1
    assert ledger.raw_structural["source_imports"] > 0
    assert ledger.raw_structural["source_entry"] > 0
