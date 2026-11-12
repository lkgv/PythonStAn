"""Input ownership and parser identity checks independent of engine execution."""

from __future__ import annotations

import ast
from collections.abc import MutableMapping
from dataclasses import replace
from pathlib import Path

import pytest

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    thaw_json_object,
    validate_json_object,
)
from pythonstan.frontend.source import normalize_source_modules, parse_sources
from pythonstan.inputs import File, Project, Source, SourceSnapshot, snapshot_target


def input_config(*, entries: list[str] | None = None) -> JsonObject:
    return {
        "inputs": {
            "project_root": None,
            "source_roots": [],
            "entries": [] if entries is None else list(entries),
            "exclusions": [],
            "bindings": {},
        }
    }


def test_snapshot_detaches_and_freezes_boundary_records() -> None:
    original = snapshot_target(Source("x=1"), input_config())
    target = thaw_json_object(original.target)
    target["nested"] = {"paths": ["original"]}
    row: JsonObject = {"module": "snippet", "paths": ["original"]}
    detached = SourceSnapshot(
        original.modules, original.scenarios, target, original.source_digest, [row]
    )
    target["nested"] = {"paths": ["changed"]}
    row["paths"] = ["changed"]
    assert not isinstance(detached.target, MutableMapping)
    assert not isinstance(detached.inventory[0], MutableMapping)
    assert thaw_json_object(detached.target)["nested"] == {"paths": ["original"]}
    assert thaw_json_object(detached.inventory[0])["paths"] == ["original"]


def test_rerun_retains_source_after_file_removal_and_has_fresh_ast(tmp_path: Path) -> None:
    path = tmp_path / "owned.py"
    path.write_text("class C:\n    def f(self, __arg): return __arg\n")
    snapshot = snapshot_target(File(path), input_config())
    first = parse_sources(snapshot.module_map, parsed_trees=snapshot.parsed_trees)
    path.unlink()
    rerun = snapshot.for_rerun()
    assert rerun.modules == snapshot.modules
    assert rerun.scenarios == snapshot.scenarios
    assert rerun.source_digest == snapshot.source_digest
    assert not rerun.parsed_trees
    second = parse_sources(rerun.module_map, parsed_trees=rerun.parsed_trees)
    assert first.trees["owned"] is not second.trees["owned"]
    assert set(first.nodes.values()) == set(second.nodes.values())


def test_scenario_reordering_preserves_immutable_inventory(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1")
    (tmp_path / "b.py").write_text("x=2")
    snapshot = snapshot_target(Project(tmp_path), input_config())
    reordered = replace(snapshot, scenarios=tuple(reversed(snapshot.scenarios)))
    assert reordered.source_digest == snapshot.source_digest
    assert reordered.inventory == snapshot.inventory
    assert reordered.scenarios == tuple(reversed(snapshot.scenarios))
    assert not isinstance(reordered.parsed_trees, MutableMapping)


def test_required_entry_arguments_preserve_private_keyword_identity() -> None:
    source = Source("class C:\n    def f(self, /, *, __arg): return __arg\n", module="sample")
    snapshot = snapshot_target(source, input_config(entries=["sample:C.f"]))
    arguments = snapshot.scenarios[0].arguments
    assert len(arguments) == 2
    positional = validate_json_object(arguments[0])
    keyword = validate_json_object(arguments[1])
    assert positional == {"top": True, "input_contract": "arbitrary_python_value"}
    assert keyword["keyword"] == "_C__arg"


def test_json_source_decoder_rejects_invalid_fields_without_target_execution(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "not_executed"
    record: JsonObject = {
        "module": "sample",
        "code": f"open({str(marker)!r}, 'w').write('bad')\n",
    }
    program = parse_sources([record])
    assert "sample" in program.modules
    assert not marker.exists()
    record["module"] = 7
    with pytest.raises(TypeError, match="string"):
        parse_sources([record])


def test_type_ignore_identity_and_parser_cache_ownership() -> None:
    source = Source("x = 1  # type: ignore[assignment]\n", module="sample")
    snapshot = snapshot_target(source, input_config())
    program = parse_sources(snapshot.module_map, parsed_trees=snapshot.parsed_trees)
    cached_tree = snapshot.parsed_trees["sample"][1]
    assert program.trees["sample"] is cached_tree
    type_ignore = cached_tree.type_ignores[0]
    assert isinstance(type_ignore, ast.TypeIgnore)
    assert program.nodes[id(type_ignore)].endswith(":1:0:TypeIgnore")


def test_source_normalization_never_parses_and_preserves_duplicate_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_parse(*args: object, **kwargs: object) -> ast.Module:
        raise AssertionError("normalization must not parse source")

    monkeypatch.setattr(ast, "parse", unexpected_parse)
    record: JsonObject = {"module": "sample", "source": "def intentionally_unparsed("}
    modules = normalize_source_modules([record])
    assert modules["sample"].source == record["source"]
    assert not isinstance(modules, MutableMapping)
    with pytest.raises(ValueError, match="duplicate source module identity"):
        normalize_source_modules([record, record])
