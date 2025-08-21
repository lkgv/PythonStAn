# k-CFA2 Pointer Analysis Usage Guide

Version: v1

This document explains how to use the k-CFA2 pointer analysis implementation in PythonStAn to analyze Python code and query points-to sets and call graphs.

## Quick Start

### Basic Usage

```python
from pythonstan.analysis.pointer.kcfa2.analysis import KCFA2PointerAnalysis
from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig

# Create configuration
config = KCFAConfig(k=2, obj_depth=2, verbose=True)

# Create analysis instance
analysis = KCFA2PointerAnalysis(config)

# Plan analysis with IR functions
analysis.plan(ir_functions)

# Initialize analysis state
analysis.initialize()

# Run analysis to fixpoint
analysis.run()

# Get results
results = analysis.results()
print(f"Points-to analysis found {len(results['points_to'])} variable bindings")
print(f"Call graph has {results['call_graph']['total_cs_edges']} edges")
```

### Configuration Options

The `KCFAConfig` class controls analysis precision and behavior:

```python
from pythonstan.analysis.pointer.kcfa2.config import KCFAConfig

config = KCFAConfig(
    k=2,                    # Context depth for k-CFA
    obj_depth=2,            # Object sensitivity depth  
    field_sensitive=True,   # Enable field sensitivity
    verbose=False,          # Enable verbose logging
    max_heap_widening=1000  # Heap widening threshold
)
```

**Key Parameters:**
- `k`: Maximum calling context length (default: 2)
- `obj_depth`: Maximum receiver object context depth (default: 2)  
- `field_sensitive`: Whether to distinguish object fields (default: True)
- `verbose`: Enable detailed analysis logging (default: False)

## Running Analysis on Code

### From IR Functions

The most common usage is to analyze IR functions directly:

```python
from pythonstan.analysis.transform.ir import transform_to_ir
from pythonstan.analysis.pointer.kcfa2.analysis import KCFA2PointerAnalysis

# Transform Python code to IR
ir_functions = transform_to_ir(python_source)

# Run pointer analysis
analysis = KCFA2PointerAnalysis()
analysis.plan(ir_functions)
analysis.initialize()
analysis.run()

# Query results
results = analysis.results()
```

### From Code Snippets

For analyzing simple code snippets:

```python
# Example Python code
code = '''
def create_list():
    x = [1, 2, 3]
    return x

def main():
    lst = create_list()
    elem = lst[0] 
    return elem
'''

# Transform and analyze
ir_functions = transform_to_ir(code)
analysis = KCFA2PointerAnalysis()
analysis.plan(ir_functions)
analysis.initialize() 
analysis.run()

# Check points-to sets
results = analysis.results()
for var, objects in results['points_to'].items():
    print(f"{var} points to: {objects}")
```

## Querying Analysis Results

### Points-to Sets

The analysis results contain points-to information for all variables:

```python
results = analysis.results()
points_to = results['points_to']

# Points-to sets are keyed by "variable@context"
for var_ctx, objects in points_to.items():
    var_name = var_ctx.split('@')[0]
    context = var_ctx.split('@')[1]
    print(f"Variable {var_name} in context {context}:")
    for obj in objects:
        print(f"  points to {obj}")
```

### Call Graph

The context-sensitive call graph shows calling relationships:

```python
results = analysis.results()
call_graph = results['call_graph']

print(f"Call graph statistics:")
print(f"  Total edges: {call_graph['total_cs_edges']}")
print(f"  Unique call sites: {call_graph['unique_call_sites']}")
print(f"  Functions called: {call_graph['unique_functions']}")

# Access the call graph adapter directly for detailed queries
cg_adapter = analysis._call_graph

# Get all callers of a function
callers = cg_adapter.get_callers("target_function")
for caller_ctx, call_site in callers:
    print(f"Called from {caller_ctx} at {call_site}")

# Get all callees from a context
from pythonstan.analysis.pointer.kcfa2.context import Context
ctx = Context()
callees = cg_adapter.get_callees(ctx)
for call_site, callee_ctx, callee_fn in callees:
    print(f"Calls {callee_fn} at {call_site} with context {callee_ctx}")
```

### Context Information

The analysis tracks calling contexts for precision:

```python
results = analysis.results()
contexts = results['contexts']

print("Analysis contexts:")
for ctx_str, depth in contexts.items():
    print(f"  {ctx_str} (depth: {depth})")
```

## Working with Abstract Objects

### Object Creation and Querying

Abstract objects represent heap allocations:

```python
from pythonstan.analysis.pointer.kcfa2.model import AbstractObject
from pythonstan.analysis.pointer.kcfa2.context import Context

# Objects are identified by allocation site, context, and receiver fingerprint
ctx = Context()
obj = AbstractObject(
    alloc_id="example.py:10:5:obj",
    alloc_ctx=ctx,
    recv_ctx_fingerprint=None
)

print(f"Object: {obj}")
print(f"Allocation site: {obj.alloc_id}")
print(f"Allocation context: {obj.alloc_ctx}")
```

### Field Access

Objects have fields for attributes and container elements:

```python
from pythonstan.analysis.pointer.kcfa2.heap_model import attr_key, elem_key, value_key

# Create field keys
attr_field = attr_key("foo")      # obj.foo
elem_field = elem_key()           # list[i], set elements  
value_field = value_key()         # dict[key]

# Query heap directly (for advanced usage)
heap = analysis._heap
obj_attr_pts = heap.get((obj, attr_field), PointsToSet())
print(f"Object attribute 'foo' points to: {obj_attr_pts}")
```

