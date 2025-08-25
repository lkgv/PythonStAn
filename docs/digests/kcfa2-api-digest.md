# k-CFA API Digest

Version: v1

## Core Analysis Class

- `KCFA2PointerAnalysis`: Main analysis driver with plan/initialize/run/results workflow
- `KCFAConfig`: Configuration class with k, obj_depth, field sensitivity settings

## Data Model

- `AbstractLocation`: Program location with function, name, and context
- `AbstractObject`: Heap object with allocation site, context, and receiver fingerprint  
- `FieldKey`: Field access key (attr name, elem, value, unknown)
- `PointsToSet`: Immutable set of abstract objects with lattice operations

## Context Management

- `CallSite`: Call site with site ID, function, basic block, index
- `Context`: Calling context as sequence of call sites (k-CFA)
- `ContextSelector`: Context selection policy with k-limiting
- `ContextManager`: Context stack management for analysis traversal

## Heap Modeling

- `make_object()`: Create abstract object with 2-object sensitivity
- `attr_key()`, `elem_key()`, `value_key()`: Field key constructors
- `compute_recv_context_fingerprint()`: Receiver context computation

## IR Integration

- `Event` types: AllocEvent, AttrLoadEvent, AttrStoreEvent, CallEvent, etc.
- `iter_function_events()`: Extract events from IR/TAC functions
- `site_id_of()`: Generate stable allocation/call site identifiers

## Worklist Management

- `Worklist[T]`: Generic deterministic worklist with FIFO/LIFO modes
- `ConstraintWorklist`: Specialized for pointer constraints
- `CallWorklist`: Specialized for function call processing

## Call Graph Support

- `CallGraphAdapter`: Context-sensitive call graph wrapper
- `add_edge()`: Add call graph edge with contexts
- `resolve_targets()`: Resolve call targets (static/dynamic)

## Builtin Support

- `FunctionSummary`: Pointer effects summary for external functions
- `BuiltinSummaryManager`: Registry for builtin function summaries
- Built-in handlers: len, iter, list/dict/tuple constructors

## Error Handling

- `KCFAError`: Base exception class
- `ConfigurationError`: Invalid configuration parameters
- `IRAdapterError`: IR/TAC adaptation failures
- `AnalysisTimeout`: Analysis timeout exceeded
- `SoundnessWarning`: Potential soundness issues

## Environment Abstractions

- `Env`: Variable to points-to set mapping
- `Store`: Location to points-to set mapping  
- `Heap`: Object field to points-to set mapping

