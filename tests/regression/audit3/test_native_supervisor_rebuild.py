"""Fault injection for the real Native supervisor's shared resource contract."""

from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Sequence
from typing import BinaryIO

import pytest

from pythonstan.analysis.contracts.cost_model import COST_MODEL_DIGEST, COST_MODEL_VERSION
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_object,
    validate_json_object,
)
from pythonstan.analysis.contracts.resources import ResourceStop, RunLedger
from pythonstan.compat import legacy_runner
from pythonstan.compat.native_worker import WorkerHeader
from pythonstan.config import resolve_config
from pythonstan.inputs import Source, SourceSnapshot, snapshot_target


def case() -> tuple[SourceSnapshot, JsonObject]:
    source = Source("result=17\n", module="sample")
    config = resolve_config(
        source, {"engine": "native", "resources": {"max_work": 10000, "max_seconds": 10}}
    )
    return snapshot_target(source, config), config.to_dict()


def packet(kind: str, work: int | None, *, value: int = 17, peak: int | None = 0) -> bytes:
    stats: JsonObject = {} if work is None else {"global_work_used": work}
    document: JsonObject = {
        "kind": kind,
        "result": {
            "solver_status": "converged",
            "coverage_status": "open",
            "values": {"sample": {"result": value}},
            "stats": stats,
        },
        "cost_model": {"version": COST_MODEL_VERSION, "digest": COST_MODEL_DIGEST},
    }
    if peak is not None:
        document["peak_resident_bytes"] = peak
    return (json.dumps(document) + "\n").encode()


