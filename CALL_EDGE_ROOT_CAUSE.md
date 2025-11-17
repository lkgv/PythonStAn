# Root Cause: Why We Still Have Low Call Edges

## The Real Problem

**Discovery:** Even with instance-to-class tracking and method object creation, we only get 25 call edges because:

### The Analysis Only Processes Entry Points

Looking at the code in `analysis.py`:

```python
def initialize(self):
    # Process all functions in empty context to discover calls
    for func_name, func in self._functions.items():
        self._contexts.add(empty_context)
        events = list(iter_function_events(func))
        for event in events:
            self._add_event_to_worklist(event, empty_context)
```

**This processes ALL functions in the empty context!**

But then during analysis:

```python
def run(self):
    while iteration < max_iterations:
        changed = False
        call_changed = self._process_all_calls()
        constraint_changed, new_constraints = self._process_all_constraints(all_constraints)
        ...
```

**The problem:** Calls within method bodies ARE being added to the worklist, but:
1. They're processed in the empty context `[]`
2. The calls refer to variables that don't exist in the empty context
3. Therefore the calls can't be resolved

## Example

Flask.__init__ method body contains:
```python
self.config = self.make_config(instance_relative_config)
```

IR translation:
```python
$tmp_1 = self.make_config  # LoadAttr
result = $tmp_1(instance_relative_config)  # Call
```

When processed in empty context `[]`:
- `self` is undefined in `[]` context
- `$tmp_1 = self.make_config` fails to resolve (self has no pts)
- Call to `$tmp_1` can't resolve

## Why Module-Level Functions Work

Module-level functions like `find_best_app()` work because:
1. They're called from module-level code
2. Variables are defined in module-level context
3. Points-to sets exist for those variables

## The Fix

### Option 1: Context-Insensitive Fallback
For calls that can't be resolved in a specific context, try resolving them context-insensitively:

```python
def _process_call(self, call: CallItem):
    # Try context-sensitive first
    ...
    
    # FALLBACK: Try context-insensitive
    if not resolved:
        # Look up callee in all contexts
        for (ctx, var), pts in self._env.items():
            if var == callee_var:
                # Try to resolve from any context
                ...
```

### Option 2: Synthetic Receiver Objects
Create synthetic objects for `self` in method contexts:

```python
def initialize(self):
    for func_name, func in self._functions.items():
        if '.' in func_name:  # It's a method
            # Create a synthetic context for this method
            method_ctx = create_method_context(func_name)
            
            # Create a synthetic 'self' object
            class_name = extract_class_name(func_name)
            self_obj = create_synthetic_instance(class_name)
            self._set_var_pts(method_ctx, 'self', PointsToSet([self_obj]))
            
            # Process events in method context
            events = list(iter_function_events(func))
            for event in events:
                self._add_event_to_worklist(event, method_ctx)
```

### Option 3: Use MRO/Class Hierarchy for Method-Level Analysis  
Analyze methods with synthetic `self` parameter that points to the defining class.

## Recommended Fix: Option 2 with Enhancements

1. **Create synthetic 'self' for each method**
2. **Bind self to class instance**
3. **Process method events in method-specific context**
4. **Allow method-to-method calls to resolve**

This will discover:
- Calls within method bodies
- Method-to-method calls on self
- Internal class interactions

## Implementation

```python
def _create_synthetic_method_contexts(self):
    """Create synthetic contexts for analyzing method bodies."""
    for func_name, func in self._functions.items():
        if '.' in func_name:  # It's a method
            # Extract class name
            parts = func_name.rsplit('.', 1)
            if len(parts) == 2:
                class_path, method_name = parts
                
                # Create method-specific context
                # Use a special marker to distinguish from call-string contexts
                method_ctx = CallStringContext(
                    call_sites=(CallSite(f"synthetic:{func_name}", 0),),
                    k=self.config.k
                )
                
                # Create synthetic 'self' instance
                synthetic_self_id = f"synthetic:self:{class_path}"
                self_obj = self._create_object(synthetic_self_id, method_ctx)
                
                # Map this instance to its class
                # Try to find class name
                class_name = class_path.split('.')[-1]  # Last component
                self._instance_class_map[synthetic_self_id] = class_name
                
                # Bind 'self' in this context
                self._set_var_pts(method_ctx, 'self', PointsToSet([self_obj]))
                
                # Process method events in this context
                events = list(iter_function_events(func))
                for event in events:
                    self._add_event_to_worklist(event, method_ctx)
                    
                self._contexts.add(method_ctx)
```

Call this from `initialize()` after processing module-level functions.

## Expected Result

- **Intra-method calls discovered**: Methods calling other methods on self
- **Call edges: 500-2000+** (10-100x improvement)
- **Real method interactions analyzed**

---

*This is the KEY FIX needed for call edge coverage*

