"""Composition is the sole installation point for optional execution bridges."""
from .oracle import IncrementalCandidateOracle
from .region_scheduler import RegionScheduler

def configure_driver(driver,*,plan=None,source_digest=None,config=None,summary_provider=None):
    options=config.to_dict() if hasattr(config,"to_dict") else dict(config or driver.options)
    if plan is not None:
        driver.region_plan=plan;driver.region_scheduler=RegionScheduler(plan)
    if options.get("engine")=="hybrid":
        if source_digest is None:
            from pythonstan.analysis.contracts import stable_digest
            source_digest=stable_digest({name:source.digest for name,source in driver.modules.items()})
        oracle=IncrementalCandidateOracle(source_digest=source_digest,profile=options.get("semantic_profile","python-core-v1"),policy_digest=plan.policy_digest if plan is not None else "",ops=driver.ops,max_work=options.get("andersen",{}).get("max_iterations",100000))
        oracle.bind(driver,source_digest=source_digest,profile=options.get("semantic_profile","python-core-v1"));driver.oracle=oracle
    if summary_provider is not None:driver.summary_provider=summary_provider
    return driver

def run_hybrid(snapshot,config,*,plan=None,observer=None,summary_provider=None):
    from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
    options=config.to_dict() if hasattr(config,"to_dict") else dict(config)
    if plan is None and options.get("regions"):
        from .plan import build_plan
        plan=build_plan(snapshot,options)
    driver=InterproceduralDriver(snapshot.module_map,options=options,observer=observer)
    configure_driver(driver,plan=plan,source_digest=snapshot.source_digest,config=options,summary_provider=summary_provider)
    return driver.run(snapshot.scenarios)
