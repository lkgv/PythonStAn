"""Regional capability, inherited policy and actual execution witnesses."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

import pytest

from pythonstan import Source
from pythonstan.analysis.composition.hybrid import configure_driver
from pythonstan.analysis.composition.plan import EffectiveRegionPlan, build_plan
from pythonstan.analysis.contracts.json_data import JsonObject, require_array, require_object
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.analysis.engines.abstract.transfer import Transfer
from pythonstan.analysis.policies import EffectiveRegionPolicy
from pythonstan.config import ConfigurationError, catalog, json_schema, resolve_config
from pythonstan.inputs import SourceSnapshot, snapshot_target

if TYPE_CHECKING:
    from pythonstan.analysis.state.flow import Flow
    from pythonstan.analysis.state.store import Frame, Store
    from pythonstan.config import EffectiveConfig


def _prepared(
    code: str, config: JsonObject
) -> tuple[EffectiveConfig, SourceSnapshot, EffectiveRegionPlan]:
    target = Source(code, module="subject")
    resolved = resolve_config(target, config={"engine": "ai_primary", **config})
    snapshot = snapshot_target(target, resolved)
    return resolved, snapshot, build_plan(snapshot, resolved)


def _rule(settings: JsonObject, **selector: str | int) -> JsonObject:
    return {"selector": {"module": "subject", **selector}, "abstract": settings}


@pytest.mark.parametrize(
    "settings",
    [
        {"max_iterations": 100},
        {"log_level": "INFO"},
        {"context_policy": "inherit"},
        {"solver_options": {"max_total_traces": 100}},
        {"solver_options": {"merge_points": []}},
    ],
)
def test_unimplemented_regional_consumers_reject_even_default_values(settings: JsonObject) -> None:
    with pytest.raises(ConfigurationError, match="regional.*consumer"):
        _prepared("result=1\n", {"regions": [_rule(settings, line_start=1)]})


def test_relation_aliases_publish_one_config_plan_and_execution_identity() -> None:
    identities: list[tuple[str, str, str]] = []
    for name in ("relations", "enable_value_partition"):
        resolved, snapshot, plan = _prepared(
            "result=1\n", {"regions": [_rule({name: False}, line_start=1)]}
        )
        driver = InterproceduralDriver(snapshot.module_map, resolved.to_dict())
        configure_driver(driver, plan=plan, config=resolved, source_digest=snapshot.source_digest)
        driver.run(snapshot.scenarios)
        decision = next(iter(plan.decisions.values()))
        assert decision.policy.boolean("relations", True) is False
        assert decision.policy.boolean("enable_value_partition", True) is False
        assert driver.region_scheduler is not None
        execution = driver.region_scheduler.execution_for(
            driver.trees["subject"].body[0], next(iter(driver.units.values())).frame, driver
        )
        assert execution.policy is decision.policy
        identities.append((resolved.digest, plan.policy_digest, execution.policy.digest))
        assert driver.policy_digest == plan.policy_digest == resolved.digest
    assert identities[0] == identities[1]


def test_nested_rule_inherits_parent_solver_children_and_scalar_limits() -> None:
    _, _, plan = _prepared(
        "def f():\n    x=1\n    result=x\nresult=f()\n",
        {
            "regions": [
                _rule(
                    {"max_const_strings": 2, "solver_options": {"max_traces_per_block": 1}},
                    qualname="subject.f",
                    kind="function",
                ),
                _rule(
                    {"int_domain_max_consts": 3, "solver_options": {"widen_delay": 4}},
                    qualname="subject.f",
                    line_start=3,
                ),
            ]
        },
    )
    decision = next(value for value in plan.decisions.values() if value.source.span[0] == 3)
    assert decision.policy.value_policy.max_const_strings == 2
    assert decision.policy.value_policy.max_const_ints == 3
    assert decision.policy.max_paths == 1
    assert decision.policy.integer("widen_delay", 0) == 4
    wire = decision.to_dict()
    require_object(require_object(wire["effective_policy"])["abstract"])["context_depth"] = 99
    assert decision.policy.context_depth == 2


def test_overlap_without_nesting_rejects_equal_priority_conflict() -> None:
    with pytest.raises(ConfigurationError, match="Equal-priority region conflict"):
        _prepared(
            "a=1\nb=2\nresult=3\n",
            {
                "regions": [
                    _rule({"max_const_strings": 2}, line_start=1, line_end=2),
                    _rule({"max_const_strings": 3}, line_start=2, line_end=3),
                ]
            },
        )


def test_singleton_recursive_component_cannot_hide_heterogeneous_policies() -> None:
    with pytest.raises(ConfigurationError, match="Heterogeneous recursive SCC"):
        _prepared(
            "def f(x):\n    a=x\n    return f(a)\nresult=f(1)\n",
            {"regions": [_rule({"int_domain_max_consts": 1}, line_start=2)]},
        )


def test_regional_transfer_does_not_replace_global_driver_or_helper_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved, snapshot, plan = _prepared(
        "a=1\nresult=a+2\n",
        {"regions": [_rule({"int_domain_max_consts": 1}, line_start=2)]},
    )
    driver = InterproceduralDriver(snapshot.module_map, resolved.to_dict())
    configure_driver(driver, plan=plan, config=resolved, source_digest=snapshot.source_digest)
    global_ops, global_transfer, global_abstract = driver.ops, driver.transfer, driver.abstract
    seen: list[int] = []
    original = Transfer.statement

    def statement(self: Transfer, node: ast.stmt, store: Store, frame: Frame) -> list[Flow]:
        assert self.d.ops is global_ops
        assert self.d.transfer is global_transfer
        assert self.d.abstract is global_abstract
        assert self.attributes.ops is self.ops
        assert self.dictionaries.ops is self.ops
        assert self.continuations.ops is self.ops
        assert self.namespaces.ops is self.ops
        assert self.dictionary_views.ops is self.ops
        seen.append(self.ops.policy.max_const_ints)
        return original(self, node, store, frame)

    monkeypatch.setattr(Transfer, "statement", statement)
    driver.run(snapshot.scenarios)
    assert 1 in seen and 10 in seen
    assert driver.ops is global_ops and driver.transfer is global_transfer
    assert driver.ops.arena is driver.default_execution.ops.arena
    assert driver.work > 0


def test_plans_from_other_sources_and_configs_are_rejected_before_installation() -> None:
    resolved, snapshot, plan = _prepared("result=1\n", {})
    other_resolved, other_snapshot, _ = _prepared("result=2\n", {})
    other = InterproceduralDriver(other_snapshot.module_map, other_resolved.to_dict())
    with pytest.raises(ConfigurationError, match="source identity"):
        configure_driver(other, plan=plan, config=resolved)
    changed = resolve_config(config={"engine": "ai_primary", "abstract": {"context_depth": 0}})
    driver = InterproceduralDriver(snapshot.module_map, resolved.to_dict())
    with pytest.raises(ConfigurationError, match="configuration identity"):
        configure_driver(driver, plan=plan, config=changed)
    assert driver.region_scheduler is None


def test_policy_attachment_owns_settings() -> None:
    raw: JsonObject = {"context_depth": 1, "solver_options": {"widen_delay": 3}}
    policy = EffectiveRegionPolicy.from_abstract(raw)
    require_object(raw["solver_options"])["widen_delay"] = 100
    policy.abstract["context_depth"] = 100
    assert policy.context_depth == 1 and policy.integer("widen_delay", 0) == 3


def test_legacy_global_solver_default_does_not_activate_a_regional_alias() -> None:
    plain = EffectiveRegionPolicy.from_abstract({})
    legacy = EffectiveRegionPolicy.from_abstract({"solver_options": {"loop_unroll": 5}})
    assert legacy.digest == plain.digest
    assert legacy.integer("widen_delay", 2) == 2


def test_regional_solver_alias_resolves_before_global_legacy_applicability() -> None:
    identities: list[str] = []
    for alias in ("loop_unroll", "widen_delay"):
        resolved, _, plan = _prepared(
            "result=1\n",
            {"regions": [_rule({"solver_options": {alias: 3}}, line_start=1)]},
        )
        identities.append(resolved.digest)
        decision = next(iter(plan.decisions.values()))
        assert decision.policy.integer("widen_delay", 0) == 3
    assert identities[0] == identities[1]


def test_registry_and_schema_advertise_only_explicit_regional_consumers() -> None:
    records = {row["path"]: row for row in catalog()}
    for path, row in records.items():
        assert row["consumer"] and row["version"] and row["effect"] and row["evidence"]
        if "region" in row["scopes"]:
            assert path.startswith("abstract.") and row["capabilities"]
    assert records["abstract.context_depth"]["scopes"] == ("global", "region")
    assert records["resources.max_work"]["scopes"] == ("global",)
    abstract = require_object(
        require_object(require_object(json_schema()["properties"])["abstract"])["properties"]
    )
    solver = require_object(abstract["solver_options"])
    assert "max_traces_per_block" in require_array(solver["x-region-children"])
    assert "max_total_traces" not in require_array(solver["x-region-children"])


@pytest.mark.parametrize("priority", [-(10**40), -1000000001, -(10**9), 0])
@pytest.mark.parametrize("mode", ["single", "duplicate", "conflict", "ordered"])
def test_priority_has_no_implicit_integer_floor(priority: int, mode: str) -> None:
    first = _rule({"context_depth": 0}, line_start=1)
    first["priority"] = priority
    rules = [first]
    if mode != "single":
        second = _rule({"context_depth": 3 if mode == "conflict" else 0}, line_start=1)
        second["priority"] = priority
        if mode == "ordered":
            first["abstract"] = {"context_depth": 3}
            second["priority"] = priority + 1
            rules.insert(0, second)
        else:
            rules.append(second)
    if mode == "conflict":
        with pytest.raises(ConfigurationError, match="Equal-priority region conflict"):
            _prepared("result=1\n", {"regions": rules})
        return
    _, _, plan = _prepared("result=1\n", {"regions": rules})
    assert next(iter(plan.decisions.values())).policy.context_depth == 0
