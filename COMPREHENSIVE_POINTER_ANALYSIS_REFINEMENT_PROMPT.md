# Comprehensive k-CFA Pointer Analysis Refinement Task

## Executive Summary

You need to complete the k-CFA pointer analysis implementation in PythonStAn by:
1. Populating missing event fields (elements, values, bases, closures, etc.)
2. Implementing hybrid __dict__ attribute access model  
3. Building ClassHierarchy during analysis with C3 MRO resolution
4. Using pointer analysis to resolve temporary variable names and trace object identity
5. Handling Python's dynamic features through deep memory context exploration

**Estimated Complexity:** High - requires deep understanding of Python semantics, pointer analysis theory, and PythonStAn's IR representation.

---

## Current State Analysis

### What Works ✅
- **Core Infrastructure:** Worklist-based fixpoint iteration, context management, points-to dataflow
- **Basic Allocation Handling:** All allocation types (const, list, dict, obj, func, class, exc, method, genframe) have field initialization logic
- **Constructor Detection:** Sophisticated `_is_constructor_call()` distinguishes constructors from pure functions
- **Test Coverage:** 4/4 advanced test cases pass

### Critical Problems ❌

#### Problem 1: Event Fields Never Populated
**Location:** `pythonstan/analysis/pointer/kcfa2/ir_adapter.py`, `_process_ir_instruction()`

**Issue:** AllocEvent is created but optional fields are NEVER populated:
- Line 301 in analysis.py checks `if "elements" in event` - but IRAssign/IRCall never add "elements"
- Line 316 checks `if "values" in event` - never populated
- Line 348 checks `if "closure_vars" in event` - never populated  
- Line 372 checks `if "bases" in event` - never populated (CRITICAL for inheritance)
- Line 422 checks `if "func_binding" in event` - never populated
- Line 441 checks `if "yield_binding" in event` - never populated

**What Needs to Happen:**
In `ir_adapter.py`, when generating AllocEvent:

```python
# For ast.List in IRAssign (lines 393-394)
elif isinstance(rval, ast.List):
    alloc_type = 'list'
    # EXTRACT ELEMENTS - currently missing!
    elements = []
    for elt in rval.elts:
        if hasattr(elt, 'id'):
            elements.append(elt.id)
    # Add to event:
    events.append(AllocEvent(..., elements=elements))
```

Similar logic needed for:
- **Dict values:** Extract from ast.Dict.values
- **Tuple/Set elements:** Extract from ast.Tuple.elts, ast.Set.elts
- **Closures:** Extract from IRFunc.cell_vars (already computed by ClosureAnalysis!)
- **Bases:** Extract from IRClass.bases
- **Method bindings:** Track receiver when method access happens
- **Generator yields:** Track yield expressions in generator functions

#### Problem 2: __dict__ Field Never Used
**Location:** `pythonstan/analysis/pointer/kcfa2/analysis.py`, lines 326-455

**Issue:** `_handle_allocation()` initializes `__dict__` field for obj/func/class types, but `_process_constraint()` (lines 486-553) NEVER accesses it. Attribute loads/stores go directly to `attr_key("name")` instead of going through `__dict__`.

**Current Behavior (Wrong for Python semantics):**
```
obj -> attr_key("name") -> points_to_set
```

**Should Be (Hybrid Model):**
```
# Known attributes (from type info)
obj -> attr_key("name") -> points_to_set

# Dynamic attributes (runtime setattr, getattr, unknown names)
obj -> attr_key("__dict__") -> dict_obj -> value_key() -> points_to_set
```

**What Needs to Happen:**
1. In `_process_constraint()` for load/store with `field == "unknown"`:
   - Don't use `unknown_attr_key()` 
   - Instead: load/store through `__dict__` indirection
2. Track when attributes are set dynamically (setattr calls)
3. Fall back to `__dict__` when attribute name is not statically known

#### Problem 3: ClassHierarchy Not Built
**Location:** `pythonstan/analysis/pointer/kcfa2/analysis.py`

