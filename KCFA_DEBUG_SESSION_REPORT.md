# k-CFA Pointer Analysis Debug Session Report

**Date:** October 25, 2025  
**Task:** Investigate why different k-CFA values produce identical results  
**Status:** 🔄 IN PROGRESS - 5/6 critical bugs fixed, 1 remaining

---

## Executive Summary

Investigated the reported issue that different k-CFA policies (0-cfa, 1-cfa, 2-cfa, 3-cfa) produce identical or nearly identical precision results, which is theoretically impossible. Found and fixed **5 critical bugs** in the pointer analysis implementation. One remaining issue with parameter passing needs resolution before the analysis will work correctly.

---

## Bugs Found & Fixed ✅

### 1. ✅ Constants Not Allocated (CRITICAL)
**File:** `pythonstan/analysis/pointer/kcfa2/ir_adapter.py`

**Problem:** Constants like `$const_0`, `$const_1` were referenced in copy operations (`a = $const_0`) but never allocated as objects. The IR transformation converts `a = 1` into `a = $const_0`, but no allocation event was created for `$const_0`.

**Fix:** Added logic in `IRCopy` event processing (lines 647-660) to detect constants and generate allocation events:
```python
if isinstance(source, str) and source.startswith('$const'):
    # Generate allocation event for the constant
    const_site_id = f"{site_id_of(instr, 'alloc')}_{source}"
    events.append(AllocEvent(...))
```

**Impact:** Constants now properly allocated. Variables that use constants can now get points-to sets.

---

### 2. ✅ Allocation Events Skipped in New Contexts (CRITICAL)
**File:** `pythonstan/analysis/pointer/kcfa2/analysis.py`

**Problem:** Lines 1071-1073 (and 2 other locations) had logic that skipped allocation events when processing functions in new contexts:
```python
# BEFORE:
if event["kind"] != "alloc":
    self._add_event_to_worklist(event, callee_ctx)
```

This meant all allocations stayed in the empty context, breaking context sensitivity.

**Fix:** Removed the skip logic - now ALL events (including allocations) are processed in each calling context:
```python
# AFTER:
for event in events:
    # Process ALL events in the callee context (including allocations)
    self._add_event_to_worklist(event, callee_ctx)
```

**Impact:** Allocations now happen in the correct calling context, not just empty context.

---

### 3. ✅ Return Events Not Handled (CRITICAL)
**File:** `pythonstan/analysis/pointer/kcfa2/analysis.py`

**Problem:** The `_add_event_to_worklist` method had no case for `event["kind"] == "return"`. Return events were completely ignored, so return values were never propagated from callee to caller.

**Fix:** Added return event handling (lines 437-445):
```python
elif event["kind"] == "return":
    # Handle return statement: copy return value to special 'return' variable
    if "value" in event and event["value"]:
        self._constraint_worklist.add_copy_constraint(
            source=event["value"],
            target="return",
            context=str(ctx),
            site_id=f"return_{event['value']}"
        )
```

**Impact:** Return values now propagated via a special `'return'` variable that `_handle_return_value` can read.

---

### 4. ✅ Fixpoint Iteration Incomplete (MAJOR)
**File:** `pythonstan/analysis/pointer/kcfa2/analysis.py`

**Problem:** The analysis wasn't iterating to fixpoint properly. Constraints were processed once, but when their input variables got values later (e.g., from return values), the constraints weren't re-evaluated.

**Fix:** Added logic to re-process all constraints in subsequent iterations (lines 191-218):
```python
# Keep track of all constraints that need repeated processing
all_constraints = []

while iteration < max_iterations:
    # ...process constraints...
    
    # If something changed, re-add all constraints for next iteration
    if changed and self._constraint_worklist.empty():
        for constraint in all_constraints:
            self._constraint_worklist._queue.append(constraint)
```

**Impact:** Constraints now re-evaluated when inputs change, allowing proper fixpoint computation.

---

### 5. ✅ Cross-Context Fallback Too Broad (MAJOR)
**File:** `pythonstan/analysis/pointer/kcfa2/analysis.py`

**Problem:** Lines 708-714 had a fallback that searched ALL contexts when a variable wasn't found in the specified context. This completely breaks context sensitivity! If `x` doesn't exist in context `[call1]`, it would search other contexts and use `x` from `[call2]`, making results identical across policies.

