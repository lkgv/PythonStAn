# k-CFA Pointer Analysis Design with 2-Object Sensitivity

Version: v2

## Architecture Overview

The k-CFA pointer analysis with 2-object sensitivity (KCFA2) is a context-sensitive static analysis for tracking object allocation and pointer relationships in Python programs. This design document outlines the architecture, invariants, and integration plans for the analysis.

### Core Components

1. **Analysis Engine** (`analysis.py`): Main analysis driver implementing constraint-based pointer analysis
2. **Context Management** (`context.py`): k-CFA calling context tracking and management
3. **Heap Model** (`heap_model.py`): 2-object sensitive heap abstraction and object creation
4. **Data Model** (`model.py`): Core abstractions (locations, objects, points-to sets)
5. **IR Adapter** (`ir_adapter.py`): Event-based interface to PythonStAn IR/TAC
6. **Call Graph Adapter** (`callgraph_adapter.py`): Context-sensitive call graph construction
7. **Configuration** (`config.py`): Analysis parameters and tuning options

## Key Invariants

### Context Sensitivity Invariants

1. **k-CFA Contexts**: Calling contexts are bounded by parameter k (default: 2)
   - Contexts are sequences of call sites: `[cs₁, cs₂, ..., csₖ]`
   - Longer call strings are truncated to maintain finiteness
   - Empty context `[]` represents the main entry point

2. **2-Object Sensitivity**: Object allocation contexts depend on receiver objects
   - Abstract objects carry allocation context + receiver context fingerprint
   - Receiver fingerprint tracks allocation info of receiver objects up to depth `obj_depth`
   - Enables precise modeling of object-oriented patterns

### Heap Model Invariants

1. **Field Sensitivity**: Configurable field abstraction
   - `attr-name`: Distinguish attributes by name (`obj.foo` vs `obj.bar`)
   - `field-insensitive`: Single abstract field for all attributes
   - Container elements use unified fields (`elem`, `value`)

2. **Allocation Site Identity**: Stable allocation site identifiers
   - Preferred format: `{file}:{line}:{col}:{kind}`
   - Fallback format: `{file_stem}:{op}:{hash(uid):x}` for missing location info
   - Deterministic across analysis runs for reproducibility

3. **Points-to Set Monotonicity**: Points-to sets only grow during analysis
   - Join operation is set union
   - No removal once objects are added
   - Convergence guaranteed by monotonicity

## Analysis Algorithm

### Phase 1: Planning
- Extract functions from IR module/list
- Build initial function registry
- Validate configuration parameters

### Phase 2: Initialization  
- Create empty context and initial environments
- Set up worklists for constraints and calls
- Add entry point functions to call worklist

### Phase 3: Iterative Constraint Solving
```
while worklists not empty:
    process_constraints()  // Handle copy, load, store constraints
    process_calls()        // Resolve targets, create contexts, add constraints
```

### Phase 4: Result Extraction
- Collect points-to information for all tracked locations
- Build context-sensitive call graph
- Generate analysis statistics

## 2-Object Sensitivity Interpretation

The 2-object sensitivity extends k-CFA by incorporating receiver object contexts into allocation site abstractions:

### Standard k-CFA Object
```
AbstractObject = (alloc_site, alloc_context)
```

### 2-Object Sensitive Object  
```
AbstractObject = (alloc_site, alloc_context, recv_ctx_fingerprint)
```

### Receiver Context Fingerprint
When allocating object `o` through method call on receiver `r`:
```
recv_ctx_fingerprint = fingerprint([r₁, r₂, ..., rₒ])
```
Where `r₁, r₂, ..., rₒ` are the receiver objects in the call chain up to depth `obj_depth`.

### Example
```python
class A:
    def create(self): return B()

class B: pass

a1 = A()  # Object: (A_alloc, [], None)
a2 = A()  # Object: (A_alloc, [], None) - same as a1 without 2-obj

b1 = a1.create()  # Object: (B_alloc, [create_call], fingerprint([a1]))
b2 = a2.create()  # Object: (B_alloc, [create_call], fingerprint([a2]))
```

