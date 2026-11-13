"""Execute a frozen source-bound plan with explicit run-local attachments."""

from __future__ import annotations

import ast
from dataclasses import replace
from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.json_data import JsonObject
from pythonstan.analysis.domains.values import ValueOperations
from pythonstan.analysis.engines.abstract.state import Frame
from pythonstan.analysis.policies.region import RegionExecutionContext
from pythonstan.analysis.state.flow import Flow, as_statement

from .plan import EffectiveRegionPlan
from .region_engine import NativeRegionEngine, validate_region_plan
from .state_adapter import reabstract_state

if TYPE_CHECKING:
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver


class RegionScheduler:
    plan: EffectiveRegionPlan
    native: NativeRegionEngine
    transitions: list[JsonObject]
    _contexts: dict[str, RegionExecutionContext]

    def __init__(self, plan: EffectiveRegionPlan) -> None:
        self.plan = plan
        self.native = NativeRegionEngine()
        self.transitions = []
        self._contexts = {}

    def validate(self, driver: InterproceduralDriver) -> bool:
        return validate_region_plan(driver, self.plan)

    def execute(
        self, node: ast.stmt, flow: Flow, frame: Frame, driver: InterproceduralDriver
    ) -> list[Flow]:
        decision = self.plan.decision(frame.module, frame.qualname, driver.point(node))
        execution = self.execution_for(node, frame, driver)
        policy = execution.policy
        scoped_frame = replace(frame, execution=execution)
        adjusted = flow
        if execution.ops.policy != driver.execution_for(frame).ops.policy:
            adjusted = reabstract_state(flow, execution.ops)
            driver.increment_stat("domain_region_adaptations", 1)
            self.transitions.append(
                {
                    "kind": "scalar_domain",
                    "point": driver.point(node),
                    "policy_digest": policy.digest,
                    "region_id": decision.region_id,
                }
            )
        if decision.engine == "native":
            outputs = self.native.execute(node, adjusted, scoped_frame, driver, execution=execution)
            self.transitions.append(
                {
                    "kind": "engine",
                    "point": driver.point(node),
                    "from": "ai_primary",
                    "to": "native",
                    "return_to": "ai_primary",
                    "region_id": decision.region_id,
                    "policy_digest": policy.digest,
                }
            )
        else:
            outputs = driver.transfer_for(execution).statement(node, adjusted.store, scoped_frame)
        outputs = driver.coarsen_flows(outputs, execution.ops)
        return [as_statement(output) for output in outputs]

    def execution_for(
        self, node: ast.stmt, frame: Frame, driver: InterproceduralDriver
    ) -> RegionExecutionContext:
        """Use the same attachment for statement execution and path compaction."""
        policy = self.plan.decision(frame.module, frame.qualname, driver.point(node)).policy
        if policy.digest == driver.default_execution.policy.digest:
            return driver.default_execution
        execution = self._contexts.get(policy.digest)
        if execution is None:
            execution = RegionExecutionContext(
                policy, ValueOperations(policy.value_policy, arena=driver.ops.arena)
            )
            self._contexts[policy.digest] = execution
        elif execution.ops.arena is not driver.ops.arena:
            raise RuntimeError("A region scheduler cannot be shared across driver arenas")
        return execution