**Fix:** Limited the fallback to only module-level variables (lines 707-722):
```python
# Only search for module-level variables (constants, globals)
# Local variables should only exist in their defining context
is_module_level = (
    constraint.source.startswith('$const') or  # Constants
    constraint.source.startswith('$global') or  # Globals
    constraint.source.isupper()  # Convention: UPPERCASE = global
)
if is_module_level:
    for search_ctx in self._contexts:
        # search...
```

**Impact:** Local variables no longer leak across contexts. Only truly global/module-level vars are shared.

---

## Remaining Issues ❌

### 6. ❌ Parameter Passing Not Working (CRITICAL)
**Status:** Under investigation

**Problem:** When a function is called with arguments, parameter passing doesn't seem to propagate values correctly. For example:
- `caller1()` has `a = 1` (works, `a@[]` has 1 object)
- `caller1()` calls `identity(a)` 
- Parameter passing should copy `a@[]` -> `x@[call#0]`
- But `x@[call#0]` remains empty!

**Symptoms:**
```
[CALL] direct: identity @[] -> target=$tmp_0
[CONSTRAINT] copy: x -> return @[call#0]
  Source x is empty    ← Parameter should have value!
  Changed: False
```

**Investigation Needed:**
1. Is `_handle_parameter_passing` actually being called?
2. Is it finding the correct argument values?
3. Is it setting them in the correct callee context?
4. Is there a timing issue where constraints run before parameter passing completes?

**Current Hypothesis:** The function's parameter (`x`) is never getting the value from the argument (`a`) during parameter passing. Need to add debug logging to `_handle_parameter_passing` to trace execution.

---

## Test Results

### Before All Fixes:
```
0-CFA: 1 context, 4 env entries, 75% precision
2-CFA: 1 context, 4 env entries, 75% precision  ❌ IDENTICAL
```

### After Fixes 1-5:
```
0-CFA: 1 context, 2 env entries, 100% precision
2-CFA: 2 contexts, 2 env entries, 100% precision

Contexts created: ✅ Different (1 vs 2)
Env entries: ⚠️  Same (only constants tracked)
Variables tracked: ❌ Most variables missing (a, b, x, $tmp_0 all empty)
```

**Diagnosis:** Context creation works, but variables aren't being tracked due to parameter passing failure.

---

## What's Working Now

✅ Context creation (different policies create different contexts)  
✅ Context selection at call sites  
✅ Call graph construction  
✅ Constant allocation  
✅ Return event processing  
✅ Fixpoint iteration framework  
✅ Limited cross-context fallback (module-level only)  

---

## What's Still Broken

❌ Parameter passing (arguments not reaching parameters)  
❌ Variables tracking (most variables have empty points-to sets)  
❌ Inter-procedural flow (values don't flow through calls)  

---

## Files Modified

1. **`pythonstan/analysis/pointer/kcfa2/ir_adapter.py`**
   - Lines 647-660: Added constant allocation in IRCopy

2. **`pythonstan/analysis/pointer/kcfa2/analysis.py`**
   - Lines 437-445: Added return event handling
   - Lines 191-218: Added fixpoint iteration logic
   - Lines 707-722: Limited cross-context fallback
   - Lines 739-750: Limited cross-context fallback (load constraints)
   - Lines 1071-1073 (and 2 more locations): Removed allocation event skipping

---

## Next Steps

1. **Debug parameter passing** - Add logging to `_handle_parameter_passing` to see why arguments aren't reaching parameters
2. **Fix parameter passing** - Once root cause identified, implement fix
3. **Comprehensive test** - Run end-to-end test with multiple functions and call chains
4. **Real-world benchmarks** - Test on Flask/Werkzeug to verify different k values produce different results

---

## Verification Commands

```bash
# Quick test of fixes so far
python debug_detailed_iteration.py

# Check context creation
python debug_call_discovery.py

# Detailed environment inspection
python debug_env_details.py
```

---

## Assessment

**Progress:** 83% complete (5/6 critical bugs fixed)  
**Confidence:** High - The fixes address real bugs, but one critical issue remains  
**Risk:** Medium - Parameter passing is fundamental; if it can't be fixed, major refactoring may be needed

---

**Recommendation:** Focus on debugging and fixing parameter passing, then run comprehensive tests.

