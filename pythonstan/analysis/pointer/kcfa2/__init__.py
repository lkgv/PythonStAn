"""k-CFA pointer analysis with 2-object sensitivity.

This package implements a context-sensitive pointer analysis for Python
using k-CFA with 2-object sensitivity. The analysis tracks object allocations
through calling contexts and receiver object contexts.

Key features:
- k-CFA calling context sensitivity (default k=2)
- 2-object sensitivity for allocation contexts
- Field-sensitive heap modeling
- Support for Python's dynamic features
- Integration with PythonStAn IR and call graph infrastructure

Example usage:
    >>> from pythonstan.analysis.pointer.kcfa2 import KCFA2PointerAnalysis, KCFAConfig
    >>> config = KCFAConfig(k=2, obj_depth=2, verbose=True)
    >>> analysis = KCFA2PointerAnalysis(config)
    >>> analysis.plan(ir_functions)
    >>> analysis.initialize()
    >>> analysis.run()
    >>> results = analysis.results()
"""

from .config import KCFAConfig
from .analysis import KCFA2PointerAnalysis
from .model import (
    AbstractLocation, 
    AbstractObject, 
    FieldKey, 
    PointsToSet
)
from .context import CallSite, Context
from .errors import (
    KCFAError,
    ConfigurationError,
    IRAdapterError, 
    AnalysisTimeout,
    SoundnessWarning
)
from .async_facts import AsyncFactsHelper
from .async_types import (
    AsyncFact,
    CoroutineDefFact,
    AwaitEdgeFact,
    TaskCreateFact,
    TaskStateFact,
    FutureFact,
    QueueAllocFact,
    QueuePutFact,
    QueueGetFact,
    SyncAllocFact,
    SyncOpFact,
    LoopCallbackScheduleFact,
    CallbackEdgeFact,
    StreamFact
)

__version__ = "0.1.0"

__all__ = [
    # Main analysis class
    "KCFA2PointerAnalysis",
    
    # Configuration
    "KCFAConfig",
    
    # Core model classes
    "AbstractLocation",
    "AbstractObject", 
    "FieldKey",
    "PointsToSet",
    "CallSite",
    "Context",
    
    # Async facts helper
    "AsyncFactsHelper",
    
    # Async fact types
    "AsyncFact",
    "CoroutineDefFact",
    "AwaitEdgeFact", 
    "TaskCreateFact",
    "TaskStateFact",
    "FutureFact",
    "QueueAllocFact",
    "QueuePutFact",
    "QueueGetFact",
    "SyncAllocFact",
    "SyncOpFact",
    "LoopCallbackScheduleFact",
    "CallbackEdgeFact",
    "StreamFact",
    
    # Exceptions
    "KCFAError",
    "ConfigurationError",
    "IRAdapterError",
    "AnalysisTimeout", 
    "SoundnessWarning",
]