"""External YAML/path values retain their public configuration meaning."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pythonstan.analysis.contracts.json_data import JsonObject
from pythonstan.config import ConfigurationError, resolve_config


def test_nested_yaml_nodes_match_the_equivalent_json_configuration(tmp_path: Path) -> None:
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "engine: ai_primary\n"
        "resources:\n  max_work: 123\n  max_seconds: 0\n"
        "inputs:\n  project_root: null\n  source_roots: [src, lib]\n"
        "  entries: []\n  bindings:\n    example.flag: {type: bool, top: true}\n",
        encoding="utf-8",
    )
    document: JsonObject = {
        "engine": "ai_primary",
        "resources": {"max_work": 123, "max_seconds": 0},
        "inputs": {
            "project_root": None,
            "source_roots": ["src", "lib"],
            "entries": [],
            "bindings": {"example.flag": {"type": "bool", "top": True}},
        },
    }
    json_path = tmp_path / "settings.json"
    json_path.write_text(json.dumps(document), encoding="utf-8")
    yaml_config = resolve_config(config=yaml_path)
    json_config = resolve_config(config=json_path)
    assert yaml_config.to_dict() == json_config.to_dict()
    assert yaml_config.digest == json_config.digest


def test_yaml_integer_block_keys_retain_canonical_duplicate_detection(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(
        'abstract:\n  solver_options:\n    loop_iters:\n      3: 1\n      "3": 2\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="Duplicate canonical loop block ID: 3"):
        resolve_config(config=path)


def test_path_objects_match_text_without_mutating_the_external_mapping(tmp_path: Path) -> None:
    roots = [tmp_path / "src", tmp_path / "lib"]
    source: dict[str, object] = {"inputs": {"project_root": tmp_path, "source_roots": roots}}
    config = resolve_config(config=source)
    equivalent = resolve_config(
        config={
            "inputs": {
                "project_root": str(tmp_path),
                "source_roots": [str(root) for root in roots],
            }
        }
    )
    assert config.to_dict() == equivalent.to_dict()
    assert roots == [tmp_path / "src", tmp_path / "lib"]
    assert config.inputs.project_root == str(tmp_path)
