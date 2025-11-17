# Diagnostic Analysis Report: Call Edge Coverage Issue

## Executive Summary

**Current Status (5 Flask modules):**
- Call edges: **25** (only module-level functions)
- Unique functions: **245** (includes 128+ class methods)
- Functions with calls: **10** (all module-level functions)
- **Class methods: ZERO call edges**

## Root Cause Analysis

### 1. Method Objects Not Being Created

**Finding:** Only 13 method objects created out of 128+ class methods
- The new method object creation in `_process_load_constraint` isn't triggering
- Most attribute loads don't create method objects
- Class name extraction logic may be failing

### 2. $tmp Variable Points-To Sets

**Finding:** Diagnostic shows:
- Total $tmp variables tracked in env: 252
- $tmp variables with points-to: 20 (shown in diagnostic sample)
- Empty $tmp variables: 0
- **Implication:** Most $tmp variables aren't being tracked in the diagnostic correctly

### 3. Method Reference Tracking

**Finding:**
- Method refs tracked: 329
- Method refs resolved: 182 (55.3%)
- **147 method refs unresolved** (44.7%)

### 4. Unresolved $tmp Calls

**Finding:** 6 $tmp variables with unresolved calls:
- $tmp_167, $tmp_1043, $tmp_1054, $tmp_1074, $tmp_1113, $tmp_1132

These are calls where even the enhanced $tmp resolution failed.

## Key Problems

### Problem 1: Method Object Creation Conditions
```python
# Current code in _process_load_constraint:
if not field_pts.objects and constraint.field not in ["elem", "value", "unknown"]:
    # Create method object...
```

**Issue:** The condition `not field_pts.objects` means we only create method objects when the field is empty. But after MRO resolution, the field may already have objects (e.g., parent class methods), so we skip creation.

**Fix:** Always try to create method objects for attribute loads from class/instance objects, don't condition on empty field_pts.

### Problem 2: Instance vs Class Detection
```python
if 'class:' in obj.alloc_id or 'alloc:' in obj.alloc_id:
```

**Issue:** This is too broad. `'alloc:'` matches ALL allocations (lists, dicts, tuples, etc.), not just instances.

**Fix:** Need better instance detection - perhaps track object types or use allocation patterns.

### Problem 3: Class Name Extraction
```python
if 'class:' in obj.alloc_id:
    parts = obj.alloc_id.split(':')
    if len(parts) >= 3:
        class_name = parts[2]
```

**Issue:** For class allocations like `test.py:110:0:class`, the format is:
- parts[0] = "test.py"
- parts[1] = "110"
- parts[2] = "0"
- parts[3] = "class"

The class name is NOT in the alloc_id!

**Fix:** Need to track class name separately during allocation, or use a different identification strategy.

### Problem 4: No Instance-to-Class Mapping

**Issue:** When we see `obj.method()` where obj is an instance:
- obj has alloc_id like `test.py:123:8:alloc`
- We don't know which class obj is an instance of
- Can't resolve method without knowing the class

**Fix:** Need to track type information - map instance allocations to their classes.

## Required Fixes

### Fix 1: Track Class for Each Instance (CRITICAL)
When creating instances in `_handle_allocation`:
```python
elif alloc_type == "alloc" and "class_name" in event:
    # This is an instance allocation
    instance_class = event["class_name"]
    # Store instance->class mapping
    self._instance_class_map[obj.alloc_id] = instance_class
```

### Fix 2: Store Class Names in Class Objects
When creating class objects:
```python
elif alloc_type == "class":
    class_name = event.get("class_name", target)
    # Store in object metadata or separate map
    self._class_names[obj.alloc_id] = class_name
```

### Fix 3: Enhanced Method Resolution in LoadAttr
```python
def _process_load_constraint(self, ctx, constraint):
    # ... existing code ...
    
    for obj in source_pts.objects:
        # Try MRO resolution first
        field_pts = self._resolve_attribute_with_mro(obj, field, ctx)
        
        # If not found, try creating method object
        if not field_pts.objects:
            # Determine class name
            class_name = None
            if obj.alloc_id in self._class_names:
                # Direct class object
                class_name = self._class_names[obj.alloc_id]
            elif obj.alloc_id in self._instance_class_map:
                # Instance - get its class
                class_name = self._instance_class_map[obj.alloc_id]
            
            if class_name:
                # Look for method in registered functions
                method_full_name = f"{class_name}.{constraint.field}"
                # Try variations: ClassName.method, module.ClassName.method
                for func_name in self._functions:
                    if func_name.endswith(f".{constraint.field}"):
                        if class_name in func_name:
                            # Create method object
                            method_obj = self._create_object(
                                f"method:{func_name}:{obj.alloc_id}", ctx
                            )
                            field_pts = PointsToSet([method_obj])
                            break
        
        target_pts = target_pts.join(field_pts)
```

### Fix 4: Enhanced Call Resolution for $tmp
The current enhancement tries to resolve $tmp through points-to, but needs to handle method objects specially.

## Immediate Action Plan

1. **Add instance-to-class tracking** (CRITICAL)
2. **Fix class name storage and retrieval**
3. **Rewrite method object creation in LoadAttr**
4. **Test with diagnostic output**
5. **Verify call edge improvements**

## Expected Improvement

With these fixes, we should see:
- **Instance method calls resolved**: Flask().__init__(), app.run(), etc.
- **Method-to-method calls discovered**: Internal calls within class methods
- **Call edge coverage**: 500-1000+ edges (10-20x improvement)

## Files to Modify

1. `pythonstan/analysis/pointer/kcfa2/analysis.py`
   - Add `_instance_class_map` and `_class_names` dictionaries
   - Enhance `_handle_allocation` to track instance types
   - Rewrite method object creation in `_process_load_constraint`

2. `pythonstan/analysis/pointer/kcfa2/ir_adapter.py`
   - Ensure class_name is included in allocation events
   - Check if instance allocations include their class

---

*Generated: 2025-10-26*

