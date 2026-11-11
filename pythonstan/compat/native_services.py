"""Explicit runtime service injection for the retained legacy Pipeline."""


def configure_native_services(manager, *, max_work=None, source_metadata=None):
    configured=[]
    metadata=None
    if source_metadata is not None:
        if type(source_metadata) is not dict or any(type(name) is not str or type(values) is not dict
                or set(values)-{"__file__","__name__","__package__"}
                or any(type(value) is not str for value in values.values())
                for name,values in source_metadata.items()):
            raise ValueError("Source metadata must map modules to explicit string metadata")
        metadata={name:dict(values) for name,values in source_metadata.items()}
    for name,analyzer in manager.analyzers.items():
        driver=getattr(analyzer,"driver",analyzer)
        solver=getattr(driver,"solver",None)
        if solver is None or not hasattr(solver,"set_intrinsic_provider"):
            continue
        if metadata is not None:
            if solver._work_iteration_started or solver.work_ledger.used:
                raise ValueError("Source metadata must be fixed before execution")
            solver.state.source_metadata={name:dict(values) for name,values in metadata.items()}
        if max_work is not None:
            solver.set_global_work_limit(max_work)
        if solver.config.enable_ai_intrinsics and solver.intrinsic_provider is None:
            from pythonstan.analysis.composition.native_intrinsics import LegacyIntrinsicProvider
            solver.set_intrinsic_provider(LegacyIntrinsicProvider())
        configured.append(name)
    return tuple(configured)
