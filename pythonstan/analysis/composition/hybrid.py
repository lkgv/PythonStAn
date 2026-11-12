"""Composition is the installation point for actual optional execution bridges."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pythonstan.analysis.contracts.engine import EngineSnapshot
from pythonstan.analysis.contracts.identity import stable_digest
from pythonstan.analysis.contracts.json_data import (
    JsonObject,
    require_int,
    require_object,
    require_string,
)
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.config import EffectiveConfig
from pythonstan.inputs import SourceSnapshot

from .oracle import IncrementalCandidateOracle
from .plan import EffectiveRegionPlan, build_plan
from .region_scheduler import RegionScheduler

if TYPE_CHECKING:
    from pythonstan.analysis.composition.guarded_dispatch import GuardedDispatchProvider
    from pythonstan.analysis.contracts.observation import ObserverPort


def configure_driver(
    driver: InterproceduralDriver,
    *,
    plan: EffectiveRegionPlan | None = None,
    source_digest: str | None = None,
    config: JsonObject | EffectiveConfig | None = None,
    summary_provider: GuardedDispatchProvider | None = None,
) -> InterproceduralDriver:
    options = (
        config.to_dict() if isinstance(config, EffectiveConfig) else dict(config or driver.options)
    )
    if plan is not None:
        driver.region_plan = plan
        driver.region_scheduler = RegionScheduler(plan)
    if options.get("engine") == "hybrid":
        if source_digest is None:
            source_digest = stable_digest(
                {name: source.digest for name, source in driver.modules.items()}
            )
        profile = require_string(options.get("semantic_profile", "python-core-v1"))
        oracle = IncrementalCandidateOracle(
            source_digest=source_digest,
            profile=profile,
            policy_digest=plan.policy_digest if plan is not None else "",
            ops=driver.ops,
            max_work=require_int(
                require_object(options.get("andersen", {})).get("max_iterations", 100000)
            ),
        )
        oracle.bind(driver, source_digest=source_digest, profile=profile)
        driver.oracle = oracle
    if summary_provider is not None:
        driver.summary_provider = summary_provider
    return driver


def run_hybrid(
    snapshot: SourceSnapshot,
    config: JsonObject | EffectiveConfig,
    *,
    plan: EffectiveRegionPlan | None = None,
    observer: ObserverPort | None = None,
    summary_provider: GuardedDispatchProvider | None = None,
) -> EngineSnapshot:
    options = config.to_dict() if isinstance(config, EffectiveConfig) else dict(config)
    if plan is None and options.get("regions"):
        plan = build_plan(snapshot, options)
    driver = InterproceduralDriver(snapshot.module_map, options=options, observer=observer)
    configure_driver(
        driver,
        plan=plan,
        source_digest=snapshot.source_digest,
        config=options,
        summary_provider=summary_provider,
    )
    return driver.run(snapshot.scenarios)
