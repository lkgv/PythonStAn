"""Real Native process controls for the checked worker wire and observations."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import UserDict
from collections.abc import Mapping
from pathlib import Path
from typing import override

import pytest

from pythonstan import File, Project, Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_array,
    require_object,
    validate_json_object,
)
from pythonstan.analysis.pointer.kcfa.input_bindings import alternatives, validate_input_bindings
from pythonstan.compat.native_worker import WorkerHeader
from pythonstan.inputs import Target


class _RaisingPath:
    def __fspath__(self) -> str:
        raise TypeError("path sentinel")


class _EqualityPretender:
    @override
    def __eq__(self, other: object) -> bool:
        return True


def test_analysis_type_rejects_nonstring_equality_pretender() -> None:
    from pythonstan.analysis.analysis import AnalysisConfig

    with pytest.raises(ValueError, match="options.type must name a supported analysis type"):
        AnalysisConfig("pta", "PointerAnalysis", options={"type": _EqualityPretender()})


def test_pipeline_rejects_sequence_of_configuration_pairs(tmp_path: Path) -> None:
    from pythonstan.world.pipeline import Pipeline

    path = tmp_path / "subject.py"
    path.write_text("result=1\n", encoding="utf-8")
    failure = pytest.raises(ValueError, Pipeline, config=[("filename", str(path))])
    assert str(failure.value) == "pipeline configuration must be a mapping"


@pytest.mark.parametrize("field", ["filename", "project_path"])
def test_pipeline_path_conversion_preserves_value_error_cause(field: str) -> None:
    from pythonstan.world.config import Config

    fields: dict[str, object] = {"filename": "subject.py", field: _RaisingPath()}
    with pytest.raises(
        ValueError, match="filename and project_path must be filesystem paths"
    ) as failure:
        Config.from_dict(fields)
    assert isinstance(failure.value.__cause__, TypeError)
    assert str(failure.value.__cause__) == "path sentinel"


@pytest.mark.parametrize("kind", ["str", "path", "userdict"])
def test_pipeline_preserves_valid_mapping_and_pathlike_configuration(
    tmp_path: Path, kind: str
) -> None:
    from pythonstan.world.pipeline import Pipeline

    path = tmp_path / "subject.py"
    path.write_text("result=1\n", encoding="utf-8")
    settings: dict[str, object] = {
        "filename": str(path) if kind == "str" else path,
        "project_path": tmp_path,
        "mock_libs": False,
    }
    config = UserDict(settings) if kind == "userdict" else settings
    pipeline = Pipeline(config=config)
    assert pipeline.config.filename == str(path)
    assert pipeline.config.project_path == str(tmp_path)


def test_zero_work_process_does_not_require_or_analyze_source() -> None:
    payload: JsonObject = {
        "config": {"engine": "native", "resources": {"max_work": 0}},
    }
    process = subprocess.run(
        [sys.executable, "-m", "pythonstan.compat.native_worker"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    frames = [validate_json_object(json.loads(line)) for line in process.stdout.splitlines()]
    assert len(frames) == 1 and frames[0]["kind"] == "result"
    result = require_object(frames[0]["result"])
    assert result["solver_status"] == "budget_exhausted"
    assert result["coverage_status"] == "open"
    assert result["values"] == {}
    assert require_object(result["stats"])["native_work"] == 0


@pytest.mark.parametrize("maximum", [True, "0", 1.25])
def test_worker_header_rejects_noninteger_work_limit(maximum: object) -> None:
    with pytest.raises(TypeError):
        WorkerHeader.from_wire({"config": {"engine": "native", "resources": {"max_work": maximum}}})


@pytest.mark.parametrize("kind", ["source", "file", "project"])
def test_real_native_worker_retains_source_identity_and_none(tmp_path: Path, kind: str) -> None:
    code = "origin=__file__\nempty=None\nresult=17\n"
    path = tmp_path / "subject.py"
    path.write_text(code, encoding="utf-8")
    target: Target
    if kind == "source":
        target = Source(code, module="subject", filename=str(path))
    elif kind == "file":
        target = File(path, module="subject")
    else:
        target = Project(tmp_path)
    result = analyze(
        target,
        config={
            "engine": "native",
            "resources": {"max_work": 5000, "max_seconds": 10, "max_memory_mb": 0},
        },
        queries=["values", "stats"],
    )
    assert require_object(result.query(ValueQuery("subject", "result")).value)["constants"] == [17]
    assert require_object(result.query(ValueQuery("subject", "origin")).value)["constants"] == [
        str(path)
    ]
    empty = require_object(result.query(ValueQuery("subject", "empty")).value)
    assert empty["constants"] == [None]
    assert empty["tagged_constants"] == [{"type": "none", "value": None}]
    assert result.solver_status == "converged"
    assert result.coverage_status == "open"


def test_native_worker_keeps_bool_and_integer_input_members_distinct() -> None:
    result = analyze(
        Source("result=choice\n", module="subject"),
        config={
            "engine": "native",
            "resources": {"max_work": 5000, "max_seconds": 10, "max_memory_mb": 0},
            "inputs": {"bindings": {"subject.choice": {"constants": [True, 1]}}},
        },
        queries=["values"],
    )
    value = require_object(result.query(ValueQuery("subject", "result")).value)
    assert value["tagged_constants"] == [
        {"type": "bool", "value": True},
        {"type": "int", "value": 1},
    ]
    assert value["top"] is False and value["bottom"] is False


def test_native_worker_preserves_unknown_callback_effect_events() -> None:
    result = analyze(
        Source("box={'value':'before'}\ncallback(box)\nresult=box['value']\n", module="subject"),
        config={
            "engine": "native",
            "resources": {"max_work": 5000, "max_seconds": 10, "max_memory_mb": 0},
            "inputs": {"bindings": {"subject.callback": {"top": True}}},
        },
        queries=["values", "effects"],
    )
    value = require_object(result.query(ValueQuery("subject", "result")).value)
    effects = require_object(result.query("effects").value)
    assert value["top"] is True
    assert require_array(effects["havoc"])
    callbacks = [require_object(row) for row in require_array(effects["calls"])]
    assert any(row["kind"] == "unknown_callback" and row["target"] == "*" for row in callbacks)
    assert result.coverage_status == "open"


def test_native_input_codec_preserves_tuple_aliases_and_opaque_top_marker() -> None:
    shared: list[object] = [1, None]
    marker = object()
    first: dict[str, object] = {"constants": (1, None), "note": shared}
    second: dict[str, object] = {"top": True, "type": marker, "same": shared}
    bindings: dict[str, object] = {"subject.first": first, "subject.second": second}
    first["owner"] = bindings
    copied = validate_input_bindings(bindings)
    first_copy = copied["subject.first"]
    second_copy = copied["subject.second"]
    assert isinstance(first_copy, Mapping) and isinstance(second_copy, Mapping)
    first_fields: Mapping[object, object] = first_copy
    second_fields: Mapping[object, object] = second_copy
    assert first_fields["constants"] == (1, None)
    assert isinstance(first_fields["constants"], tuple)
    owner_copy = first_fields["owner"]
    assert isinstance(owner_copy, Mapping)
    owner_fields: Mapping[object, object] = owner_copy
    assert owner_copy is not copied
    assert owner_fields["subject.first"] is first_copy
    assert owner_fields["subject.second"] is second_copy
    assert first_fields["note"] is second_fields["same"]
    assert first_fields["note"] is not shared
    values, top = alternatives(second_fields)
    assert values == ()
    assert top is second_fields["type"] and type(top) is object
    assert top is not marker