## Advanced Usage

### Custom IR Events

For testing or specialized analysis, you can create custom IR events:

```python
from pythonstan.analysis.pointer.kcfa2.ir_adapter import make_alloc_event, make_call_event

# Create allocation event
alloc_event = make_alloc_event(
    alloc_id="test.py:10:5:obj",
    target="x",
    type_="obj"
)

# Create call event  
call_event = make_call_event(
    call_id="test.py:15:8:call",
    callee_symbol="target_func",
    args=["x", "y"],
    target="result"
)

# Process events manually
from pythonstan.analysis.pointer.kcfa2.context import Context
ctx = Context()
analysis._add_event_to_worklist(alloc_event, ctx)
analysis._add_event_to_worklist(call_event, ctx)
```

### Builtin Function Summaries

The analysis includes summaries for Python builtin functions:

```python
from pythonstan.analysis.pointer.kcfa2.summaries import BuiltinSummaryManager

# Access builtin summaries
summaries = BuiltinSummaryManager()

# Check if function has summary
has_len = summaries.has_summary("len")
print(f"len() builtin has summary: {has_len}")

# Get summary for manual application
len_summary = summaries.get_summary("len")
if len_summary:
    # Apply summary manually (advanced usage)
    len_summary.apply(target_var, args, context, analysis)
```

### Worklist Inspection

For debugging, you can inspect the analysis worklists:

```python
# Check worklist sizes during analysis
constraint_worklist = analysis._constraint_worklist
call_worklist = analysis._call_worklist

print(f"Constraint worklist size: {constraint_worklist.size()}")
print(f"Call worklist size: {call_worklist.size()}")

# Access worklist mode
print(f"Constraint processing mode: {constraint_worklist.mode}")
print(f"Call processing mode: {call_worklist.mode}")
```

## Performance Considerations

### Analysis Scalability

- **Context depth**: Higher `k` values increase precision but may cause exponential blowup
- **Object sensitivity**: Higher `obj_depth` improves precision for object-oriented code
- **Field sensitivity**: Attribute-sensitive analysis is more precise but costlier than field-insensitive
- **Widening**: Use `max_heap_widening` to control heap size and avoid non-termination

### Tuning for Large Codebases

```python
# Configuration for large codebases
config = KCFAConfig(
    k=1,                    # Reduce context sensitivity
    obj_depth=1,            # Reduce object sensitivity  
    field_sensitive=False,  # Disable field sensitivity
    max_heap_widening=500,  # Aggressive widening
    verbose=False           # Disable verbose output
)
```

### Memory Usage

Monitor analysis memory usage through statistics:

```python
results = analysis.results()
print(f"Environment size: {results['env_size']} variable bindings")
print(f"Heap size: {results['heap_size']} field bindings")
print(f"Contexts: {len(results['contexts'])} unique contexts")
print(f"Objects created: {results['statistics']['objects_created']}")
```

## Common Patterns

### Simple Variable Tracking

```python
# Track variables through assignments
code = '''
x = [1, 2, 3]
y = x
z = y[0]
'''

analysis = KCFA2PointerAnalysis()
analysis.plan(transform_to_ir(code))
analysis.initialize()
analysis.run()

results = analysis.results()
# x, y should point to same list object
# z should point to integer object
```

### Function Call Analysis

```python
# Analyze function calls and returns
code = '''
def identity(obj):
    return obj

def main():
    x = [1, 2, 3]
    y = identity(x)
    return y
'''

analysis = KCFA2PointerAnalysis()
analysis.plan(transform_to_ir(code))
analysis.initialize()
analysis.run()

# Check that y points to same object as x
results = analysis.results()
```

### Object-Oriented Code

```python
# Analyze method calls with receivers
code = '''
class Container:
    def __init__(self, value):
        self.value = value
    
    def get_value(self):
        return self.value

def main():
    c = Container([1, 2, 3])
    v = c.get_value()
    return v
'''

config = KCFAConfig(obj_depth=2)  # Use object sensitivity
analysis = KCFA2PointerAnalysis(config)
analysis.plan(transform_to_ir(code))
analysis.initialize()
analysis.run()
```

## Limitations and Known Issues

1. **IR Integration**: The current implementation has limited integration with PythonStAn's IR representation
2. **Builtin Summaries**: Many builtin function summaries are not yet implemented
3. **Exception Handling**: Exception flow is not fully modeled
4. **Generators**: Generator and coroutine analysis is basic
5. **Metaclasses**: Advanced metaprogramming features use conservative approximations

## Debugging Analysis Issues

### Enable Verbose Logging

```python
config = KCFAConfig(verbose=True)
analysis = KCFA2PointerAnalysis(config)
# ... run analysis ...
# Check console output for detailed progress
```

### Inspect Intermediate State

```python
# Access internal analysis state for debugging
env = analysis._env
heap = analysis._heap
contexts = analysis._contexts

print(f"Analysis state:")
print(f"  Environment entries: {len(env)}")
print(f"  Heap entries: {len(heap)}")  
print(f"  Contexts: {len(contexts)}")
```

### Check Statistics

```python
results = analysis.results()
stats = results['statistics']

print(f"Processing statistics:")
print(f"  Objects created: {stats['objects_created']}")
print(f"  Constraints processed: {stats['constraints_processed']}")
print(f"  Calls processed: {stats['calls_processed']}")
```
