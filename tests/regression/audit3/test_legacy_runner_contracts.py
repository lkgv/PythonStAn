"""Disposable-worker wire, prefix retention and timeout cost contracts."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

import pytest

from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_array,
    require_object,
    validate_json_object,
)
from pythonstan.compat import legacy_runner
from pythonstan.config import EffectiveConfig, resolve_config
from pythonstan.inputs import Source, SourceSnapshot, snapshot_target


def worker_case() -> tuple[SourceSnapshot, EffectiveConfig]:
    target = Source("result=17", filename="sample.py", module="sample")
    config = resolve_config(
        target,
        {"engine": "native", "resources": {"max_work": 100, "max_seconds": 1}},
    )
    return snapshot_target(target, config), config


def frame(kind: str, result: JsonObject) -> str:
    return json.dumps({"kind": kind, "result": result}) + "\n"


def test_latest_frame_is_typed_and_rejects_non_json_numbers() -> None:
    first: JsonObject = {"values": {"sample": {"result": 1}}}
    final: JsonObject = {"values": {"sample": {"result": 17}}}
    wire = "diagnostic line\n" + frame("progress", first) + frame("result", final)
    assert legacy_runner._latest_frame(wire.encode()) == final
    assert legacy_runner._latest_frame('{"kind":"result","result":{"x":NaN}}') is None
    assert legacy_runner._latest_frame(None) is None


def test_timeout_keeps_completed_prefix_and_reserves_unobserved_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, config = worker_case()
    partial: JsonObject = {
        "solver_status": "converged",
        "coverage_status": "closed_under_profile",
        "values": {"sample": {"result": 17}},
        "calls": [["sample", "sample.f", "point"]],
        "obligations": [{"kind": "existing"}],
        "stats": {"global_work_used": 7},
        "phase_trace": [{"phase": "prefix"}],
    }
    wire = frame("progress", partial).encode()

    def expire(
        command: Sequence[str],
        *,
        input: str,
        text: bool,
        capture_output: bool,
        timeout: float | None,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(command), timeout or 0, output=wire)

    monkeypatch.setattr(subprocess, "run", expire)
    result = legacy_runner.run_legacy(snapshot, config)
    assert result["solver_status"] == "budget_exhausted"
    assert result["coverage_status"] == "open"
    assert result["values"] == partial["values"]
    assert result["calls"] == partial["calls"]
    assert require_array(result["obligations"])[0] == {"kind": "existing"}
    stats = require_object(result["stats"])
    assert stats["global_work_observed"] == 7
    assert stats["unobserved_work_reservation"] == 93
    assert stats["total_work"] == 100
    assert stats["work_count_status"] == "conservative_reservation"
    phases = require_array(result["phase_trace"])
    assert phases[0] == {"phase": "prefix"}
    assert require_object(phases[-1])["partial_export"] is True


def test_timeout_without_frame_keeps_full_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, config = worker_case()

    def expire(
        command: Sequence[str],
        *,
        input: str,
        text: bool,
        capture_output: bool,
        timeout: float | None,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(list(command), timeout or 0, output=b"incomplete frame")

    monkeypatch.setattr(subprocess, "run", expire)
    result = legacy_runner.run_legacy(snapshot, config.to_dict())
    assert result["coverage_status"] == "open"
    assert result["values"] == {}
    stats = require_object(result["stats"])
    assert stats["global_work_observed"] == 0
    assert stats["unobserved_work_reservation"] == stats["total_work"] == 100


def test_success_serializes_all_source_and_scenario_identity_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, config = worker_case()
    captured: list[JsonObject] = []
    result_payload: JsonObject = {"values": {"sample": {"result": 17}}, "stats": {}}

    def complete(
        command: Sequence[str],
        *,
        input: str,
        text: bool,
        capture_output: bool,
        timeout: float | None,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        decoded: object = json.loads(input)
        captured.append(validate_json_object(decoded))
        return subprocess.CompletedProcess(list(command), 0, frame("result", result_payload), "")

    monkeypatch.setattr(subprocess, "run", complete)
    result = legacy_runner.run_legacy(snapshot, config)
    assert result["values"] == result_payload["values"]
    module = require_object(require_array(captured[0]["modules"])[0])
    expected = snapshot.modules[0]
    assert module == {
        "module": expected.module,
        "filename": expected.filename,
        "source": expected.source,
        "digest": expected.digest,
        "is_package": expected.is_package,
        "original_bytes_digest": expected.original_bytes_digest,
        "encoding": expected.encoding,
        "import_root_digest": expected.import_root_digest,
    }
    scenario = require_object(require_array(captured[0]["scenarios"])[0])
    assert scenario["root_module"] == snapshot.scenarios[0].root_module
    assert scenario["scenario_id"] == snapshot.scenarios[0].scenario_id
    assert scenario["arguments"] == []


@pytest.mark.parametrize(
    ("exit_code", "output", "error", "message"),
    [
        (1, "", "worker failed", "Legacy process failed"),
        (0, "incomplete frame", "", "Legacy process emitted invalid JSON"),
        (0, frame("result", {"error": "worker exception"}), "", "worker exception"),
    ],
)
def test_process_and_protocol_failures_cannot_return_closed_results(
    monkeypatch: pytest.MonkeyPatch, exit_code: int, output: str, error: str, message: str
) -> None:
    snapshot, config = worker_case()

    def complete(
        command: Sequence[str],
        *,
        input: str,
        text: bool,
        capture_output: bool,
        timeout: float | None,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(command), exit_code, output, error)

    monkeypatch.setattr(subprocess, "run", complete)
    with pytest.raises(RuntimeError, match=message):
        legacy_runner.run_legacy(snapshot, config)