class FakeWorker:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0
        self.quota = 0

    def run(
        self,
        command: Sequence[str],
        *,
        input: str,
        text: bool,
        stdout: BinaryIO,
        stderr: BinaryIO,
        timeout: float | None,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        payload = validate_json_object(json.loads(input))
        assert payload["cost_model"] == {
            "version": COST_MODEL_VERSION,
            "digest": COST_MODEL_DIGEST,
        }
        quota = require_object(require_object(payload["config"])["resources"])["max_work"]
        assert type(quota) is int
        self.quota = quota
        assert text and not check and timeout is not None and 0 < timeout <= 10
        if self.mode == "cancel":
            raise KeyboardInterrupt
        if self.mode == "oom":
            raise MemoryError
        stdout.write(
            packet("progress", 7, peak=2 * 1024 * 1024 if self.mode == "timeout_peak" else 0)
        )
        stdout.flush()
        if self.mode in {"timeout", "timeout_peak"}:
            raise subprocess.TimeoutExpired(list(command), timeout)
        if self.mode == "progress_only":
            return subprocess.CompletedProcess(list(command), 0, "", "")
        if self.mode == "nonzero":
            return subprocess.CompletedProcess(list(command), -9, "", "")
        if self.mode == "malformed":
            stdout.write(b"invalid frame\n")
            return subprocess.CompletedProcess(list(command), 0, "", "")
        stdout.write(packet("progress", 7))
        if self.mode == "model":
            wrong = validate_json_object(json.loads(packet("result", 9, value=99)))
            wrong["cost_model"] = {"version": "foreign", "digest": "foreign"}
            stdout.write((json.dumps(wrong) + "\n").encode())
        elif self.mode == "error":
            failed = validate_json_object(json.loads(packet("result", 9)))
            failed["result"] = {"error": "private worker traceback sentinel"}
            stdout.write((json.dumps(failed) + "\n").encode())
        elif self.mode.startswith("bad_"):
            malformed = validate_json_object(json.loads(packet("result", 9, value=99)))
            malformed_result = require_object(malformed["result"])
            if self.mode == "bad_work":
                malformed_result["stats"] = {"global_work_used": True}
            else:
                malformed_result[self.mode.removeprefix("bad_")] = 3
            stdout.write((json.dumps(malformed) + "\n").encode())
        else:
            work = (
                None
                if self.mode == "missing"
                else 4
                if self.mode == "regress"
                else quota + 1
                if self.mode == "overcount"
                else 9
            )
            stdout.write(
                packet(
                    "result",
                    work,
                    peak=2 * 1024 * 1024
                    if self.mode == "peak"
                    else None
                    if self.mode == "no_peak"
                    else 0,
                    value=99 if self.mode in {"regress", "overcount"} else 17,
                )
            )
        if self.mode == "after":
            stdout.write(packet("progress", 9, value=99))
        stdout.flush()
        return subprocess.CompletedProcess(list(command), 0, "", "")


def test_real_worker_consumes_one_shared_grant_and_releases_known_unused_capacity() -> None:
    snapshot, config = case()
    ledger = RunLedger(10000, 10, 0)
    ledger.charge_work(11, "earlier_stage")
    result = legacy_runner.run_legacy(snapshot, config, ledger=ledger)
    assert result["solver_status"] == "converged"
    assert ledger.stop_reason is None
    assert ledger.work.reserved == 0
    work = require_object(ledger.snapshot()["work"])
    categories = require_object(work["by_kind"])
    assert categories["earlier_stage"] == 11
    assert isinstance(categories["native_worker"], int) and categories["native_worker"] > 0
    assert work["used"] == sum(value for value in categories.values() if type(value) is int)


def test_completed_mock_usage_and_duplicate_progress_are_not_double_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, config = case()
    ledger = RunLedger(10000, 10, 0)
    worker = FakeWorker("success")
    monkeypatch.setattr(subprocess, "run", worker.run)
    result = legacy_runner.run_legacy(snapshot, config, ledger=ledger)
    assert result["values"] == {"sample": {"result": 17}}
    assert ledger.work.reserved == 0 and ledger.stop_reason is None
    assert require_object(ledger.work.snapshot()["by_kind"])["native_worker"] == 9
    assert worker.calls == 1 and 9 < worker.quota < 10000


@pytest.mark.parametrize(
    "mode",
    [
        "timeout",
        "progress_only",
        "nonzero",
        "malformed",
        "missing",
        "regress",
        "overcount",
        "model",
        "error",
        "bad_stats",
        "bad_values",
        "bad_obligations",
        "bad_phase_trace",
        "bad_work",
    ],
)
def test_unknown_worker_holds_unobserved_cost_and_completed_prefix(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    snapshot, config = case()
    ledger = RunLedger(10000, 10, 0)
    worker = FakeWorker(mode)
    monkeypatch.setattr(subprocess, "run", worker.run)
    result = legacy_runner.run_legacy(snapshot, config, ledger=ledger)
    assert result["coverage_status"] == "open" and result["solver_status"] != "converged"
    assert result["values"] == {"sample": {"result": 17}}
    stats = require_object(result["stats"])
    assert stats["global_work_observed"] == 7
    assert stats["unobserved_work_reservation"] == worker.quota - 7
    assert stats["work_count_status"] == "unknown_reservation"
    assert ledger.remaining_work > 0
    assert "private worker traceback sentinel" not in repr(result)
    with pytest.raises(ResourceStop):
        ledger.charge_work(1, "forbidden_retry")


@pytest.mark.parametrize("mode,reason", [("cancel", "cancelled"), ("oom", "max_memory_mb")])
def test_cancel_and_oom_return_small_typed_results_and_hold_unknown_work(
    monkeypatch: pytest.MonkeyPatch, mode: str, reason: str
) -> None:
    snapshot, config = case()
    ledger = RunLedger(10000, 10, 0)
    monkeypatch.setattr(subprocess, "run", FakeWorker(mode).run)
    result = legacy_runner.run_legacy(snapshot, config, ledger=ledger)
    assert ledger.stop_reason == reason and result["values"] == {}
    assert result["coverage_status"] == "open"
    assert require_object(result["stats"])["work_count_status"] == "unknown_reservation"


def test_frame_after_terminal_cannot_replace_facts_or_complete_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, config = case()
    ledger = RunLedger(10000, 10, 0)
    monkeypatch.setattr(subprocess, "run", FakeWorker("after").run)
    result = legacy_runner.run_legacy(snapshot, config, ledger=ledger)
    assert ledger.stop_reason == "native_terminal_protocol_failure"
    assert result["values"] == {"sample": {"result": 17}}
    assert require_object(result["stats"])["global_work_observed"] == 9
    assert require_object(result["stats"])["work_count_status"] == "unknown_reservation"


@pytest.mark.parametrize(
    "mode,reason", [("peak", "max_memory_mb"), ("no_peak", "native_memory_observation_unavailable")]
)
def test_complete_usage_is_settled_even_when_memory_evidence_stops_the_request(
    monkeypatch: pytest.MonkeyPatch, mode: str, reason: str
) -> None:
    snapshot, config = case()
    ledger = RunLedger(10000, 10, 1, memory_reader=lambda: 0)
    monkeypatch.setattr(subprocess, "run", FakeWorker(mode).run)
    result = legacy_runner.run_legacy(snapshot, config, ledger=ledger)
    assert ledger.stop_reason == reason and result["coverage_status"] == "open"
    stats = require_object(result["stats"])
    assert stats["global_work_observed"] == 9
    assert stats["unobserved_work_reservation"] == 0 and stats["work_count_status"] == "settled"
    assert ledger.work.reserved == 0


def test_timeout_is_first_cause_even_when_cleanup_later_observes_excess_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, config = case()
    ledger = RunLedger(10000, 10, 1, memory_reader=lambda: 0)
    monkeypatch.setattr(subprocess, "run", FakeWorker("timeout_peak").run)
    result = legacy_runner.run_legacy(snapshot, config, ledger=ledger)
    assert ledger.stop_reason == "max_seconds"
    assert require_object(result["stats"])["global_work_observed"] == 7


def test_absolute_deadline_expires_before_any_worker_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, config = case()
    clock = [0.0]
    ledger = RunLedger(10000, 1, 0, clock=lambda: clock[0])
    clock[0] = 2.0
    worker = FakeWorker("success")
    monkeypatch.setattr(subprocess, "run", worker.run)
    result = legacy_runner.run_legacy(snapshot, config, ledger=ledger)
    assert worker.calls == 0 and ledger.stop_reason == "max_seconds"
    assert result["solver_status"] == "budget_exhausted"


class ObservedSpool(io.BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.read_sizes: list[int] = []

    def readline(self, size: int | None = -1) -> bytes:
        self.read_sizes.append(-1 if size is None else size)
        return super().readline(size)


@pytest.mark.parametrize("quota,reads", [(1, []), (2, [1024])])
def test_wire_reads_cannot_precede_admission(quota: int, reads: list[int]) -> None:
    ledger = RunLedger(10, 10, 0)
    permit = ledger.reserve_work(quota, category="native_wire")
    stream = ObservedSpool(b"x" * 8192)
    assert list(legacy_runner._worker_frames(stream, permit)) == ["native_wire_work_budget"]
    assert stream.read_sizes == reads


def test_oversized_frame_is_stopped_without_reading_beyond_its_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(legacy_runner, "_MAX_WORKER_FRAME_BYTES", 64)
    ledger = RunLedger(10, 10, 0)
    permit = ledger.reserve_work(10, category="native_wire")
    stream = ObservedSpool(b"x" * 8192)
    assert list(legacy_runner._worker_frames(stream, permit)) == ["native_wire_capacity_exceeded"]
    assert stream.tell() == 64 and stream.read_sizes == [64]


def test_worker_rejects_a_foreign_supervisor_model_before_processing_source() -> None:
    _, config = case()
    with pytest.raises(ValueError, match="cost model"):
        WorkerHeader.from_wire({"config": config, "cost_model": {"version": "foreign"}})