**Issue:** World has `ClassHierarchy` object (pythonstan/world/class_hierarchy.py), but pointer analysis never populates it. Without hierarchy:
- Method resolution doesn't follow inheritance chain
- Multiple inheritance isn't handled
- Attribute lookups don't check base classes

**What Needs to Happen:**
1. Add `build_class_hierarchy: bool = True` option to `KCFAConfig`
2. In `_handle_allocation()` for `alloc_type == "class"`:
   ```python
   if self.config.build_class_hierarchy and "bases" in event:
       for base_name in event["bases"]:
           # Resolve base_name to actual class object via points-to
           base_pts = self._get_var_pts(ctx, base_name)
           for base_obj in base_pts.objects:
               # Add to hierarchy
               self.class_hierarchy.add_inheritance(
                   alloc_id,  # subclass
                   base_obj.alloc_id  # base class
               )
   ```
3. Store ClassHierarchy in analysis results for later queries

#### Problem 4: C3 MRO Not Implemented
**Location:** NEW FILE needed: `pythonstan/analysis/pointer/kcfa2/mro.py`

**Issue:** Python uses C3 linearization for Method Resolution Order in multiple inheritance. Without this:
- `super()` calls resolve incorrectly
- Diamond inheritance patterns break
- Method lookup follows wrong order

**What Needs to Happen:**
Implement C3 linearization algorithm:

```python
def compute_mro(class_obj: AbstractObject, 
                class_hierarchy: ClassHierarchy) -> List[AbstractObject]:
    """Compute C3 Method Resolution Order for a class.
    
    Algorithm:
    1. MRO(C) = [C] + merge(MRO(B1), MRO(B2), ..., [B1, B2, ...])
    2. merge() selects head of first list if it doesn't appear 
       in tail of any other list
    3. Repeat until all lists exhausted
    
    Raises:
        TypeError: If inheritance graph is inconsistent
    """
    # Implementation here
```

Use this in attribute load/store to search through MRO chain.

#### Problem 5: Temporary Variable Name Resolution
**Location:** Throughout IR, especially in `site_id_of()` and analysis results

**Issue:** PythonStAn IR uses temporary variables extensively:
```python
tmp_10 = Dog("Buddy")  # Should know tmp_10 is really a Dog instance
tmp_20 = Cat          # Should know tmp_20 is the Cat class
tmp_30 = tmp_10.bark  # Should know this is a bound method
```

**What Needs to Happen:**
1. **Track Assignment Chains:**
   ```python
   def resolve_actual_name(var_name: str, ctx: Context) -> Optional[str]:
       """Trace assignment chain to find actual object name.
       
       tmp_10 = Dog  -> returns "Dog"
       tmp_20 = tmp_10 -> returns "Dog" (transitively)
       x = y = z = Dog -> all return "Dog"
       """
   ```

2. **Use Allocation Site for Identity:**
   - Allocation site encodes source location: "file.py:42:10:obj"
   - Parse to extract actual class name from source context
   - Store mapping: temp_var -> actual_name

3. **Annotate Results:**
   ```python
   {
       "points_to": {
           "tmp_10@[]": ["file.py:42:10:obj"],
           "tmp_10@[]#actual_name": "Dog",  # Resolved name
           "tmp_10@[]#actual_class": "Dog"  # Resolved class
       }
   }
   ```

4. **Enhance site_id_of():**
   - Extract class name from IRClass.name
   - Extract function name from IRFunc.name  
   - Include in allocation site ID for readability

---

## Implementation Plan

### Phase 1: Populate Event Fields (HIGH PRIORITY)

**File:** `pythonstan/analysis/pointer/kcfa2/ir_adapter.py`

**Task 1.1: Extract Container Elements**
In `_process_ir_instruction()`, for IRAssign with container literals:

