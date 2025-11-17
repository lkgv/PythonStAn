# Context Sensitivity Bug Fix Report

## Date: October 25, 2025

## Summary

Fixed critical bugs preventing different context-sensitive policies from producing different analysis results. The analysis now correctly creates and uses distinct contexts for different k-CFA policies.

## Bugs Fixed

### Bug #1: Constraint Processing Always Used Empty Context
**Location:** `pythonstan/analysis/pointer/kcfa2/analysis.py`, line 664-668 (old)

**Problem:**
```python
def _process_constraint(self, constraint) -> bool:
    # ...
    if constraint.context == "[]":
        ctx = Context()  # Empty context
    else:
        ctx = Context()  # Default to empty context for simplicity  ❌
```

The method was hardcoded to always use `Context()` (the basic empty context) instead of parsing the constraint's context string back into the proper context object.

**Fix:**
```python
def _process_constraint(self, constraint) -> bool:
    # ...
    # Find the context object matching the constraint's context string
    ctx = None
    context_str = constraint.context
    
    for candidate_ctx in self._contexts:
        if str(candidate_ctx) == context_str:
            ctx = candidate_ctx
            break
    
    if ctx is None:
        ctx = self._context_selector.empty_context()
        if ctx not in self._contexts:
            self._contexts.add(ctx)
```

### Bug #2: Call Processing Always Used Empty Context
**Location:** `pythonstan/analysis/pointer/kcfa2/analysis.py`, line 998 (old)

**Problem:**
```python
def _process_call(self, call: CallItem) -> bool:
    # ...
    caller_ctx = self._context_selector.empty_context()  # ❌ Hardcoded!
```

Same issue as Bug #1 - the method ignored the actual caller context from the `CallItem`.

**Fix:**
```python
def _process_call(self, call: CallItem) -> bool:
    # ...
    # Find the caller context object matching the call's context string
    caller_ctx = None
    context_str = call.caller_ctx
    
    for candidate_ctx in self._contexts:
        if str(candidate_ctx) == context_str:
            caller_ctx = candidate_ctx
            break
    
    if caller_ctx is None:
        caller_ctx = self._context_selector.empty_context()
        if caller_ctx not in self._contexts:
            self._contexts.add(caller_ctx)
```

### Bug #3: Function Names Didn't Match Call Targets
**Location:** 
- `pythonstan/ir/ir_statements.py`, line 1021 (typo)
- `pythonstan/analysis/pointer/kcfa2/analysis.py`, lines 76-77, 84-85, 89-90 (registration)

**Problem:**
1. Missing closing bracket in `get_name()`:
   ```python
   return f'<function {self.name}'  # ❌ Missing '>'
   ```

2. Functions registered with decorated names:
   ```python
   self._functions[func.get_name()] = func  # '<function identity' ❌
   ```
   But calls used simple names:
   ```python
   callee_symbol: 'identity'  # ✓
   ```
   Result: No calls were ever resolved!

**Fix:**
1. Fixed typo:
   ```python
   return f'<function {self.name}>'  # ✓
   ```

2. Changed registration to use simple names:
   ```python
   self._functions[func.name] = func  # 'identity' ✓
   ```

## Verification Results

### Test File: `test_with_calls.py`
Simple Python file with 4 functions and multiple call sites.

**Before Fix:**
- 0-CFA: 1 context, 75% precision
- 2-CFA: 1 context, 75% precision ❌ **IDENTICAL!**
- Call edges: 0

**After Fix:**
- 0-CFA: 1 context, 75% precision
- 2-CFA: 9 contexts, 100% precision ✅ **DIFFERENT!**
- Call edges: 0-CFA: 6, 2-CFA: 8
- Context examples:
  - `[]` (empty)
  - `[test.py:23:9:call#0]` (1-level)
  - `[test.py:23:9:call#0 → test.py:9:13:call#0]` (2-level)

### Key Metrics Comparison

| Metric | 0-CFA | 2-CFA | Notes |
|--------|-------|-------|-------|
| Total Contexts | 1 | 9 | ✅ Different |
| Env Entries | 4 | 9 | ✅ Context-sensitive tracking |
| Call Edges | 6 | 8 | ✅ More edges with deeper analysis |
| Precision | 75.0% | 100.0% | ✅ 2-CFA is more precise |

## Root Cause Analysis

The bugs stemmed from an incomplete implementation of context-sensitive analysis:

1. **Context objects were created correctly** by the `ContextSelector`
2. **Constraints and calls were created with context strings** (e.g., `context=str(ctx)`)
3. **BUT** when processing constraints/calls, the code didn't parse these strings back into context objects
4. **ADDITIONALLY** function names didn't match between registration and call resolution

This meant that even though the infrastructure supported multiple contexts, the analysis always fell back to using the empty context.

## Impact

### What Now Works
✅ Different k-CFA policies create different numbers of contexts
✅ Context-sensitive policies track variables per-context
✅ Inter-procedural calls create new contexts based on policy
✅ Call graph edges are properly created
✅ Precision varies across policies (as theoretically expected)

### What This Enables
- Meaningful comparison of context-sensitive policies
- Accurate benchmarking of precision/performance tradeoffs
- Validation of k-CFA theory on Python code
- Foundation for hybrid policies (obj-sensitive, type-sensitive, etc.)

## Testing Recommendations

To verify the fix on any codebase:

```bash
# Run with different policies
python benchmark/analyze_real_world.py <project> --max-modules 5 --k 0
python benchmark/analyze_real_world.py <project> --max-modules 5 --k 2

# Compare results - should see:
# - Different context counts (0-cfa=1, 2-cfa>>1)
# - Different precision metrics
# - Different call graph sizes
```

## Files Modified

1. `pythonstan/analysis/pointer/kcfa2/analysis.py`
   - Fixed `_process_constraint()` (lines 657-677)
   - Fixed `_process_call()` (lines 992-1012)
   - Fixed `plan()` to use `func.name` instead of `func.get_name()` (lines 75-91)

2. `pythonstan/ir/ir_statements.py`
   - Fixed typo in `IRFunc.get_name()` (line 1021)

## Next Steps

The user requested two tasks:

1. ✅ **TASK 1: Debug Why Results Are Identical** - COMPLETE
   - Found and fixed 3 bugs
   - Verified fix with test cases
   - Different policies now produce different results

2. ⏭️ **TASK 2: Comprehensive Analysis with Dependencies**
   - Expand analysis to include library dependencies
   - Enhanced metrics collection
   - Per-module breakdowns
   - Memory profiling

## Conclusion

The context sensitivity infrastructure was correctly implemented, but three critical bugs in context handling and function registration prevented it from working. All bugs have been fixed and verified.

The analysis now correctly implements context-sensitive pointer analysis with configurable policies, enabling meaningful comparison of different context abstraction strategies.

