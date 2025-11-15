"""k-CFA Pointer Analysis for Python.

This package provides context-sensitive pointer analysis for Python programs
using k-CFA and related context sensitivity policies.

Main entry point:
    >>> from pythonstan.analysis.pointer.kcfa import PointerAnalysis, Config
    >>> config = Config(context_policy="2-cfa")
    >>> analysis = PointerAnalysis(config)
    >>> result = analysis.analyze(ir_module)

For detailed usage, see documentation in docs/kcfa/
"""

# Public API exports
from .analysis import PointerAnalysis, AnalysisResult
from .config import Config
from .context import (
    CallSite,
    AbstractContext,
    CallStringContext,
    ObjectContext,
    TypeContext,
    ReceiverContext,
    HybridContext,
    Scope
)
from .context_selector import ContextPolicy, ContextSelector, parse_policy
from .object import AllocKind, AllocSite, AbstractObject
from .variable import VariableKind, Variable
from .heap_model import FieldKind, Field, attr, elem, unknown
from .state import PointsToSet, PointerAnalysisState
from .solver import PointerSolver
from .ir_translator import IRTranslator
from .constraints import (
    Constraint,
    CopyConstraint,
    LoadConstraint,
    StoreConstraint,
    AllocConstraint,
    CallConstraint,
    ReturnConstraint,
    ConstraintManager
)
from .class_hierarchy import ClassHierarchyManager, MROError
from .builtin_api_handler import BuiltinSummary

__all__ = [
    # Main entry points
    "PointerAnalysis",
    "AnalysisResult",
    "Config",
    
    # Context types
    "CallSite",
    "AbstractContext",
    "CallStringContext",
    "ObjectContext",
    "TypeContext",
    "ReceiverContext",
    "HybridContext",
    "ContextPolicy",
    "ContextSelector",
    "parse_policy",
    
    # Object model
    "AllocKind",
    "AllocSite",
    "AbstractObject",
    "VariableKind",
    "Scope",
    "Variable",
    "FieldKind",
    "Field",
    "attr",
    "elem",
    "value",
    "unknown",
    "PointsToSet",
    "PointerAnalysisState",
    "PointerSolver",
    
    # Constraints
    "Constraint",
    "CopyConstraint",
    "LoadConstraint",
    "StoreConstraint",
    "AllocConstraint",
    "CallConstraint",
    "ReturnConstraint",
    "ConstraintManager",
    
    # Class hierarchy
    "ClassHierarchyManager",
    "MROError",
    
    # Extension points
    "BuiltinSummary",
]

__version__ = "1.0.0"


