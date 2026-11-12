"""Observed summary versions used by real caller invalidation."""
from __future__ import annotations

from dataclasses import dataclass

from pythonstan.analysis.contracts.identity import IdentityEpoch, PolicyId, StateRevision, UnitId


@dataclass(frozen=True, slots=True)
class SummaryDependency:
    producer: UnitId
    observed_revision: StateRevision
    input_scope: str
    policy: PolicyId
    epoch: IdentityEpoch

    def needs_refresh(
        self,
        *,
        revision: StateRevision,
        input_scope: str,
        policy: PolicyId,
        epoch: IdentityEpoch,
    ) -> bool:
        return (
            self.observed_revision != revision
            or self.input_scope != input_scope
            or self.policy != policy
            or self.epoch != epoch
        )