With 2-object sensitivity, `b1` and `b2` are distinguished by their receiver contexts.

## Call Graph Integration

### Context-Sensitive Call Graph
- Edges: `(caller_ctx, call_site) → {(callee_ctx, callee_fn)}`
- Integrates with `pythonstan.graph.call_graph.CallGraph`
- Supports both direct and indirect calls
- Handles method resolution with receiver objects

### Target Resolution Strategy
1. **Static Resolution**: Use class hierarchy and method resolution order
2. **Dynamic Resolution**: Conservative approximation for dynamic calls
3. **Builtin Handling**: Function summaries for builtin/external functions

## Configuration Parameters

### Core Parameters
- `k`: Call string length (default: 2)
- `obj_depth`: Object sensitivity depth (default: 2)  
- `field_sensitivity_mode`: Field abstraction strategy
- `containers`: Container field mapping (list→elem, dict→value)

### Performance Parameters
- `timeouts`: Analysis timeout in seconds
- `max_heap_widening`: Heap size limit before widening
- `verbose`: Detailed logging flag

## Integration Points

### PythonStAn IR Integration
- Import from `pythonstan.ir.ir_statements`
- Handle IR operations: assign, call, attr load/store, subscript access
- Extract source location information for allocation sites

### TAC Integration  
- Import from `pythonstan.analysis.transform.three_address`
- Handle three-address code instructions
- Map TAC variables to abstract locations

### Pipeline Integration
- Register with `pythonstan.world.analysis_manager`
- Support analysis pipeline execution
- Provide results interface for downstream analyses

## Implementation Status

### Completed Components (Session 4)

1. **Context Management** (`context.py`): ✅ Complete
   - CallSite, Context, ContextSelector, ContextManager
   - Proper k-CFA algorithm with k-limiting
   - String representations include function names for debugging

2. **Heap Model** (`heap_model.py`): ✅ Complete  
   - 2-object sensitivity with receiver context fingerprints
   - Field key constructors for all access patterns
   - Stable allocation site ID generation with fallbacks

3. **Worklist Management** (`worklist.py`): ✅ Complete
   - Generic Worklist with FIFO/LIFO modes
   - Specialized ConstraintWorklist and CallWorklist
   - Deterministic behavior avoiding nondeterministic iteration

4. **IR Adapter** (`ir_adapter.py`): ✅ Minimal Implementation
   - TypedDict event schemas for all pointer operations
   - Site ID extraction with source location support
   - Stub implementation ready for IR/TAC integration

### Test Coverage

- **46 tests passing**: All foundational utilities fully tested
- **Interface tests**: Module imports, equality/hash invariants
- **Context tests**: k-CFA operations, recursion, context management  
- **Heap model tests**: Object sensitivity, field addressing
- **Integration tests**: Site ID generation, basic IR adapter

### Ready for Next Phase

The foundational utilities provide a solid base for implementing:
- Constraint generation from IR/TAC events
- Constraint solving with worklist algorithms
- Call graph construction with context sensitivity
- Full analysis driver implementation

## Open Questions and Future Work

1. **Heap Abstraction**: When and how to apply widening for large heaps
2. **Exception Handling**: Modeling exception propagation and handlers
3. **Generator Support**: Proper modeling of generator frames and yields
4. **Descriptor Protocol**: Handling `__get__`, `__set__`, property access
5. **Metaclass Support**: Analysis of metaclass instantiation patterns
6. **IR Integration**: Complete implementation of iter_function_events()
7. **Performance Optimization**: Efficiency tuning for large codebases

## References

- Milanova, A., Rountev, A., & Ryder, B. G. (2005). Parameterized object sensitivity for points-to analysis for Java.
- Smaragdakis, Y., & Bravenboer, M. (2011). Using datalog for fast and easy program analysis.
- Lhoták, O., & Hendren, L. (2003). Scaling Java points-to analysis using SPARK.
