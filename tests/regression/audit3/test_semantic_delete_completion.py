"""Deletion completion witnesses for binding state and class name fallbacks."""

from __future__ import annotations

import pytest

from pythonstan import Source, ValueQuery, analyze

from .domain_oracles import concrete_result, wire_contains


@pytest.mark.parametrize("engine", ["ai_primary", "hybrid"])
@pytest.mark.parametrize(
    "source",
    [
        "x='global'\nclass C:\n    x='local'\n    del x\n    result=x\nresult=C.result\n",
        "class C:\n    len='local'\n    del len\n    result=len(())\nresult=C.result\n",
        "x='global'\nclass C:\n    try:\n        del x\n    except NameError:\n        result=x\nresult=C.result\n",
        "x=1\ntry:\n    del [x, abs]\nfinally:\n    try:\n        result=x\n    except NameError:\n        result='deleted'\n",
    ],
)
def test_delete_scope_and_cleanup_match_python(source: str, engine: str) -> None:
    # The final case deliberately escapes NameError after executing finally.
    oracle_source = (
        "try:\n"
        + "\n".join("    " + line for line in source.splitlines())
        + "\nexcept NameError:\n    pass\n"
    )
    expected = concrete_result(oracle_source)
    result = analyze(
        Source(oracle_source, module="subject"),
        config={"engine": engine},
        queries=(ValueQuery("subject", "result"),),
    )
    answer = result.query(ValueQuery("subject", "result"))
    assert answer.status == "answered"
    assert answer.coverage_status == "closed_under_profile"
    assert wire_contains(answer.value, expected)
    assert not answer.value["top"] and not answer.value["top_sorts"]


@pytest.mark.parametrize("engine", ["ai_primary", "hybrid"])
def test_maybe_bound_delete_preserves_both_completions(engine: str) -> None:
    source = "if flag:\n    x=1\ntry:\n    del x\n    result='deleted'\nexcept NameError:\n    result='missing'\n"
    result = analyze(
        Source(source, module="subject"),
        config={
            "engine": engine,
            "inputs": {"bindings": {"subject.flag": {"type": "bool", "top": True}}},
        },
        queries=(ValueQuery("subject", "result"),),
    )
    answer = result.query(ValueQuery("subject", "result"))
    assert answer.status == "answered"
    assert answer.coverage_status == "closed_under_profile"
    for flag in (False, True):
        assert wire_contains(answer.value, concrete_result(source, {"flag": flag}))
