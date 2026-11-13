"""Non-author regional policy conflict, inheritance and capability witnesses."""

from __future__ import annotations

import pytest

from pythonstan import Source
from pythonstan.analysis.composition.hybrid import configure_driver
from pythonstan.analysis.composition.plan import EffectiveRegionPlan, build_plan
from pythonstan.analysis.contracts.json_data import JsonObject
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.config import ConfigurationError, EffectiveConfig, resolve_config
from pythonstan.inputs import SourceSnapshot, snapshot_target


def _plan(
    source: str, rules: list[JsonObject]
) -> tuple[EffectiveConfig, SourceSnapshot, EffectiveRegionPlan]:
    target = Source(source, module="subject")
    config = resolve_config(target, config={"engine": "ai_primary", "regions": rules})
    snapshot = snapshot_target(target, config)
    return config, snapshot, build_plan(snapshot, config)


def _parent() -> JsonObject:
    return {
        "selector": {"module": "subject", "kind": "function", "qualname": "subject.f"},
        "abstract": {"max_const_strings": 2, "solver_options": {"max_traces_per_block": 1}},
    }


def _child() -> JsonObject:
    return {
        "selector": {"module": "subject", "kind": "block", "line_start": 3},
        "abstract": {"int_domain_max_consts": 3, "solver_options": {"widen_delay": 4}},
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_nested_policy_is_order_independent_and_duplicate_child_is_idempotent(
    reverse: bool,
) -> None:
    rules = [_parent(), _child(), _child()]
    if reverse:
        rules.reverse()
    _, _, plan = _plan("def f():\n    a=1\n    return a\nresult=f()\n", rules)
    decision = next(row for row in plan.decisions.values() if row.source.span[0] == 3)
    assert decision.policy.value_policy.max_const_strings == 2
    assert decision.policy.value_policy.max_const_ints == 3
    assert decision.policy.max_paths == 1
    assert decision.policy.integer("widen_delay", 0) == 4


@pytest.mark.parametrize("function_kind", [False, True])
def test_nested_function_selector_breadth_uses_unique_source_nodes(function_kind: bool) -> None:
    selector: JsonObject = {"module": "subject", "qualname": "subject.f.g"}
    if function_kind:
        selector["kind"] = "function"
    _, _, plan = _plan(
        "def f():\n    def g():\n        a=1\n        b=2\n        return a+b\n    return g()\nresult=f()\n",
        [_parent(), {"selector": selector, "abstract": {"int_domain_max_consts": 3}}],
    )
    decision = next(row for row in plan.decisions.values() if row.source.span[0] == 5)
    assert decision.policy.value_policy.max_const_strings == 2
    assert decision.policy.value_policy.max_const_ints == 3
    assert decision.policy.max_paths == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_explicit_priority_wins_independently_of_rule_input_order(reverse: bool) -> None:
    rules: list[JsonObject] = [
        {
            "selector": {"module": "subject", "line_start": 1},
            "priority": 1,
            "abstract": {"context_depth": 0},
        },
        {
            "selector": {"module": "subject", "line_start": 1},
            "priority": 4,
            "abstract": {"context_depth": 1},
        },
    ]
    if reverse:
        rules.reverse()
    _, _, plan = _plan("result=1\n", rules)
    assert next(iter(plan.decisions.values())).policy.context_depth == 1


@pytest.mark.parametrize(
    "solver",
    [{"enable_trace_partitioning": True}, {"infer_merge_points": True}, {"loop_iters": {}}],
)
def test_solver_parent_does_not_grant_unsupported_child_capabilities(solver: JsonObject) -> None:
    with pytest.raises(ConfigurationError, match="regional.*consumer"):
        _plan(
            "result=1\n",
            [
                {
                    "selector": {"module": "subject", "line_start": 1},
                    "abstract": {"solver_options": solver},
                }
            ],
        )


def test_native_region_rejects_ai_only_solver_policy() -> None:
    with pytest.raises(ConfigurationError, match="native.*consumer"):
        _plan(
            "result=1\n",
            [
                {
                    "selector": {"module": "subject", "line_start": 1},
                    "engine": "native",
                    "abstract": {"solver_options": {"max_traces_per_block": 1}},
                }
            ],
        )


def test_mutually_recursive_source_component_rejects_mixed_effective_policies() -> None:
    with pytest.raises(ConfigurationError, match="recursive SCC"):
        _plan(
            "def f(x):\n    return g(x)\ndef g(x):\n    return f(x)\nresult=f(1)\n",
            [
                {
                    "selector": {"module": "subject", "kind": "function", "qualname": "subject.f"},
                    "abstract": {"context_depth": 0},
                }
            ],
        )


def test_source_digest_argument_cannot_substitute_a_foreign_plan_identity() -> None:
    config, snapshot, plan = _plan("result=1\n", [])
    driver = InterproceduralDriver(snapshot.module_map, config.to_dict())
    with pytest.raises(ConfigurationError, match="source identity"):
        configure_driver(driver, plan=plan, config=config, source_digest="unrelated-source")
    assert driver.region_scheduler is None
