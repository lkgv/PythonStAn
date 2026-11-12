"""Independent checks of typed configuration ownership and resolution tiers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from pythonstan.analysis.contracts.json_data import require_array, require_object
from pythonstan.config import ConfigurationError, resolve_config
from pythonstan.results.output import OutputSpec
from pythonstan.results.queries import ValueQuery, ViewQuery


@dataclass(frozen=True)
class _Target:
    kind: str
    config_search_root: Path | None


def test_configuration_owns_immutable_inputs_and_detached_projections() -> None:
    constants = [1, 2]
    raw: dict[str, object] = {
        "inputs": {"bindings": {"main.x": {"type": "int", "constants": constants}}}
    }
    resolved = resolve_config(config=raw)
    original_digest = resolved.digest
    constants.append(3)
    projection = resolved.to_dict()
    binding = require_object(require_object(projection["inputs"])["bindings"])
    require_array(require_object(binding["main.x"])["constants"]).append(4)
    provenance = resolved.provenance
    provenance["engine"] = "mutated"
    assert resolved.digest == original_digest
    assert resolved.provenance["engine"] == "builtin"
    assert isinstance(resolved.inputs.bindings, MappingProxyType)
    frozen_binding = resolved.inputs.bindings["main.x"]
    assert isinstance(frozen_binding, Mapping)
    assert frozen_binding["constants"] == (1, 2)


def test_typed_query_and_output_records_are_consumed_without_reparsing() -> None:
    query = ValueQuery("main", "result")
    output = OutputSpec(views=("calls",), max_items=4)
    resolved = resolve_config(queries=[query, ViewQuery("events")], output=output)
    assert resolved.queries[0] is query
    assert resolved.output is output
    assert resolved.observation.enabled
    assert resolved.provenance["observation.enabled"] == "query_requirement"
    assert resolved.provenance["output.max_items"] == "output_argument"


def test_output_mapping_matches_record_and_rejects_unknown_fields_in_both_tiers() -> None:
    mapped = resolve_config(output={"views": ["calls"], "max_items": 3})
    typed = resolve_config(output=OutputSpec(views=("calls",), max_items=3))
    assert mapped.to_dict() == typed.to_dict()
    assert mapped.digest == typed.digest
    with pytest.raises(ConfigurationError):
        resolve_config(config={"output": {"unexpected": 1}})
    with pytest.raises(ConfigurationError):
        resolve_config(output={"unexpected": 1})


def test_file_discovery_walks_parents_but_project_discovery_stays_at_root(tmp_path: Path) -> None:
    config = tmp_path / "pythonstan.json"
    config.write_text('{"resources":{"max_work":7}}', encoding="utf-8")
    child = tmp_path / "child"
    child.mkdir()
    file_config = resolve_config(_Target("file", child))
    project_config = resolve_config(_Target("project", child))
    assert file_config.resources.max_work == 7
    assert file_config.config_file == str(config)
    assert project_config.resources.max_work == 100000
    assert project_config.config_file is None


def test_discovery_rejects_ambiguous_default_files(tmp_path: Path) -> None:
    (tmp_path / "pythonstan.json").write_text("{}", encoding="utf-8")
    (tmp_path / "pythonstan.yaml").write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Conflicting default configurations"):
        resolve_config(_Target("project", tmp_path))


def test_config_relative_paths_and_resource_override_provenance(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        '{"inputs":{"project_root":"project","source_roots":["src"]},"resources":{"max_work":7}}',
        encoding="utf-8",
    )
    resolved = resolve_config(config=path, overrides={"resources": {"max_work": 9}})
    assert resolved.inputs.project_root == str(tmp_path / "project")
    assert resolved.inputs.source_roots == (str(tmp_path / "src"),)
    assert resolved.resources.max_work == 9
    assert resolved.provenance["inputs.project_root"] == f"config:{path}"
    assert resolved.provenance["resources.max_work"] == "override"


def test_presets_aliases_and_active_control_rejections() -> None:
    native = resolve_config(config={"mode": "kcfa", "preset": "precise"})
    assert native.engine == "native"
    assert native.andersen["context_policy"] == "3-cfa"
    abstract = resolve_config(config={"mode": "ai", "ai": {"relations": False}})
    assert abstract.engine == "native_then_ai"
    assert abstract.abstract["enable_value_partition"] is False
    with pytest.raises(ConfigurationError, match="no active public consumer"):
        resolve_config(config={"engine": "ai_primary", "andersen": {"context_policy": "3-cfa"}})
    with pytest.raises(ConfigurationError, match="conflicts"):
        resolve_config(config={"abstract": {"relations": False, "enable_value_partition": True}})


@pytest.mark.parametrize(
    "document",
    (
        '{"resources":{"max_work":1,"max_work":2}}',
        "resources:\n  max_work: 1\n  max_work: 2\n",
        "!!python/tuple [1, 2]\n",
    ),
)
def test_duplicate_and_unsafe_file_forms_raise_configuration_error(
    tmp_path: Path, document: str
) -> None:
    suffix = ".json" if document.startswith("{") else ".yaml"
    path = tmp_path / ("settings" + suffix)
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        resolve_config(config=path)


def test_cyclic_mapping_raises_configuration_error_without_recursion_escape() -> None:
    cycle: dict[str, object] = {}
    cycle["main.x"] = cycle
    with pytest.raises(ConfigurationError):
        resolve_config(config={"inputs": {"bindings": cycle}})