```python
elif isinstance(rval, ast.List):
    alloc_type = 'list'
    elements = []
    if hasattr(rval, 'elts'):
        for elt in rval.elts:
            if hasattr(elt, 'id'):
                elements.append(elt.id)
            elif isinstance(elt, ast.Constant):
                # Handle constant elements
                elements.append(f"$const_{elt.value}")
    
    events.append(AllocEvent(
        kind="alloc",
        alloc_id=site_id,
        target=target,
        type=alloc_type,
        elements=elements if elements else None,  # Only add if non-empty
        recv_binding=None,
        bb=block_id,
        idx=instr_idx
    ))
```

Repeat for:
- `ast.Tuple` -> extract elts
- `ast.Set` -> extract elts  
- `ast.Dict` -> extract values (keys are abstracted away)

**Task 1.2: Extract Class Bases**
In `_process_ir_instruction()`, for IRClass/IRFunc (lines 595-606):

```python
elif isinstance(instr, (IRFunc, IRClass)):
    site_id = site_id_of(instr, 'func' if isinstance(instr, IRFunc) else 'class')
    name = instr.name if hasattr(instr, 'name') else 'unknown'
    
    # Extract additional fields
    extra_fields = {}
    
    if isinstance(instr, IRClass):
        # Extract bases from IRClass.bases
        if hasattr(instr, 'bases') and instr.bases:
            bases = []
            for base_expr in instr.bases:
                if isinstance(base_expr, ast.Name):
                    bases.append(base_expr.id)
                elif isinstance(base_expr, ast.Attribute):
                    # Handle qualified names like "module.ClassName"
                    bases.append(ast.unparse(base_expr))
            extra_fields['bases'] = bases if bases else None
    
    elif isinstance(instr, IRFunc):
        # Extract closure variables from IRFunc.cell_vars
        if hasattr(instr, 'cell_vars') and instr.cell_vars:
            extra_fields['closure_vars'] = list(instr.cell_vars)
    
    events.append(AllocEvent(
        kind="alloc",
        alloc_id=site_id,
        target=name,
        type='func' if isinstance(instr, IRFunc) else 'class',
        recv_binding=None,
        bb=block_id,
        idx=instr_idx,
        **extra_fields  # Add bases or closure_vars dynamically
    ))
```

**Task 1.3: Track Method Bindings and Generator Yields**
These require analyzing attr_load events and yield statements:

```python
# When processing IRLoadAttr on a method
if is_method_access(obj_type, attr_name):
    # Generate method allocation event
    method_site_id = site_id_of(instr, 'method')
    events.append(AllocEvent(
        kind="alloc",
        alloc_id=method_site_id,
        target=target,
        type='method',
        recv_binding=obj,  # Receiver object
        func_binding=attr,  # Method name
        bb=block_id,
        idx=instr_idx
    ))
```

### Phase 2: Implement Hybrid __dict__ Model (MEDIUM PRIORITY)

**File:** `pythonstan/analysis/pointer/kcfa2/analysis.py`

**Task 2.1: Modify _process_constraint() for Unknown Attributes**

```python
elif constraint.constraint_type == "load":
    # Load constraint: target = source.field
    source_pts = self._get_var_pts(ctx, constraint.source)
    target_pts = PointsToSet()
    
    # Determine field key
    if constraint.field == "unknown":
        # Dynamic attribute access - go through __dict__
        dict_field = attr_key("__dict__")
        for obj in source_pts.objects:
            dict_pts = self._get_field_pts(obj, dict_field)
            # dict_pts contains dictionary objects
            for dict_obj in dict_pts.objects:
                # Load all values from the dictionary
                value_field = value_key()
                field_pts = self._get_field_pts(dict_obj, value_field)
                target_pts = target_pts.join(field_pts)
    else:
        # Known attribute name - direct access
        field = attr_key(constraint.field)
        for obj in source_pts.objects:
            field_pts = self._get_field_pts(obj, field)
            target_pts = target_pts.join(field_pts)
    
    if self._set_var_pts(ctx, constraint.target, target_pts):
        changed = True
```

Similar logic for store constraints.

**Task 2.2: Handle setattr/getattr Specially**

When processing IRCall for setattr/getattr, generate appropriate attr_store/attr_load events with field="unknown".

### Phase 3: Build ClassHierarchy with C3 MRO (HIGH PRIORITY)

