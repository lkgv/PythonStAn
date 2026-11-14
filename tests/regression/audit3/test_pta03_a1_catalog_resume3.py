"""Captured method targets are checked once before any Native child dispatch."""

from __future__ import annotations

import ast
import subprocess
from dataclasses import replace

import pytest

from pythonstan.analysis.contracts.json_data import JsonObject, require_object
from pythonstan.analysis.contracts.resources import RunLedger
from pythonstan.compat import legacy_runner
from pythonstan.compat.native_worker import WorkerHeader, WorkerRequest
from pythonstan.config import resolve_config
from pythonstan.inputs import Source, SourceSnapshot, snapshot_target


def case(name: str = "missing") -> tuple[SourceSnapshot, JsonObject]:
    source = Source("def identity(x):\n    return x\nresult=identity(17)\n", module="subject")
    config = resolve_config(
        source,
        {
            "engine": "native",
            "andersen": {
                "method_context_policies": [
                    {"module": "subject", "qualname": "subject." + name, "policy": "0-cfa"}
                ]
            },
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
    )
    return snapshot_target(source, config), config.to_dict()


@pytest.mark.parametrize("cache", ["captured", "missing", "stale"])
def test_unknown_named_target_is_charged_and_rejected_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, cache: str
) -> None:
    snapshot, config = case()
    if cache == "missing":
        snapshot = snapshot.for_rerun()
    elif cache == "stale":
        snapshot = replace(
            snapshot,
            parsed_trees={"subject": ("stale digest", ast.parse("def missing(): pass"))},
        )
    calls: list[object] = []

    def refuse(*args: object, **kwargs: object) -> None:
        calls.append(args)
        raise AssertionError("Unknown method policy must be rejected before child dispatch")

    monkeypatch.setattr(subprocess, "run", refuse)
    ledger = RunLedger(10000, 2, 0)
    raw = legacy_runner.run_legacy(snapshot, config, ledger=ledger)
    assert not calls and raw["solver_status"] == "failed"
    assert not raw["values"] and not raw["calls"]
    assert "Unmatched source method policy" in str(raw)
    assert ledger.work.by_kind["native_method_inventory"] > 0
    assert not require_object(raw["stats"])["global_work_used"]
    assert not ledger.work.reserved
    if cache != "captured":
        assert ledger.work.by_kind["native_method_inventory_parse"] > 0


def test_worker_rejects_catalog_with_a_different_captured_source_digest() -> None:
    snapshot, config = case("identity")
    source = snapshot.modules[0]
    payload: JsonObject = {
        "config": config,
        "modules": [legacy_runner._source_module_wire(source)],
        "scenarios": [],
        "method_inventory": [
            {"module": "subject", "digest": "different", "qualnames": ["subject.identity"]}
        ],
    }
    with pytest.raises(ValueError, match="source identity"):
        WorkerRequest.from_header(WorkerHeader.from_wire(payload))


def test_valid_target_survives_a_missing_parser_cache() -> None:
    snapshot, config = case("identity")
    ledger = RunLedger(10000, 2, 0)
    raw = legacy_runner.run_legacy(snapshot.for_rerun(), config, ledger=ledger)
    assert raw["solver_status"] == "converged", raw
    value = require_object(require_object(require_object(raw["values"])["subject"])["result"])
    assert value["constants"] == [17] and not value["top"]
    assert ledger.work.by_kind["native_method_inventory_parse"] > 0
