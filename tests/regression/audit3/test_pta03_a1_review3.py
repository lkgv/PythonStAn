"""Independent actual-source method policy and heap authority controls."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pythonstan import Project, Source, ValueQuery, analyze
from pythonstan.analysis.contracts.identity import SourceModule
from pythonstan.analysis.contracts.json_data import JsonObject, require_object
from pythonstan.analysis.pointer.kcfa.context_selector import parse_method_context_policies
from pythonstan.compat.native_services import CapturedMethodInventory
from pythonstan.config import ConfigurationError, resolve_config


def rule(name: str, policy: str, module: str = "subject") -> JsonObject:
    return {"module": module, "qualname": module + "." + name, "policy": policy}


def concrete(source: str, symbols: tuple[str, ...]) -> dict[str, object]:
    code = (
        "import json\n"
        + source
        + "\nprint(json.dumps({k:globals()[k] for k in "
        + repr(symbols)
        + "}))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    return json.loads(result.stdout)


def public(target: Source | Project, options: JsonObject, symbols: tuple[str, ...]) -> JsonObject:
    result = analyze(
        target,
        config={
            "engine": "native",
            "andersen": {"enable_synthetic_method_contexts": False, **options},
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=tuple(ValueQuery("subject", name) for name in symbols) + ("stats",),
    )
    return result.raw_snapshot()


def constants(raw: JsonObject, name: str) -> set[object]:
    row = require_object(require_object(require_object(raw["values"])["subject"])[name])
    assert not row["top"], row
    return set(row["constants"])


@pytest.mark.parametrize("policy,expected", [("0-cfa", {5, 7}), ("2-cfa", {7})])
def test_nested_returned_function_target_uses_its_exact_source_fqn(
    policy: str, expected: set[int]
) -> None:
    source = (
        "def make():\n    def identity(x):\n        return x\n    return identity\n"
        "target=make()\na=target(5)\nresult=target(7)\n"
    )
    assert concrete(source, ("result",)) == {"result": 7}
    raw = public(
        Source(source, module="subject"),
        {"context_policy": "2-cfa", "method_context_policies": [rule("make.identity", policy)]},
        ("result",),
    )
    assert raw["solver_status"] == "converged", raw
    assert constants(raw, "result") == expected


def test_inherited_method_target_does_not_change_same_named_plain_function() -> None:
    source = (
        "def pick(x):\n    return x\nclass Base:\n    def pick(self,x):\n        return x\n"
        "class Child(Base):\n    pass\nreceiver=Child()\na=receiver.pick(5)\n"
        "result=receiver.pick(7)\nb=pick(9)\nunrelated=pick(11)\n"
    )
    assert concrete(source, ("result", "unrelated")) == {"result": 7, "unrelated": 11}
    raw = public(
        Source(source, module="subject"),
        {"context_policy": "2-cfa", "method_context_policies": [rule("Base.pick", "0-cfa")]},
        ("result", "unrelated"),
    )
    assert raw["solver_status"] == "converged", raw
    assert constants(raw, "result") == {5, 7}
    assert constants(raw, "unrelated") == {11}


@pytest.mark.parametrize("policy,expected", [("0-cfa", {5, 7}), ("2-cfa", {7})])
def test_captured_import_target_is_valid_before_ir_inventory_is_complete(
    tmp_path: Path, policy: str, expected: set[int]
) -> None:
    (tmp_path / "helper.py").write_text("def identity(x):\n    return x\n")
    (tmp_path / "subject.py").write_text(
        "from helper import identity\na=identity(5)\nresult=identity(7)\n"
    )
    raw = public(
        Project(tmp_path, entries=("subject:",)),
        {
            "context_policy": "2-cfa",
            "method_context_policies": [rule("identity", policy, "helper")],
        },
        ("result",),
    )
    assert raw["solver_status"] == "converged", raw
    assert constants(raw, "result") == expected


def test_declared_but_uncalled_method_rule_is_not_falsely_unmatched() -> None:
    source = "def dormant(x):\n    return x\nresult=11\n"
    assert concrete(source, ("result",)) == {"result": 11}
    raw = public(
        Source(source, module="subject"),
        {"method_context_policies": [rule("dormant", "0-cfa")]},
        ("result",),
    )
    assert raw["solver_status"] == "converged", raw
    assert constants(raw, "result") == {11}
    assert not any("dormant" in str(call) for call in raw["calls"])


def test_public_native_result_retains_actual_matched_method_decisions() -> None:
    source = "def identity(x):\n    return x\na=identity(5)\nresult=identity(7)\n"
    raw = public(
        Source(source, module="subject"),
        {"context_policy": "2-cfa", "method_context_policies": [rule("identity", "0-cfa")]},
        ("result",),
    )
    assert raw["solver_status"] == "converged", raw
    assert constants(raw, "result") == {5, 7}
    stats = require_object(raw["stats"])
    assert "method_context_policy" in stats, stats
    policy = require_object(stats["method_context_policy"])
    assert policy["rules"] == [rule("identity", "0-cfa")]
    decisions = require_object(policy["decisions"])
    assert decisions["subject:subject.identity:0-cfa"] >= 2


@pytest.mark.parametrize("depth,separate", [(0, False), (2, True)])
def test_real_allocation_ids_follow_heap_policy_not_method_table(
    depth: int, separate: bool
) -> None:
    source = "def make(x):\n    return [x]\na=make(5)\nb=make(7)\nleft=a[0]\nright=b[0]\n"
    assert concrete(source, ("left", "right")) == {"left": 5, "right": 7}
    raw = public(
        Source(source, module="subject"),
        {
            "context_policy": "0-cfa",
            "method_context_policies": [rule("make", "2-cfa")],
            "heap_context_policy": "2-cfa",
            "heap_context_depth": depth,
        },
        ("a", "b", "left", "right"),
    )
    assert raw["solver_status"] == "converged", raw
    values = require_object(require_object(raw["values"])["subject"])
    a, b = require_object(values["a"]), require_object(values["b"])
    assert bool(set(a["objects"]).isdisjoint(set(b["objects"]))) is separate
    assert constants(raw, "left") == ({5} if separate else {5, 7})
    assert constants(raw, "right") == ({7} if separate else {5, 7})


def test_method_zero_does_not_invent_two_heap_callers() -> None:
    source = "def make(x):\n    return [x]\na=make(5)\nb=make(7)\nleft=a[0]\nright=b[0]\n"
    raw = public(
        Source(source, module="subject"),
        {
            "context_policy": "2-cfa",
            "method_context_policies": [rule("make", "0-cfa")],
            "heap_context_policy": "2-cfa",
            "heap_context_depth": 2,
        },
        ("left", "right"),
    )
    assert raw["solver_status"] == "converged", raw
    assert constants(raw, "left") == constants(raw, "right") == {5, 7}


@pytest.mark.parametrize(
    "rows",
    [
        [{"module": "subject", "qualname": "subject.f"}],
        [{"module": "subject", "qualname": 3, "policy": "0-cfa"}],
        [{"module": "subject", "qualname": "other.f", "policy": "0-cfa"}],
        [rule("f", "0-cfa"), rule("f", "0-cfa")],
    ],
)
def test_malformed_exact_rules_are_rejected_before_solving(rows: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        parse_method_context_policies(rows)


@pytest.mark.parametrize("engine", ["ai_primary", "hybrid"])
def test_unsupported_island_routes_reject_table_at_request_resolution(engine: str) -> None:
    with pytest.raises(ConfigurationError):
        resolve_config(
            Source("result=11", module="subject"),
            {"engine": engine, "andersen": {"method_context_policies": [rule("f", "0-cfa")]}},
        )


def test_empty_table_preserves_public_facts_and_selected_work() -> None:
    source = "def identity(x):\n    return x\na=identity(5)\nresult=identity(7)\n"
    first = public(Source(source, module="subject"), {"context_policy": "2-cfa"}, ("result",))
    second = public(
        Source(source, module="subject"),
        {"context_policy": "2-cfa", "method_context_policies": []},
        ("result",),
    )
    assert first["solver_status"] == second["solver_status"] == "converged"
    assert first["values"] == second["values"]
    assert (
        require_object(first["stats"])["native_selected_work"]
        == require_object(second["stats"])["native_selected_work"]
    )


def test_one_method_table_supports_all_captured_root_scenarios(tmp_path: Path) -> None:
    first = "def f(x):\n    return x\nold=f(5)\nresult=f(7)\n"
    second = "def g(x):\n    return x\nold=g(9)\nresult=g(11)\n"
    assert concrete(first, ("result",)) == {"result": 7}
    assert concrete(second, ("result",)) == {"result": 11}
    (tmp_path / "a.py").write_text(first)
    (tmp_path / "b.py").write_text(second)
    result = analyze(
        Project(tmp_path, entries=("a:", "b:")),
        config={
            "engine": "native",
            "andersen": {
                "context_policy": "2-cfa",
                "enable_synthetic_method_contexts": False,
                "method_context_policies": [rule("f", "0-cfa", "a"), rule("g", "2-cfa", "b")],
            },
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=(ValueQuery("a", "result"), ValueQuery("b", "result"), "stats"),
    )
    raw = result.raw_snapshot()
    assert raw["solver_status"] == "converged", raw
    values = require_object(raw["values"])
    a = require_object(require_object(values["a"])["result"])
    b = require_object(require_object(values["b"])["result"])
    assert not a["top"] and set(a["constants"]) == {5, 7}
    assert not b["top"] and b["constants"] == [11]
    decisions = require_object(
        require_object(require_object(raw["stats"])["method_context_policy"])["decisions"]
    )
    assert decisions["a:a.f:0-cfa"] >= 2
    assert decisions["b:b.g:2-cfa"] >= 2


@pytest.mark.parametrize("policy,expected", [("0-cfa", {5, 7}), ("2-cfa", {7})])
@pytest.mark.parametrize("keyword", [False, True])
def test_raw_staticmethod_forwards_actual_arguments_and_method_rule(
    policy: str, expected: set[int], keyword: bool
) -> None:
    signature = "*, x" if keyword else "x"
    first = "x=5" if keyword else "5"
    second = "x=7" if keyword else "7"
    source = (
        f"def identity({signature}):\n    return x\nwrapper=staticmethod(identity)\n"
        f"a=wrapper({first})\nresult=wrapper({second})\n"
    )
    assert concrete(source, ("result",)) == {"result": 7}
    raw = public(
        Source(source, module="subject"),
        {"context_policy": "2-cfa", "method_context_policies": [rule("identity", policy)]},
        ("result",),
    )
    assert raw["solver_status"] == "converged", raw
    assert constants(raw, "result") == expected


@pytest.mark.parametrize("policy,expected", [("0-cfa", {5, 7}), ("2-cfa", {7})])
def test_returned_staticmethod_preserves_persistent_target_flow(
    policy: str, expected: set[int]
) -> None:
    source = (
        "def identity(x):\n    return x\ndef wrap(f):\n    return staticmethod(f)\n"
        "wrapper=wrap(identity)\na=wrapper(5)\nresult=wrapper(7)\n"
    )
    assert concrete(source, ("result",)) == {"result": 7}
    raw = public(
        Source(source, module="subject"),
        {"context_policy": "2-cfa", "method_context_policies": [rule("identity", policy)]},
        ("result",),
    )
    assert raw["solver_status"] == "converged", raw
    assert constants(raw, "result") == expected


def test_raw_classmethod_does_not_invoke_underlying_function() -> None:
    source = (
        "def identity(x):\n    return x\nwrapper=classmethod(identity)\n"
        "try:\n    wrapper(7)\nexcept TypeError:\n    result=11\n"
    )
    assert concrete(source, ("result",)) == {"result": 11}
    raw = public(
        Source(source, module="subject"),
        {"context_policy": "2-cfa", "method_context_policies": [rule("identity", "0-cfa")]},
        ("result",),
    )
    assert raw["solver_status"] == "converged", raw
    assert 11 in constants(raw, "result")
    assert not any(require_object(call)["callee"] == "subject.identity" for call in raw["calls"])


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_module", "missing_module", "extra_field", "wrong_prefix", "duplicate_name"],
)
def test_captured_catalog_shape_and_identity_reject_malformed_values(mutation: str) -> None:
    a = SourceModule("a", "a.py", "def f(x):\n    return x\n")
    b = SourceModule("b", "b.py", "def g(x):\n    return x\n")
    rows = [
        {"module": "a", "digest": a.digest, "qualnames": ["a.f"]},
        {"module": "b", "digest": b.digest, "qualnames": ["b.g"]},
    ]
    if mutation == "duplicate_module":
        rows.append(dict(rows[0]))
    elif mutation == "missing_module":
        rows.pop()
    elif mutation == "extra_field":
        rows[0]["extra"] = "unexpected"
    elif mutation == "wrong_prefix":
        rows[0]["qualnames"] = ["b.f"]
    else:
        rows[0]["qualnames"] = ["a.f", "a.f"]
    with pytest.raises((TypeError, ValueError)):
        CapturedMethodInventory.from_wire(rows, {"a": a, "b": b})


def test_captured_catalog_owns_its_validated_names() -> None:
    source = SourceModule("a", "a.py", "def f(x):\n    return x\n")
    names = ["a.f"]
    rows = [{"module": "a", "digest": source.digest, "qualnames": names}]
    captured = CapturedMethodInventory.from_wire(rows, {"a": source})
    names.append("a.changed")
    rows[0]["digest"] = "changed"
    assert captured.targets == frozenset({("a", "a.f")})
    assert captured.sources == (("a", source.digest, ("a.f",)),)


def test_native_then_ai_prepass_consumes_the_same_method_rule() -> None:
    source = "def identity(x):\n    return x\na=identity(5)\nresult=identity(7)\n"
    assert concrete(source, ("result",)) == {"result": 7}
    result = analyze(
        Source(source, module="subject"),
        config={
            "engine": "native_then_ai",
            "andersen": {
                "context_policy": "2-cfa",
                "enable_synthetic_method_contexts": False,
                "method_context_policies": [rule("identity", "0-cfa")],
            },
            "abstract": {"context_depth": 2},
            "resources": {"max_work": 10000, "max_seconds": 2, "max_memory_mb": 512},
        },
        queries=(ValueQuery("subject", "result"), "stats"),
    )
    raw = result.raw_snapshot()
    assert raw["solver_status"] == "converged", raw
    assert constants(raw, "result") == {7}
    prepass = require_object(require_object(raw["stats"])["native_prepass"])
    decisions = require_object(require_object(prepass["method_context_policy"])["decisions"])
    assert decisions["subject:subject.identity:0-cfa"] >= 2


@pytest.mark.parametrize("policy,expected", [("0-cfa", {5, 7}), ("2-cfa", {7})])
def test_private_source_method_keeps_exact_target_identity(policy: str, expected: set[int]) -> None:
    source = (
        "class C:\n    def __pick(self,x):\n        return x\n"
        "    def route(self,x):\n        return self.__pick(x)\n"
        "receiver=C()\na=receiver.route(5)\nresult=receiver.route(7)\n"
    )
    assert concrete(source, ("result",)) == {"result": 7}
    raw = public(
        Source(source, module="subject"),
        {"context_policy": "2-cfa", "method_context_policies": [rule("C.__pick", policy)]},
        ("result",),
    )
    assert raw["solver_status"] == "converged", raw
    assert constants(raw, "result") == expected
    decisions = require_object(
        require_object(require_object(raw["stats"])["method_context_policy"])["decisions"]
    )
    assert decisions["subject:subject.C.__pick:" + policy] >= 2