**File:** `pythonstan/analysis/pointer/kcfa2/config.py`

```python
@dataclass
class KCFAConfig:
    # ... existing fields ...
    build_class_hierarchy: bool = True
    use_mro: bool = True  # Enable MRO-based attribute resolution
```

**File:** `pythonstan/analysis/pointer/kcfa2/analysis.py`

```python
class KCFA2PointerAnalysis:
    def __init__(self, config: KCFAConfig):
        # ... existing initialization ...
        
        if self.config.build_class_hierarchy:
            from pythonstan.world import World
            self.class_hierarchy = World.class_hierarchy
            # Or create a local one:
            # from pythonstan.world.class_hierarchy import ClassHierarchy
            # self.class_hierarchy = ClassHierarchy()
        else:
            self.class_hierarchy = None
```

**Task 3.1: Populate Hierarchy During Allocation**

In `_handle_allocation()` for alloc_type == "class":

```python
elif alloc_type == "class":
    # ... existing field initialization ...
    
    # Build class hierarchy
    if self.config.build_class_hierarchy and self.class_hierarchy:
        # Register this class
        class_name = alloc_id  # Use allocation site as class identifier
        
        if "bases" in event and event["bases"]:
            for base_name in event["bases"]:
                # Resolve base_name to its allocation site via points-to
                base_pts = self._get_var_pts(ctx, base_name)
                if base_pts.objects:
                    for base_obj in base_pts.objects:
                        self.class_hierarchy.add_subclass(
                            base_obj.alloc_id,  # parent class
                            class_name  # child class
                        )
```

**File:** NEW `pythonstan/analysis/pointer/kcfa2/mro.py`

```python
"""Method Resolution Order (MRO) computation using C3 linearization."""

from typing import List, Set, Tuple
from .model import AbstractObject

def compute_c3_mro(class_obj: AbstractObject, 
                   class_hierarchy) -> List[str]:
    """Compute C3 linearization (MRO) for a class.
    
    Python's MRO algorithm ensures:
    1. Children come before parents
    2. Parent order is preserved
    3. Diamond patterns are handled correctly
    
    Args:
        class_obj: Abstract object representing the class
        class_hierarchy: ClassHierarchy instance
        
    Returns:
        List of class identifiers in MRO order
        
    Raises:
        TypeError: If inheritance graph is inconsistent
    """
    class_id = class_obj.alloc_id
    
    def merge(sequences: List[List[str]]) -> List[str]:
        """Merge sequences using C3 algorithm."""
        result = []
        while True:
            # Remove empty sequences
            sequences = [seq for seq in sequences if seq]
            if not sequences:
                return result
            
            # Find candidate (head that doesn't appear in any tail)
            candidate = None
            for seq in sequences:
                head = seq[0]
                # Check if head appears in any tail
                appears_in_tail = False
                for other_seq in sequences:
                    if head in other_seq[1:]:
                        appears_in_tail = True
                        break
                
                if not appears_in_tail:
                    candidate = head
                    break
            
            if candidate is None:
                # Inconsistent hierarchy
                raise TypeError(f"Cannot create consistent MRO for {class_id}")
            
            result.append(candidate)
            # Remove candidate from all sequences
            for seq in sequences:
                if seq[0] == candidate:
                    seq.pop(0)
        
        return result
    
    # Get direct bases
    bases = class_hierarchy.get_bases_by_id(class_id)
    if not bases:
        # No bases - MRO is just [class_id, object]
        return [class_id, "object"]
    
    # Compute MRO for each base recursively
    base_mros = []
    for base_id in bases:
        base_obj = ... # Lookup base object
        base_mro = compute_c3_mro(base_obj, class_hierarchy)
        base_mros.append(base_mro)
    
    # C3 linearization: L(C) = [C] + merge(L(B1), L(B2), ..., [B1, B2, ...])
    return [class_id] + merge(base_mros + [bases])
```

**Task 3.2: Use MRO in Attribute Resolution**

In `_process_constraint()` for load/store:

