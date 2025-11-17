# Unknown Resolution Tracking - Quick Reference

## What It Does

The unknown tracking system identifies and handles unresolved calls and allocations in pointer analysis by:
1. **Creating conservative "unknown" objects** so analysis continues soundly
2. **Logging failures** with detailed warnings (when verbose mode enabled)
3. **Tracking statistics** accessible via the query interface

## Quick Start

### Basic Usage

```python
from pythonstan.analysis.pointer.kcfa import Config, PointerSolver, PointerAnalysisState

# Enable verbose mode to see warnings
config = Config(verbose=True)
state = PointerAnalysisState()
solver = PointerSolver(state, config)

# ... add constraints and solve ...
solver.solve_to_fixpoint()

# Query unknown statistics
query = solver.query()
unknown_summary = query.get_unknown_summary()

print(f"Total unknowns: {unknown_summary['total_unknowns']}")
```

### Get Detailed Report

```python
# Get all unknown records with details
details = query.get_unknown_details()

for unknown in details:
    print(f"{unknown['kind']} at {unknown['location']}: {unknown['message']}")
```

### Integrated Statistics

```python
# Get all statistics including unknowns
stats = query.get_statistics()

print(f"Variables: {stats['num_variables']}")
print(f"Objects: {stats['num_objects']}")
print(f"Unknowns: {stats['total_unknowns']}")
```

## Unknown Categories

The system tracks **9 types** of unknown resolutions:

| Category | Description | Example |
|----------|-------------|---------|
| `callee_empty` | Call with empty points-to set | `func()` where func never assigned |
| `callee_non_callable` | Calling non-callable object | `my_list()` where my_list is a LIST |
| `function_not_in_registry` | Function IR not available | Calling external/library function |
| `missing_dependencies` | IR translator/selector missing | Analysis setup incomplete |
| `dynamic_attribute` | Dynamic attribute access | `getattr(obj, variable_name)` |
| `field_load_empty` | Load from empty field | `obj.attr` where attr not set |
| `import_not_found` | Module import failed | Import resolution failed |
| `alloc_context_failure` | Context selection error | Context selector issue |
| `translation_error` | IR translation exception | Malformed IR or bug |

## Configuration

```python
config = Config(
    verbose=True,            # Enable [UNKNOWN] warnings
    track_unknowns=True,     # Enable tracking (default: True)
    log_unknown_details=False  # Log each unknown immediately (default: False)
)
```

## Query Interface

### Methods Available

```python
query = solver.query()

# Get summary statistics
summary = query.get_unknown_summary()
# Returns: {"total_unknowns": 5, "unknown_callee_empty": 2, ...}

# Get detailed records
details = query.get_unknown_details()
# Returns: [{"kind": "callee_empty", "location": "...", "message": "...", ...}]

# Get all statistics (includes unknowns)
stats = query.get_statistics()
# Returns: {"num_variables": 150, "total_unknowns": 5, ...}
```

## What Happens When Unknown Detected

For each unknown resolution:

1. **Recorded**: Added to unknown tracker with category, location, message
2. **Logged**: Warning printed if `verbose=True`
3. **Conservative Object**: UNKNOWN object created and propagated to target
4. **Analysis Continues**: No crashes, maintains soundness

Example flow for `result = unknown_func()`:

```
1. Solver tries to resolve unknown_func
2. Points-to set is empty
3. Records: UnknownKind.CALLEE_EMPTY at call site
4. Logs: [UNKNOWN] Empty callee at test.py:42: unknown_func
5. Creates: AllocKind.UNKNOWN object named "unknown_call_test:42"
6. Propagates: result → {unknown object}
7. Analysis continues with conservative approximation
```

## Real-World Example

Analyzing Flask application:

```python
# Analyze Flask code
solver.solve_to_fixpoint()
query = solver.query()

# Check analysis quality
stats = query.get_statistics()
unknown_rate = stats['total_unknowns'] / stats['num_variables']

if unknown_rate < 0.05:
    print("✓ High quality analysis (< 5% unknowns)")
elif unknown_rate < 0.20:
    print("○ Moderate quality (5-20% unknowns)")
else:
    print("⚠ Many unknowns (> 20%) - check configuration")

# Identify top issues
summary = query.get_unknown_summary()
for kind, count in sorted(summary.items(), key=lambda x: x[1], reverse=True):
    if kind != 'total_unknowns' and count > 10:
        print(f"  Common issue: {kind} ({count} occurrences)")
```

## Common Scenarios

### Scenario: Empty Callee

**Code:**
```python
def foo():
    result = bar()  # bar never defined or imported
```

**Tracking:**
- Category: `callee_empty`
- Warning: `[UNKNOWN] Empty callee at foo:2: bar`
- Result: `result` points to unknown object

### Scenario: Non-Callable

**Code:**
```python
my_list = [1, 2, 3]
result = my_list()  # Lists aren't callable
```

**Tracking:**
- Category: `callee_non_callable`
- Warning: `[UNKNOWN] Non-callable at test:2: LIST object`
- Result: `result` points to unknown object

### Scenario: External Function

**Code:**
```python
import numpy as np
result = np.array([1, 2, 3])  # numpy not analyzed
```

**Tracking:**
- Category: `function_not_in_registry` (or `callee_empty` if not allocated)
- Warning: `[UNKNOWN] Function not in registry: array`
- Result: `result` points to unknown object

## Benefits

- ✅ **Soundness**: Analysis doesn't crash on incomplete code
- ✅ **Debuggability**: Know exactly what couldn't be resolved
- ✅ **Quality Metrics**: Quantify analysis completeness
- ✅ **Production Ready**: Graceful degradation on real-world code
- ✅ **Zero Overhead**: Fast path unchanged for successful resolutions

## Files

**Core Implementation:**
- `pythonstan/analysis/pointer/kcfa/unknown_tracker.py` - Tracking infrastructure
- `pythonstan/analysis/pointer/kcfa/object.py` - UNKNOWN allocation kind
- `pythonstan/analysis/pointer/kcfa/solver.py` - Integration with solver
- `pythonstan/analysis/pointer/kcfa/config.py` - Configuration options

**Tests:**
- `tests/pointer/kcfa/test_unknown_tracking.py` - 12 tests (all passing)

**Demos:**
- `demo_unknown_tracking.py` - Interactive demonstration

## See Also

- `UNKNOWN_TRACKING_IMPLEMENTATION_COMPLETE.md` - Full implementation details
- `PHASE5_IMPLEMENTATION_COMPLETE.md` - Phase 5 complete feature list

