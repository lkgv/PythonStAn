"""W0 oracle integrity; product semantic acceptance remains a separate red gate."""

from __future__ import annotations

import ast
import copy
import hashlib
from pathlib import Path

import pytest

from tests.regression.audit3 import semantic_oracle as oracle

FROZEN_MANIFEST_SHA256 = "16771ac12bb1267a032fb004c98b47680ee938fcf95a5f639eac5549f159e66b"
MANIFEST = oracle.load_manifest()
CASES = [oracle.object_value(case) for case in oracle.array_value(MANIFEST["cases"])]


def test_frozen_manifest_and_retained_inputs_have_not_changed() -> None:
    assert hashlib.sha256(oracle.MANIFEST.read_bytes()).hexdigest() == FROZEN_MANIFEST_SHA256
    for relative, digest in oracle.object_value(MANIFEST["retained_files"]).items():
        assert hashlib.sha256((oracle.ROOT / relative).read_bytes()).hexdigest() == digest
    ids = [
        oracle.object_value(item)["id"] for item in oracle.array_value(MANIFEST["retained_A01_A18"])
    ]
    assert ids == [f"A{number:02d}" for number in range(1, 19)]
    assert len({case["id"] for case in CASES}) == len(CASES) == 52
    assert {f"AS-{number:02d}" for number in range(1, 9)} <= {case["finding"] for case in CASES}


def test_acceptance_runner_rejects_a_weakened_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = tmp_path / "semantic_manifest.json"
    changed.write_text(oracle.MANIFEST.read_text() + "\n")
    monkeypatch.setattr(oracle, "MANIFEST", changed)
    with pytest.raises(ValueError, match="denominator"):
        oracle.load_manifest()


@pytest.mark.parametrize("case", CASES, ids=[oracle.string_value(case["id"]) for case in CASES])
def test_frozen_independent_concrete_expectation(case: dict[str, oracle.Json]) -> None:
    source = oracle.string_value(case["source"])
    assert hashlib.sha256(source.encode()).hexdigest() == case["source_sha256"]
    result = oracle.check_concrete(case)
    assert result["passed"], result


@pytest.mark.parametrize("status", ["unsupported", "unavailable", "partial", "truncated"])
def test_mandatory_acceptance_rejects_missing_query(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    case = copy.deepcopy(CASES[0])

    def missing_worker(
        code: str, request: dict[str, oracle.Json], timeout: int = 15
    ) -> dict[str, oracle.Json]:
        return {
            "process_status": "completed",
            "solver_status": "converged",
            "coverage_status": "closed_under_profile",
            "answer": {"status": status},
        }

    monkeypatch.setattr(oracle, "run_worker", missing_worker)
    result = oracle.check_analyzer(case, oracle.object_value(MANIFEST["resources"]))
    assert result["passed"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"top": True},
        {"top_sorts": ["str"]},
        {"bottom": True},
        {"string_patterns": {"unknown": True}},
        {"tagged_constants": []},
    ],
)
def test_top_or_missing_value_cannot_close_mandatory(
    payload: dict[str, oracle.Json],
) -> None:
    assert oracle.exact_scalar(payload, ["sk"]) is False


def test_booleans_and_integers_do_not_share_exact_gold() -> None:
    value: dict[str, oracle.Json] = {"tagged_constants": [{"type": "bool", "value": True}]}
    assert oracle.exact_scalar(value, [True])
    assert not oracle.exact_scalar(value, [1])


def test_context_zero_contract_requires_finite_useful_inclusion() -> None:
    assert oracle.finite_inclusion({"integer_interval": {"lower": 0, "upper": 10}}, [10])
    assert not oracle.finite_inclusion({"integer_interval": {"lower": None, "upper": None}}, [10])
    assert not oracle.finite_inclusion({"top_sorts": ["int"]}, [10])


def test_wrong_two_handler_pairs_cannot_pass_l1_quality() -> None:
    wrong: dict[str, oracle.Json] = {
        "relations": [
            {"key": "'read'", "handler": "subject.box", "key_type": "str", "partitioned": True},
            {
                "key": "'write'",
                "handler": "subject.identity",
                "key_type": "str",
                "partitioned": True,
            },
        ]
    }
    assert not oracle.exact_handler_relations(wrong)
    correct = copy.deepcopy(wrong)
    rows = oracle.array_value(correct["relations"])
    oracle.object_value(rows[0])["handler"] = "subject.identity"
    oracle.object_value(rows[1])["handler"] = "subject.box"
    assert oracle.exact_handler_relations(correct)


def test_empty_function_exit_scope_cannot_pass_l2_acceptance() -> None:
    row: dict[str, oracle.Json] = {
        "function_exit": {},
        "returns": {
            "value": {
                "subject.identity": {},
                "subject.box": {},
                "subject.dispatch": {},
            }
        },
    }
    assert not oracle.function_scopes_are_useful(row, ["identity", "box", "dispatch"])


def test_open_status_without_propagated_residual_cannot_pass() -> None:
    row: dict[str, oracle.Json] = {
        "process_status": "completed",
        "coverage_status": "open",
        "answer": {"status": "answered", "value": {"top": True}},
    }
    assert not oracle.open_boundary_is_propagated(row, [[97, 98]])


def test_all_legacy_regression_selectors_resolve() -> None:
    records = oracle.array_value(MANIFEST["retained_A01_A18"])
    gold = oracle.object_value(
        oracle.decode((oracle.ROOT / "docs/refactor/query-gold.json").read_text())
    )
    fixtures = {
        oracle.object_value(item)["id"]
        for item in oracle.array_value(gold["mandatory_supported_fixtures"])
    }
    symbols = {
        node.name
        for test_path in (oracle.ROOT / "tests/refactor").rglob("test_*.py")
        for node in ast.walk(ast.parse(test_path.read_text()))
        if isinstance(node, ast.FunctionDef)
    }
    for item in records:
        record = oracle.object_value(item)
        tests = oracle.array_value(record["tests"])
        assert tests, record["id"]
        for test in tests:
            selector = oracle.string_value(test).split(" ", 1)[0]
            if selector in fixtures or selector in symbols:
                continue
            path = Path(selector.split("::", 1)[0])
            # Existing ledger records a few bare filenames; resolve these to a
            # unique production-owned test rather than inventing a new selector.
            matches = [path] if path.is_file() else list((oracle.ROOT / "tests").rglob(path.name))
            assert len(matches) == 1 and matches[0].is_file(), (record["id"], test, matches)