```python
def _resolve_attribute_with_mro(self, obj: AbstractObject, 
                                 field: FieldKey) -> PointsToSet:
    """Resolve attribute following MRO chain."""
    if not self.config.use_mro or not self.class_hierarchy:
        # Fall back to direct access
        return self._get_field_pts(obj, field)
    
    # Get MRO for object's class
    mro = compute_c3_mro(obj, self.class_hierarchy)
    
    result = PointsToSet()
    for class_id in mro:
        # Try to find attribute in this class
        class_obj = ... # Lookup class object by ID
        field_pts = self._get_field_pts(class_obj, field)
        if field_pts.objects:
            result = result.join(field_pts)
            break  # Stop at first match (Python behavior)
    
    return result
```

### Phase 4: Temporary Variable Resolution (MEDIUM PRIORITY)

**Task 4.1: Add Name Tracing Infrastructure**

**File:** NEW `pythonstan/analysis/pointer/kcfa2/name_resolution.py`

```python
"""Resolve temporary variable names to actual object identities."""

from typing import Dict, Optional
from .model import AbstractObject
from .context import Context

class NameResolver:
    """Tracks assignment chains to resolve temporary variables."""
    
    def __init__(self):
        # Map: (var_name, context) -> actual_name
        self._name_map: Dict[Tuple[str, str], str] = {}
        # Map: allocation_site -> source_name
        self._alloc_names: Dict[str, str] = {}
    
    def record_assignment(self, target: str, source: str, ctx: Context):
        """Record assignment: target = source."""
        ctx_str = str(ctx)
        
        # If source has a known name, propagate it
        if (source, ctx_str) in self._name_map:
            actual = self._name_map[(source, ctx_str)]
            self._name_map[(target, ctx_str)] = actual
        elif not source.startswith('tmp_'):
            # Source is not a temp - use it as actual name
            self._name_map[(target, ctx_str)] = source
    
    def record_allocation(self, target: str, alloc_site: str, 
                          alloc_type: str, ctx: Context):
        """Record allocation: target = new Object()."""
        ctx_str = str(ctx)
        
        # Extract class name from allocation site if possible
        # Format: "file.py:42:10:obj" or could include class name
        if alloc_type in ('func', 'class'):
            # For functions/classes, target might be the actual name
            if not target.startswith('tmp_'):
                self._name_map[(target, ctx_str)] = target
                self._alloc_names[alloc_site] = target
        else:
            # For objects, try to infer from constructor call
            # This requires tracking the constructor being called
            pass
    
    def resolve(self, var_name: str, ctx: Context) -> Optional[str]:
        """Resolve variable to its actual name."""
        return self._name_map.get((var_name, str(ctx)))
```

**Task 4.2: Integrate into Analysis**

In `KCFA2PointerAnalysis`:

```python
def __init__(self, config: KCFAConfig):
    # ... existing ...
    self.name_resolver = NameResolver()

def _handle_allocation(self, event: Event, ctx: Context):
    # ... existing allocation handling ...
    
    # Track name
    self.name_resolver.record_allocation(
        target=event["target"],
        alloc_site=event["alloc_id"],
        alloc_type=event["type"],
        ctx=ctx
    )

def _process_constraint(self, constraint):
    if constraint.constraint_type == "copy":
        # Track assignment chain
        self.name_resolver.record_assignment(
            target=constraint.target,
            source=constraint.source,
            ctx=ctx
        )
    # ... rest of processing ...
```

**Task 4.3: Annotate Results**

In `results()` method:

```python
def results(self) -> Dict[str, Any]:
    # ... existing results ...
    
    # Add resolved names
    resolved_names = {}
    for (var, ctx_str), pts in self.env_map.items():
        actual_name = self.name_resolver.resolve(var, ctx_from_str(ctx_str))
        if actual_name and actual_name != var:
            resolved_names[f"{var}@{ctx_str}"] = actual_name
    
    results["resolved_names"] = resolved_names
    return results
```

### Phase 5: Dynamic Feature Handling (ADVANCED)

**Task 5.1: Handle __getattr__ and __setattr__**

