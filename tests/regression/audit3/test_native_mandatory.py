"""Mandatory Native boundary regressions, with AI and concrete controls."""

from __future__ import annotations

import pytest

from pythonstan import Source
from pythonstan.analysis.composition.hybrid import configure_driver
from pythonstan.analysis.composition.plan import build_plan
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.config import resolve_config
from pythonstan.inputs import snapshot_target

from .domain_oracles import JsonValue, concrete_result, public_answer, require_members


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("target", ["heap", "cell", "nested"])
def test_assignment_failure_keeps_completed_prefix(target: str, native: bool) -> None:
    """NCOMP-01: the second target failure cannot roll back the first."""
    assignments = {
        "heap": "(a[0],b[0])=(1,2)",
        "cell": "(x,b[0])=(1,2)",
        "nested": "((a[0],b[0]),x)=((1,2),3)",
    }
    read = "x" if target == "cell" else "a[0]"
    source = (
        "a=[0]\nx=0\nb=[]\ntry:\n    "
        + assignments[target]
        + "\nexcept IndexError:\n    result="
        + read
        + "\n"
    )
    assert concrete_result(source) == 1
    require_members(public_answer(source, native_line=5 if native else None), [1])


@pytest.mark.parametrize("native", [False, True])
def test_assignment_success_positive_control(native: bool) -> None:
    source = "a=[0]\nb=[0]\n(a[0],b[0])=(1,2)\nresult=a[0]\n"
    assert concrete_result(source) == 1
    require_members(public_answer(source, native_line=3 if native else None), [1])


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("size", [2, 3])
def test_repeated_dictionary_allocation_retains_old_alias(native: bool, size: int) -> None:
    """NCOMP-02: changing keys at one site must cover earlier live objects."""
    source = (
        f"saved=[]\nfor i in range({size}):\n    current={{i:'present'}}\n"
        "    saved.append(current)\ntry:\n    result=saved[0][0]\n"
        "except KeyError:\n    result='missing'\n"
    )
    assert concrete_result(source) == "present"
    require_members(public_answer(source, native_line=3 if native else None), ["present"])


@pytest.mark.parametrize("native", [False, True])
def test_fixed_key_allocation_positive_control(native: bool) -> None:
    source = (
        "saved=[]\nfor i in range(2):\n    current={'v':i}\n    saved.append(current)\n"
        "result=saved[0]['v']\n"
    )
    assert concrete_result(source) == 0
    require_members(public_answer(source, native_line=3 if native else None), [0])


def execute_control(source: str, config: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], int]:
    """Observe actual result and call-unit consumers, not accepted config text."""
    target = Source(source, module="subject")
    resolved = resolve_config(target, config=config)
    snapshot = snapshot_target(target, resolved)
    plan = build_plan(snapshot, resolved)
    driver = InterproceduralDriver(snapshot.module_map, options=resolved.to_dict())
    configure_driver(driver, plan=plan, source_digest=snapshot.source_digest, config=resolved)
    payload = driver.run(snapshot.scenarios).to_dict()
    return payload["values"]["subject"]["result"], len(driver.units)


def test_regional_context_policy_has_real_callsite_consumer() -> None:
    """NCOMP-03: a requested zero context must merge these two calls."""
    source = "def f(x):\n    return x\na=f(1)\nresult=f(2)\n"
    global_value, global_units = execute_control(
        source, {"engine": "ai_primary", "abstract": {"context_depth": 0}}
    )
    regional_value, regional_units = execute_control(
        source,
        {
            "engine": "ai_primary",
            "regions": [
                {
                    "selector": {
                        "module": "subject",
                        "kind": "block",
                        "line_start": 3,
                        "line_end": 4,
                    },
                    "engine": "ai_primary",
                    "abstract": {"context_depth": 0},
                }
            ],
        },
    )
    require_members(global_value, [1, 2])
    require_members(regional_value, [1, 2])
    assert regional_units == global_units


def test_regional_relation_alias_matches_canonical_policy() -> None:
    source = (
        "def plain(payload):\n    return payload\ndef wrapped(payload):\n"
        "    return {'v':payload}\ndef choose(key,payload):\n"
        "    table={'alpha':plain,'beta':wrapped}\n    return table[key](payload)\n"
        "result=choose(key,'ok')\n"
    )
    values: list[dict[str, JsonValue]] = []
    for option in ("relations", "enable_value_partition"):
        value, _ = execute_control(
            source,
            {
                "engine": "ai_primary",
                "inputs": {"bindings": {"subject.key": {"type": "str", "top": True}}},
                "regions": [
                    {
                        "selector": {
                            "module": "subject",
                            "qualname": "subject.choose",
                            "kind": "block",
                            "line_start": 7,
                        },
                        "engine": "ai_primary",
                        "abstract": {option: False},
                    }
                ],
            },
        )
        values.append(value)
    # Relation identity is semantic data: do not delete it for alpha comparison.
    assert values[0]["relations"] == values[1]["relations"]


def test_regional_nested_trace_budget_changes_actual_paths() -> None:
    """NCOMP-03: compare a real correlation consumer, not cached attributes."""
    source = (
        "if choose:\n    x='A'\n    marker=1\nelse:\n    x='B'\n    marker=2\n"
        "if marker==1:\n    result=x\nelse:\n    result='X'\n"
    )
    common: dict[str, JsonValue] = {
        "engine": "ai_primary",
        "inputs": {"bindings": {"subject.choose": {"type": "bool", "top": True}}},
    }
    global_value, _ = execute_control(
        source, {**common, "abstract": {"solver_options": {"max_traces_per_block": 1}}}
    )
    regional_value, _ = execute_control(
        source,
        {
            **common,
            "regions": [
                {
                    "selector": {
                        "module": "subject",
                        "kind": "block",
                        "line_start": 1,
                        "line_end": 10,
                    },
                    "engine": "ai_primary",
                    "abstract": {"solver_options": {"max_traces_per_block": 1}},
                }
            ],
        },
    )
    require_members(global_value, ["A", "B", "X"])
    assert regional_value["constants"] == global_value["constants"]
