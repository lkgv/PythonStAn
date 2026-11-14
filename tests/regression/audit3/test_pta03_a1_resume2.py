"""Actual worker method rules, separate allocation authority and strict input contracts."""

from __future__ import annotations

import pytest

from pythonstan import Source, ValueQuery, analyze
from pythonstan.analysis.contracts.json_data import JsonObject, require_object
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.context import CallStringContext
from pythonstan.analysis.pointer.kcfa.context_selector import (
    ContextPolicy,
    ContextSelector,
    parse_method_context_policies,
)
from pythonstan.config import ConfigurationError, resolve_config

from .test_pta03_transition_resume2 import site


def row(name: str, policy: str) -> JsonObject:
    return {"module": "subject", "qualname": "subject." + name, "policy": policy}


def result_for(source: str, options: JsonObject, symbols: tuple[str, ...]) -> JsonObject:
    result = analyze(
        Source(source, module="subject"),
        config={
            "engine": "native",
            "andersen": options,
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=tuple(ValueQuery("subject", name) for name in symbols) + ("stats",),
    )
    return result.raw_snapshot()


@pytest.mark.parametrize("policy,expected", [("0-cfa", {1, 2}), ("2-cfa", {2})])
def test_actual_rule_changes_only_selected_function_call_precision(
    policy: str, expected: set[int]
) -> None:
    source = (
        "def identity(x):\n    return x\ndef stable(x):\n    return x\n"
        "a=identity(1)\nresult=identity(2)\nu=stable(3)\nunrelated=stable(4)\n"
    )
    raw = result_for(
        source,
        {"context_policy": "2-cfa", "method_context_policies": [row("identity", policy)]},
        ("result", "unrelated"),
    )
    assert raw["solver_status"] == "converged", raw
    values = require_object(require_object(raw["values"])["subject"])
    selected = require_object(values["result"])
    stable = require_object(values["unrelated"])
    assert set(selected["constants"]) == expected and not selected["top"]
    assert stable["constants"] == [4] and not stable["top"]


@pytest.mark.parametrize("depth,expected", [(2, ({1}, {2})), (0, ({1, 2}, {1, 2}))])
def test_actual_worker_heap_policy_is_independent_of_targeted_method_choice(
    depth: int, expected: tuple[set[int], set[int]]
) -> None:
    source = "def make(x):\n    return [x]\na=make(1)\nb=make(2)\nleft=a[0]\nright=b[0]\n"
    raw = result_for(
        source,
        {
            "context_policy": "0-cfa",
            "method_context_policies": [row("make", "2-cfa")],
            "heap_context_policy": "2-cfa",
            "heap_context_depth": depth,
        },
        ("left", "right"),
    )
    assert raw["solver_status"] == "converged", raw
    values = require_object(require_object(raw["values"])["subject"])
    assert set(require_object(values["left"])["constants"]) == expected[0]
    assert set(require_object(values["right"])["constants"]) == expected[1]


def test_explicit_heap_policy_does_not_inherit_larger_method_bound() -> None:
    calls = (site("one()"), site("two()"), site("three()"))
    selected = ContextSelector(
        ContextPolicy.CALL_3, heap_context_policy="2-cfa"
    ).select_alloc_context(CallStringContext(calls, 3), "allocation")
    assert isinstance(selected, CallStringContext)
    assert selected.k == 2 and selected.call_sites == calls[-2:]


def test_input_rule_rows_are_frozen_and_legacy_roundtrip_is_exact() -> None:
    rules = [row("identity", "0-cfa")]
    compiled = parse_method_context_policies(rules)
    config = Config(method_context_policies=compiled)
    selector = ContextSelector(ContextPolicy.CALL_2, method_context_policies=compiled)
    rules[0]["policy"] = "3-cfa"
    assert selector.for_method("subject", "subject.identity").policy is ContextPolicy.INSENSITIVE
    assert Config.from_dict(config.to_dict()) == config
    assert config.to_dict()["method_context_policies"] == [row("identity", "0-cfa")]


@pytest.mark.parametrize(
    "rules",
    [
        [row("identity", "0-cfa"), row("identity", "2-cfa")],
        [{"module": "subject", "qualname": "subject.identity", "policy": "999-cfa"}],
        [{"module": "subject", "qualname": "subject.identity", "policy": "0-cfa", "extra": 1}],
    ],
)
def test_invalid_rows_reject_before_running_source(rules: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        parse_method_context_policies(rules)


@pytest.mark.parametrize("engine", ["ai_primary", "hybrid"])
def test_method_table_is_rejected_for_isolated_region_routes(engine: str) -> None:
    with pytest.raises(ConfigurationError, match="native/native_then_ai"):
        resolve_config(
            Source("result=1", module="subject"),
            {"engine": engine, "andersen": {"method_context_policies": [row("identity", "0-cfa")]}},
        )


@pytest.mark.parametrize("kind", ["instance", "class", "static"])
@pytest.mark.parametrize("policy,expected", [("0-cfa", {1, 2}), ("2-cfa", {2})])
def test_method_rule_is_consumed_at_every_method_call_route(
    kind: str, policy: str, expected: set[int]
) -> None:
    definition = {
        "instance": "    def pick(self,x):\n        return x\n",
        "class": "    @classmethod\n    def pick(cls,x):\n        return x\n",
        "static": "    @staticmethod\n    def pick(x):\n        return x\n",
    }[kind]
    receiver = "receiver=C()\n" if kind == "instance" else "receiver=C\n"
    source = "class C:\n" + definition + receiver + "a=receiver.pick(1)\nresult=receiver.pick(2)\n"
    raw = result_for(
        source,
        {"context_policy": "2-cfa", "method_context_policies": [row("C.pick", policy)]},
        ("result",),
    )
    assert raw["solver_status"] == "converged", raw
    value = require_object(require_object(require_object(raw["values"])["subject"])["result"])
    assert set(value["constants"]) == expected and not value["top"], raw


def test_unmatched_target_has_no_user_call_or_heap_publications() -> None:
    source = "def identity(x):\n    return x\nresult=identity(1)\n"
    raw = result_for(source, {"method_context_policies": [row("missing", "0-cfa")]}, ("result",))
    assert raw["solver_status"] != "converged"
    assert not raw["calls"] and not raw["values"]
    assert "Unmatched source method policy" in str(raw)