These magic methods intercept attribute access dynamically:

```python
def _handle_dynamic_attr_access(self, obj: AbstractObject, 
                                 attr_name: str, 
                                 ctx: Context) -> PointsToSet:
    """Handle __getattr__ interception."""
    # Check if object's class defines __getattr__
    if self.config.use_mro and self.class_hierarchy:
        mro = compute_c3_mro(obj, self.class_hierarchy)
        for class_id in mro:
            class_obj = ... # Lookup
            getattr_field = attr_key("__getattr__")
            getattr_pts = self._get_field_pts(class_obj, getattr_field)
            if getattr_pts.objects:
                # __getattr__ is defined - simulate call
                # Result is unknown, return TOP or create new alloc
                return PointsToSet()  # Conservative: unknown
    
    # No __getattr__ - normal lookup
    return self._get_field_pts(obj, attr_key(attr_name))
```

**Task 5.2: Handle Metaclasses**

Python classes are instances of metaclasses:

```python
# When creating class object
if alloc_type == "class":
    # Classes are instances of their metaclass
    if "metaclass" in event:
        metaclass_name = event["metaclass"]
        metaclass_pts = self._get_var_pts(ctx, metaclass_name)
        # Store __class__ relationship
        class_field = attr_key("__class__")
        self._set_field_pts(obj, class_field, metaclass_pts)
```

**Task 5.3: Handle Decorators**

Track decorator applications:

```python
if isinstance(instr, IRFunc) and instr.decorator_list:
    # Decorators wrap functions
    for decorator_expr in instr.decorator_list:
        decorator_name = extract_name(decorator_expr)
        # Simulate: func = decorator(func)
        # Generate synthetic call event
```

---

## Testing Strategy

### Test 1: Verify Event Field Population

Create test case with:
```python
# Test elements extraction
numbers = [1, 2, 3]

# Test bases extraction  
class Animal: pass
class Dog(Animal): pass

# Test closure extraction
def outer(x):
    def inner(y):
        return x + y  # Captures x
    return inner
```

Verify in analysis results that:
- List allocation has `elements` field
- Dog class allocation has `bases=['Animal']`
- inner function allocation has `closure_vars=['x']`

### Test 2: Verify __dict__ Hybrid Model

```python
class Obj:
    pass

o = Obj()
o.known_attr = 42  # Direct field access
setattr(o, "dynamic_attr", 99)  # Should go through __dict__

# Verify both attributes are accessible
print(o.known_attr)
print(getattr(o, "dynamic_attr"))
```

### Test 3: Verify MRO Resolution

```python
class A:
    def method(self): return "A"

class B(A):
    def method(self): return "B"

class C(A):
    def method(self): return "C"

class D(B, C):  # Diamond inheritance
    pass

d = D()
d.method()  # Should resolve to B.method (C3 MRO: D, B, C, A)
```

Verify MRO is computed correctly and method resolution follows it.

### Test 4: Verify Name Resolution

```python
tmp_10 = Dog
tmp_20 = tmp_10
MyDog = tmp_20  # Assignment chain

# Verify all three variables resolve to "Dog" class
```

---

## Key Files to Modify

1. **`pythonstan/analysis/pointer/kcfa2/ir_adapter.py`** (300+ lines)
   - Populate all event fields
   - Extract elements, values, bases, closures

2. **`pythonstan/analysis/pointer/kcfa2/analysis.py`** (200+ lines)
   - Implement hybrid __dict__ model
   - Build ClassHierarchy
   - Integrate MRO resolution
   - Add NameResolver

3. **`pythonstan/analysis/pointer/kcfa2/config.py`** (10 lines)
   - Add build_class_hierarchy and use_mro options

4. **NEW `pythonstan/analysis/pointer/kcfa2/mro.py`** (150 lines)
   - Implement C3 linearization
   - Provide MRO query interface

5. **NEW `pythonstan/analysis/pointer/kcfa2/name_resolution.py`** (100 lines)
   - Track assignment chains
   - Resolve temporary variables

