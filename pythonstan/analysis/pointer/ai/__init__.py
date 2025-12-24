"""Abstract Interpretation (AI) engine for PTA-driven demand analysis.

This package implements a modular abstract interpreter that can be called
by the k-CFA pointer analysis to produce sound summaries for selected
scopes/call sites. The AI can query PTA's points-to and field-points-to
information, and returns summaries (returns, field writes, effects) that
PTA can compile into constraints.

Architecture:
- api: PtaQuery protocol, AISummary, AISummaryEngine entry point
- state: AIState (env, heap, exn state for intraprocedural interpretation)
- heap: FieldStore (bounded map + default bucket), AbsObj
- domains: Pluggable value domains (strings, containers, bool)
- interpreter: IR transfer functions per ai_spec.md §7
- solver: Worklist + SCC fixpoint solver with widening/narrowing
- summary: Summary extraction in PTA-compatible form

Usage:
    from pythonstan.analysis.pointer.ai import AISummaryEngine, AnalysisBudget
    
    engine = AISummaryEngine(pta_query, AnalysisBudget())
    summary = engine.analyze_callee(scope_ir, call_context, arg_bindings)
    
    # Summary contains: ret, writes, reads, unknown_effects
    for write in summary.writes:
        print(f"Write: {write}")
"""

from .api import (
    PtaQuery,
    AISummary,
    AISummaryEngine,
    FieldWrite,
    UnknownEffect,
    ArgBinding,
    AnalysisBudget,
)
from .state import AIState, AbsVal
from .heap import (
    AIAddr,
    FieldStore,
    AbsObj,
    ObjKind,
    ContainerSlots,
)
from .summary import SummaryAccumulator
from .interpreter import IRInterpreter, TransferResult
from .solver import AISolver, SolverConfig

__all__ = [
    # API
    "PtaQuery",
    "AISummary",
    "AISummaryEngine",
    "FieldWrite",
    "UnknownEffect",
    "ArgBinding",
    "AnalysisBudget",
    # State
    "AIState",
    "AbsVal",
    # Heap
    "AIAddr",
    "FieldStore",
    "AbsObj",
    "ObjKind",
    "ContainerSlots",
    # Summary
    "SummaryAccumulator",
    # Interpreter
    "IRInterpreter",
    "TransferResult",
    # Solver
    "AISolver",
    "SolverConfig",
]
