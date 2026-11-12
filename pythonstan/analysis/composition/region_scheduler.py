"""Execute a frozen source-bound region plan through explicit adapters."""

from __future__ import annotations

import ast
from dataclasses import replace
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.json_data import JsonObject, require_int, thaw_json_object
from pythonstan.analysis.domains.values import ValueOperations, ValuePolicy
from pythonstan.analysis.engines.abstract.state import Frame
from pythonstan.analysis.state.flow import Flow, as_statement

from .plan import EffectiveRegionPlan
from .region_engine import NativeRegionEngine, validate_region_plan
from .state_adapter import reabstract_state

if TYPE_CHECKING:
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver


def _policy_wire(policy: ValuePolicy) -> JsonObject:
    return {
        "max_const_strings": policy.max_const_strings,
        "max_const_ints": policy.max_const_ints,
        "max_addr_set_size": policy.max_addr_set_size,
        "max_scalar_pairs": policy.max_scalar_pairs,
        "max_int_bits": policy.max_int_bits,
    }


class RegionScheduler:
    plan: EffectiveRegionPlan
    native: NativeRegionEngine
    transitions: list[JsonObject]

    def __init__(self, plan: EffectiveRegionPlan) -> None:
        self.plan = plan
        self.native = NativeRegionEngine()
        self.transitions = []

    def validate(self, driver: InterproceduralDriver) -> bool:
        return validate_region_plan(driver, self.plan)

    def execute(
        self, node: ast.stmt, flow: Flow, frame: Frame, driver: InterproceduralDriver
    ) -> list[Flow]:
        decision = self.plan.decision(frame.module, frame.qualname, driver.point(node))
        overrides = thaw_json_object(decision.abstract)
        old_ops, old_abstract, old_transfer_ops = driver.ops, driver.abstract, driver.transfer.ops
        adjusted = flow
        if overrides:
            settings: JsonObject = {**driver.abstract, **overrides}
            policy = replace(
                old_ops.policy,
                max_const_strings=require_int(
                    settings.get("max_const_strings", old_ops.policy.max_const_strings)
                ),
                max_const_ints=require_int(
                    settings.get("int_domain_max_consts", old_ops.policy.max_const_ints)
                ),
                max_addr_set_size=require_int(
                    settings.get("max_addr_set_size", old_ops.policy.max_addr_set_size)
                ),
                max_scalar_pairs=require_int(
                    settings.get("max_scalar_pairs", old_ops.policy.max_scalar_pairs)
                ),
            )
            new_ops = ValueOperations(policy, arena=old_ops.arena)
            adjusted = reabstract_state(flow, new_ops)
            driver.ops, driver.transfer.ops, driver.abstract = new_ops, new_ops, settings
            driver.stats["domain_region_adaptations"] = (
                require_int(driver.stats.get("domain_region_adaptations", 0)) + 1
            )
            self.transitions.append(
                {
                    "kind": "scalar_domain",
                    "point": driver.point(node),
                    "before": _policy_wire(old_ops.policy),
                    "after": _policy_wire(policy),
                }
            )
        try:
            if decision.engine == "native":
                outputs = self.native.execute(node, adjusted, frame, driver)
                self.transitions.append(
                    {
                        "kind": "engine",
                        "point": driver.point(node),
                        "from": "ai_primary",
                        "to": "native",
                        "return_to": "ai_primary",
                        "region_id": decision.region_id,
                    }
                )
            else:
                outputs = driver.transfer.statement(node, adjusted.store, frame)
            outputs = driver.coarsen_flows(outputs, driver.ops)
        finally:
            driver.ops, driver.transfer.ops, driver.abstract = (
                old_ops,
                old_transfer_ops,
                old_abstract,
            )
        return [as_statement(output) for output in outputs]