6. **`pythonstan/world/class_hierarchy.py`** (50 lines)
   - Add methods: get_bases_by_id, get_mro, add_subclass_by_id

7. **`pythonstan/analysis/pointer/kcfa2/model.py`** (minor)
   - Extend TypedDict definitions if needed for new event fields

---

## Critical Design Decisions

### 1. Hybrid __dict__ Model
**Decision:** Use direct field access for known attributes, __dict__ indirection for dynamic/unknown.

**Rationale:**
- Precision: Direct access preserves field-sensitivity for statically known fields
- Soundness: __dict__ fallback handles dynamic setattr/getattr
- Performance: Avoids double indirection for common case

### 2. C3 MRO Implementation
**Decision:** Implement full C3 linearization, not simplified approximation.

**Rationale:**
- Python semantics require it for correctness
- Multiple inheritance is common in real code
- Diamond patterns must be handled properly
- super() calls depend on correct MRO

### 3. Name Resolution Strategy
**Decision:** Track assignment chains + use allocation site context.

**Rationale:**
- Temporary variables lose semantic meaning
- Assignment chains reveal actual identity (tmp_10 = Dog)
- Allocation sites encode source location
- Results must be human-readable for usefulness

### 4. Dynamic Feature Handling
**Decision:** Conservative approximation for __getattr__, decorators, metaclasses.

**Rationale:**
- Full precision requires symbolic execution
- Static analysis must remain sound
- Conservative = return TOP or create fresh allocation
- Document limitations clearly

---

## Expected Outcomes

After completion:

1. **Event Fields Populated:** All AllocEvents have appropriate optional fields
2. **Hybrid __dict__ Works:** Dynamic attributes accessed correctly
3. **ClassHierarchy Built:** Inheritance relationships tracked
4. **MRO Computed:** C3 linearization for all classes
5. **Names Resolved:** Temporary variables mapped to actual names
6. **Tests Pass:** All existing tests + new tests for above features

**Success Metrics:**
- Zero missing event fields (no None when data available)
- ClassHierarchy contains all inheritance edges
- MRO matches Python's output for test classes
- >80% of temporary variables resolved to actual names
- Dynamic attribute test cases work correctly

---

## Implementation Order (Recommended)

1. **Phase 1 First** - Event field population (highest ROI, unblocks everything else)
2. **Phase 3 Next** - ClassHierarchy + MRO (critical for correctness)
3. **Phase 2 Then** - Hybrid __dict__ model (improves precision)
4. **Phase 4 After** - Name resolution (improves usability)
5. **Phase 5 Last** - Dynamic features (advanced, can be future work)

---

## Common Pitfalls to Avoid

1. **Don't Skip Event Fields:** Every missing field is a precision loss
2. **Don't Ignore Multiple Inheritance:** C3 MRO is required, not optional
3. **Don't Assume Simple Chains:** Aliases can be complex (x = y = z = ...)
4. **Don't Forget Context:** Same variable in different contexts is different
5. **Don't Hardcode Class Names:** Use allocation sites and points-to, not strings
6. **Test Incrementally:** Don't implement everything before testing

---

## Questions to Resolve Before Starting

1. Should ClassHierarchy be shared (World.class_hierarchy) or analysis-local?
   - **Recommendation:** Analysis-local, copy to World on completion
   
2. How to handle unresolved base classes (forward references)?
   - **Recommendation:** Defer, resolve in second pass after all classes seen
   
3. Should MRO cache be persistent across contexts?
   - **Recommendation:** Yes, MRO is context-insensitive

4. How to present resolved names in results?
   - **Recommendation:** Separate "resolved_names" dict, keep original var names too

---

## Conclusion

This is a complex but well-defined task. The current implementation has solid foundations - you're completing the missing pieces to make it production-ready for real Python code with classes, inheritance, and dynamic features.

**Estimated Effort:** 3-5 days for experienced developer, 1-2 weeks for learning + implementation.

**Priority:** HIGH - Without these features, the analysis cannot handle realistic Python code.

Good luck! The foundation is solid - you're adding the critical missing pieces.

