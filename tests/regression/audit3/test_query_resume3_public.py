"""Preregistered public query controls using actual shared-session producers."""

from __future__ import annotations

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import JsonObject, require_object


def options(*, work: int = 10000, debug: bool = False) -> JsonObject:
    return {
        "engine": "ai_primary",
        "resources": {"max_work": work, "max_seconds": 2, "max_memory_mb": 512},
        "observation": {"enabled": debug},
    }


def test_single_only_real_function_answer_and_no_unrequested_bulk_cover() -> None:
    query = ValueQuery("m", "f", "function_exit")
    result = analyze(
        Source("def f():\n    return 17\nx=f()\n", module="m"), config=options(), queries=[query]
    )
    answer = result.query(query)
    assert answer.status == "answered" and answer.coverage_status == "closed_under_profile"
    assert require_object(answer.value)["constants"] == [17]
    bulk = result.query("returns")
    assert bulk.status == "not_requested" and bulk.coverage_status == "open"
    assert require_object(result.raw_snapshot()["stats"])["units"] == 2


def test_complete_raise_only_is_not_lazy_or_unknown() -> None:
    query = ValueQuery("m", "f", "function_exit")
    result = analyze(
        Source("def f():\n    raise ValueError()\nf()\n", module="m"),
        config=options(),
        queries=[query, "returns", "outcomes"],
    )
    answer = result.query(query)
    payload = require_object(answer.value)
    assert answer.status == "answered" and answer.coverage_status == "closed_under_profile"
    assert payload["bottom"] and not payload["normal_exit"]
    assert payload["exceptions"] == ["ValueError"]


@pytest.mark.parametrize(
    "source, reason",
    [
        ("def f():\n    return 17\nx=0\n", "unreachable"),
        ("def f():\n    yield 17\nx=f()\n", "lazy"),
    ],
)
def test_unexecuted_body_has_no_function_return_answer(source: str, reason: str) -> None:
    query = ValueQuery("m", "f", "function_exit")
    result = analyze(
        Source(source, module="m"), config=options(), queries=[query, "returns", "outcomes"]
    )
    answer = result.query(query)
    assert answer.status == "unavailable" and answer.coverage_status == "open"
    metadata = require_object(
        require_object(result.raw_snapshot()["provenance"])["procedure_queries"]
    )
    rows = metadata["queries"]
    assert isinstance(rows, list)
    row = next(
        require_object(item) for item in rows if require_object(item).get("qualname") == "m.f"
    )
    assert row["scope_status"] == reason


def test_unknown_scope_cannot_inherit_a_closed_bulk_inventory() -> None:
    result = analyze(Source("x=17\n", module="m"), config=options(), queries=["returns"])
    bulk = result.query("returns")
    assert bulk.status == "answered" and bulk.value == {}
    unknown = result.query(ValueQuery("m", "missing", "function_exit"))
    assert unknown.status == "unavailable" and unknown.coverage_status == "open"


def test_cap_zero_does_not_cover_an_empty_function_scope() -> None:
    query = ValueQuery("m", "f", "function_exit")
    result = analyze(
        Source("def f():\n    return 17\nx=f()\n", module="m"),
        config=options(work=0),
        queries=[query, "returns"],
    )
    answer = result.query(query)
    assert answer.status == "unavailable" and answer.coverage_status == "open"


def test_debug_enablement_does_not_produce_semantic_exit_facts() -> None:
    query = ValueQuery("m", "f", "function_exit")
    source = Source("def f():\n    return 17\nx=f()\n", module="m")
    answers = [
        analyze(source, config=options(debug=debug), queries=[query]).query(query).to_dict()
        for debug in (False, True)
    ]
    assert answers[0] == answers[1]


def test_open_bulk_outcomes_preserve_actual_prefix_payload() -> None:
    result = analyze(
        Source("result=list(b'ab')\n", module="m"),
        config=options(),
        queries=[ValueQuery("m", "result"), "returns", "outcomes"],
    )
    answer = result.query("outcomes")
    assert answer.status == "answered" and answer.coverage_status == "open"
    payload = require_object(answer.value)
    rows = payload["m"]
    assert isinstance(rows, list)
    assert {require_object(row)["kind"] for row in rows} >= {"normal", "raise"}


def test_lazy_single_unavailable_with_genuine_module_bulk_outcomes_answer() -> None:
    query = ValueQuery("m", "f", "function_exit")
    result = analyze(
        Source("def f():\n    yield 17\nresult=f()\n", module="m"),
        config=options(),
        queries=[query, "returns", "outcomes"],
    )
    single = result.query(query)
    assert single.status == "unavailable" and single.coverage_status == "open"
    outcomes = result.query("outcomes")
    assert outcomes.status == "answered" and outcomes.coverage_status == "open"
    payload = require_object(outcomes.value)
    assert payload["m"] and payload["m.f"] == []
    returns = result.query("returns")
    assert returns.status == "unavailable" and returns.coverage_status == "open"
