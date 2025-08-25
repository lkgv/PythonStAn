# IR Semantics for Pointer-Relevant Behaviors

Version: v1

## A. Repository Overview

The PythonStAn codebase provides a comprehensive framework for static analysis of Python programs. The core components relevant to IR and TAC (Three Address Code) transformation are:

- **IR Statements** (`pythonstan/ir/ir_statements.py`): Defines the intermediate representation of Python statements
- **Three Address Transformation** (`pythonstan/analysis/transform/three_address.py`): Converts Python AST to simpler three-address form
- **IR Transformation** (`pythonstan/analysis/transform/ir.py`): Transforms Python AST to IR statements
- **CFG Construction** (`pythonstan/analysis/transform/block_cfg.py`, `pythonstan/analysis/transform/cfg.py`): Builds control flow graphs at block and statement levels
- **CFG Representation** (`pythonstan/graph/cfg/`): Contains the basic blocks, edges, and graph representation

The overall transformation pipeline follows these steps:
1. Source code is parsed into Python AST
2. Three-address code transformation simplifies expressions
3. IR transformation converts to IR statements
4. Block CFG groups IR statements into basic blocks
5. Statement-level CFG creates fine-grained control flow

The main entrypoints to these transformations as defined in `docs/digests/transform-entrypoints.json` are:
- `IR` class in `pythonstan/analysis/transform/ir.py`
- `ThreeAddress` class in `pythonstan/analysis/transform/three_address.py`
- `BlockCFG` class in `pythonstan/analysis/transform/block_cfg.py`
- `CFG` class in `pythonstan/analysis/transform/cfg.py`

## B. Instruction-by-Instruction Semantics

### 1. Allocation/Object Creation

#### Name: `IRAssign` (Object Allocation)
- **Lowers from**: Python `obj = SomeClass()` → TAC `$tmp_1 = SomeClass(); obj = $tmp_1`
- **Reads**: Class name, constructor arguments
- **Writes**: Target variable
- **Allocations**: Object instance with site ID `{file}:{lineno}:{col}:obj`
- **Control-flow**: Normal successor
- **Environment/scope**: New object in current scope
- **Points-to effects**: Target variable points to newly allocated object
- **Edge cases**: Dynamic class name (variable); class with `__new__` method overriding allocation

#### Name: `IRAssign` (List Creation)
- **Lowers from**: Python `lst = [a, b, c]` → TAC `$tmp_1 = [a, b, c]; lst = $tmp_1`
- **Reads**: List element variables
- **Writes**: Target variable
- **Allocations**: List object with site ID `{file}:{lineno}:{col}:list`
- **Control-flow**: Normal successor
- **Environment/scope**: New list in current scope
- **Points-to effects**: Target variable points to newly allocated list; List elements field "elem" points to element values
- **Edge cases**: Nested lists; list comprehensions

#### Name: `IRAssign` (Tuple Creation)
- **Lowers from**: Python `tup = (a, b, c)` → TAC `$tmp_1 = (a, b, c); tup = $tmp_1`
- **Reads**: Tuple element variables
- **Writes**: Target variable
- **Allocations**: Tuple object with site ID `{file}:{lineno}:{col}:tuple`
- **Control-flow**: Normal successor
- **Environment/scope**: New tuple in current scope
- **Points-to effects**: Target variable points to newly allocated tuple; Tuple elements field "elem" points to element values
- **Edge cases**: Nested tuples; single element tuples with trailing comma

#### Name: `IRAssign` (Dict Creation)
- **Lowers from**: Python `d = {k1: v1, k2: v2}` → TAC `$tmp_1 = {k1: v1, k2: v2}; d = $tmp_1`
- **Reads**: Dict key and value variables
- **Writes**: Target variable
- **Allocations**: Dict object with site ID `{file}:{lineno}:{col}:dict`
- **Control-flow**: Normal successor
- **Environment/scope**: New dict in current scope
- **Points-to effects**: Target variable points to newly allocated dict; Dict field "value" points to values
- **Edge cases**: Dynamic key names; dict comprehensions

#### Name: `IRAssign` (Set Creation)
- **Lowers from**: Python `s = {a, b, c}` → TAC `$tmp_1 = {a, b, c}; s = $tmp_1`
- **Reads**: Set element variables
- **Writes**: Target variable
- **Allocations**: Set object with site ID `{file}:{lineno}:{col}:set`
- **Control-flow**: Normal successor
- **Environment/scope**: New set in current scope
- **Points-to effects**: Target variable points to newly allocated set; Set field "elem" points to element values
- **Edge cases**: Set comprehensions

#### Name: `IRFunc`/`IRAssign` (Function/Closure Creation)
- **Lowers from**: Python `def func(): ...` or `lambda: ...`
- **Reads**: Free variables in closure
- **Writes**: Function name
- **Allocations**: Function object with site ID `{file}:{lineno}:{col}:func`
- **Control-flow**: Normal successor
- **Environment/scope**: Captures free variables from enclosing scope in closure cells
- **Points-to effects**: Function name points to function object; closure cells store references to free variables
- **Edge cases**: Nested functions; lambdas; decorators

#### Name: `IRClass` (Class Definition)
- **Lowers from**: Python `class C: ...`
- **Reads**: Base classes
- **Writes**: Class name
- **Allocations**: Class object with site ID `{file}:{lineno}:{col}:class`
- **Control-flow**: Normal successor
- **Environment/scope**: Class namespace with methods and class variables
- **Points-to effects**: Class name points to class object; methods stored as attributes
- **Edge cases**: Metaclasses; dynamic base classes; decorators

#### Name: `IRRaise` (Exception Creation)
- **Lowers from**: Python `raise Exception()`
- **Reads**: Exception class, arguments
- **Writes**: N/A
- **Allocations**: Exception object with site ID `{file}:{lineno}:{col}:exc`
- **Control-flow**: Exception edge to handler
- **Environment/scope**: N/A
- **Points-to effects**: Exception variable in handler points to raised exception
- **Edge cases**: Re-raising exceptions; raising from other exceptions

### 2. Variable Assignment/Move

#### Name: `IRCopy`
- **Lowers from**: Python `a = b` → TAC `a = b`
- **Reads**: Source variable
- **Writes**: Target variable
- **Allocations**: None
- **Control-flow**: Normal successor
- **Environment/scope**: Updates target in current scope
- **Points-to effects**: Target variable points to the same objects as source variable
- **Edge cases**: None

### 3. Attribute Operations

#### Name: `IRLoadAttr`
- **Lowers from**: Python `a = obj.attr` → TAC `a = obj.attr`
- **Reads**: Base object variable
- **Writes**: Target variable
- **Allocations**: None (but may trigger descriptor `__get__`)
- **Control-flow**: Normal successor (may raise AttributeError)
- **Environment/scope**: Current scope
- **Points-to effects**: Target variable points to the object referenced by object's attribute
- **Edge cases**: Descriptor protocol (`__get__`); property getters; dynamic attribute names (getattr); MRO for class attributes

#### Name: `IRStoreAttr`
- **Lowers from**: Python `obj.attr = val` → TAC `obj.attr = val`
- **Reads**: Base object variable, value variable
- **Writes**: Object's attribute
- **Allocations**: None (but may trigger descriptor `__set__`)
- **Control-flow**: Normal successor (may raise AttributeError)
- **Environment/scope**: Modifies object's attribute
- **Points-to effects**: Object's attribute field "attr" points to value object
- **Edge cases**: Descriptor protocol (`__set__`); property setters; dynamic attribute names (setattr)

### 4. Container/Subscript Operations

#### Name: `IRLoadSubscr`
- **Lowers from**: Python `a = container[idx]` → TAC `a = container[idx]`
- **Reads**: Container variable, index variable
- **Writes**: Target variable
- **Allocations**: None
- **Control-flow**: Normal successor (may raise exceptions)
- **Environment/scope**: Current scope
- **Points-to effects**: Target variable points to object at container element
- **Edge cases**: Magic methods (`__getitem__`); slicing; negative indices; multi-dimensional

#### Name: `IRStoreSubscr`
- **Lowers from**: Python `container[idx] = val` → TAC `container[idx] = val`
- **Reads**: Container variable, index variable, value variable
- **Writes**: Container element
- **Allocations**: None
- **Control-flow**: Normal successor (may raise exceptions)
- **Environment/scope**: Modifies container
- **Points-to effects**: Container's element field "elem" (for lists/tuples/sets) or "value" (for dict) points to value
- **Edge cases**: Magic methods (`__setitem__`); slicing; type-specific behavior

### 5. Function/Method Calls

#### Name: `IRCall` (Direct Function Call)
- **Lowers from**: Python `res = func(arg1, arg2)` → TAC `res = func(arg1, arg2)`
- **Reads**: Function name, argument variables
- **Writes**: Target variable (if any)
- **Allocations**: None (but function execution may allocate)
- **Control-flow**: Call edge to function entry, return edge back to caller
- **Environment/scope**: Creates new function frame
- **Points-to effects**: Target variable points to function's return value
- **Edge cases**: Varargs, kwargs; default arguments; generators

#### Name: `IRCall` (Indirect Function Call)
- **Lowers from**: Python `res = func_var(arg1, arg2)` → TAC `res = func_var(arg1, arg2)`
- **Reads**: Function variable, argument variables
- **Writes**: Target variable (if any)
- **Allocations**: None (but function execution may allocate)
- **Control-flow**: Indirect call edge to possible function entries
- **Environment/scope**: Creates new function frame
- **Points-to effects**: Target variable points to possible return values from called functions
- **Edge cases**: Multiple possible callees; higher-order functions

#### Name: `IRCall` (Method Call)
- **Lowers from**: Python `res = obj.method(arg)` → TAC `$tmp_1 = obj.method; res = $tmp_1(arg)`
- **Reads**: Base object, method name, argument variables
- **Writes**: Target variable (if any)
- **Allocations**: Bound method object with site ID `{file}:{lineno}:{col}:method`
- **Control-flow**: Method binding, then call edge
- **Environment/scope**: Creates new function frame with self bound
- **Points-to effects**: Target variable points to method's return value
- **Edge cases**: Method resolution order (MRO); `__call__`; descriptors

### 6. Return, Yield, Exceptions

#### Name: `IRReturn`
- **Lowers from**: Python `return expr` → TAC `$tmp_1 = expr; return $tmp_1`
- **Reads**: Return value variable (if any)
- **Writes**: Return slot in caller
- **Allocations**: None
- **Control-flow**: Edge to function exit
- **Environment/scope**: Transfers control back to caller
- **Points-to effects**: Caller's result variable points to returned value
- **Edge cases**: Return from generator (StopIteration); return in finally block

#### Name: `IRYield`
- **Lowers from**: Python `yield expr` → TAC `$tmp_1 = expr; yield $tmp_1`
- **Reads**: Yield value variable
- **Writes**: Generator frame's yield value slot
- **Allocations**: Frame object for generator with site ID `{file}:{lineno}:{col}:genframe`
- **Control-flow**: Edge to caller, re-entry edge back to resume point
- **Environment/scope**: Preserves generator frame
- **Points-to effects**: Caller's result variable points to yielded value
- **Edge cases**: Generator expressions; yield from; sending values back

#### Name: `IRRaise`
- **Lowers from**: Python `raise exc` → TAC `$tmp_1 = exc; raise $tmp_1`
- **Reads**: Exception variable
- **Writes**: Exception handling mechanism
- **Allocations**: May create exception object
- **Control-flow**: Edge to exception handler or propagates up call stack
- **Environment/scope**: Unwinds stack until handler found
- **Points-to effects**: Handler's exception variable points to raised exception
- **Edge cases**: Re-raises; exception chaining (raise from)

### 7. Import/Module Creation

#### Name: `IRImport`
- **Lowers from**: Python `import mod` or `from mod import name`
- **Reads**: Module name
- **Writes**: Target variable(s)
- **Allocations**: Module object with site ID `{file}:{module_name}:module`
- **Control-flow**: Normal successor
- **Environment/scope**: Adds module to current scope
- **Points-to effects**: Import name points to module object or imported attribute
- **Edge cases**: Relative imports; star imports; circular imports

### 8. Environment/Scoping

#### Name: `IRScope` (and subclasses `IRModule`, `IRClass`, `IRFunc`)
- **Lowers from**: Python module, class, or function definitions
- **Reads**: Parent scope for free variables
- **Writes**: Scope namespace
- **Allocations**: Scope object
- **Control-flow**: Normal successor
- **Environment/scope**: Creates new lexical scope
- **Points-to effects**: Name resolution through scope chain
- **Edge cases**: Nested scopes; closures; nonlocal declarations

## C. Class/Function/Visitor Hierarchy

### IR Statement Hierarchy

```
IRStatement (ABC)
├── IRAbstractStmt (ABC)
│   ├── IRAstStmt
│   ├── Label
│   ├── IRCatchException
│   ├── Goto
│   ├── JumpIfFalse
│   ├── JumpIfTrue
│   ├── IRYield
│   ├── IRReturn
│   ├── IRRaise
│   ├── IRPass
│   ├── IRAwait
│   ├── IRDel
│   ├── IRImport
│   ├── IRCall
│   ├── IRAnno
│   ├── AbstractIRAssign (ABC)
│   │   ├── IRAssign
│   │   ├── IRCopy
│   │   ├── IRStoreAttr
│   │   ├── IRLoadAttr
│   │   ├── IRStoreSubscr
│   │   ├── IRLoadSubscr
│   │   └── IRPhi
│   └── IRScope (ABC)
│       ├── IRModule
│       ├── IRFunc
│       └── IRClass
```

### Transformation Hierarchy

```
Transform (ABC)
├── ThreeAddress
├── IR
├── BlockCFG
└── CFG

NodeTransformer
└── ThreeAddressTransformer
```

### CFG Hierarchy

```
Graph
└── ControlFlowGraph

Node
└── BaseBlock

Edge
└── CFGEdge
    ├── NormalEdge
    ├── IfEdge
    ├── CallEdge
    ├── ReturnEdge
    ├── CallToReturnEdge
    ├── WhileEdge
    ├── WhileElseEdge
    ├── WithEdge
    ├── WithEndEdge
    ├── ExceptionEdge
    ├── ExceptionEndEdge
    ├── FinallyEdge
    └── FinallyEndEdge
```

## D. Gaps and Proposed Resolutions

1. **Dynamic Attribute Access**: The current IR handles direct attribute access well, but dynamic attribute access using `getattr()` and `setattr()` are modeled as regular function calls. Propose adding specialized handling:
   - Add `IRDynamicLoadAttr` and `IRDynamicStoreAttr` variants
   - Conservative approximation: attribute with unknown name could reference any attribute

2. **Method Binding**: Method binding (getting a bound method from an object) is not explicitly modeled, but rather flattened into a sequence: get attribute, then call. Propose:
   - Add explicit `IRMethodBind` operation to represent `obj.method` → bound method conversion
   - Use site ID format: `{file}:{lineno}:{col}:method` for bound method allocation

3. **Generator Frame Objects**: Generator `yield` operations create a frame object, but this allocation isn't explicitly modeled. Propose:
   - Track generator frame objects with site ID: `{file}:{lineno}:{col}:genframe`
   - Model saved local variables as fields of the frame object

4. **Descriptors and Special Methods**: Python's descriptor protocol (`__get__`, `__set__`) and special methods for container access aren't explicitly modeled. Propose:
   - Add hooks in attribute/container access operations to dispatch to descriptor methods
   - Handle common patterns like property, staticmethod, classmethod

5. **Position Information**: Some IR nodes may lack position information when derived from complex transformations. Propose:
   - For position-less nodes, generate stable identifier: `{file_stem}:{op}:{hash(uid)%2**32:x}`
   - Ensure all TAC temporary variables preserve source position information

6. **Container Element Sensitivity**: The current model treats container elements uniformly. Propose:
   - For lists/sets/tuples: use field name "elem" for all elements
   - For dicts: use field name "value" for values; separate modeling for keys

7. **Complex Function Call Patterns**: Function calls with complex argument patterns (unpacking, keyword arguments) are simplified in TAC but could lose precision. Propose:
   - Track argument flow explicitly with specialized nodes for unpacking
   - Model kwargs dictionaries as separate entities

## E. Extension Points

1. **Stable Site IDs**: To ensure consistent allocation site modeling across analysis passes:
   - Use format: `{file}:{lineno}:{col}:{kind}` where kind is one of:
     - "obj" (object instance)
     - "list" (list creation)
     - "tuple" (tuple creation)
     - "dict" (dictionary creation)
     - "set" (set creation)
     - "func" (function/lambda creation)
     - "class" (class definition)
     - "exc" (exception)
     - "method" (bound method)
     - "genframe" (generator frame)
   - Fall back to `{file_stem}:{op}:{hash(uid)%2**32:x}` when position info is unavailable

2. **IR Adapter Hooks**: To support extensibility for specialized analyses:
   - Transfer function hooks for custom dataflow analysis
   - Custom annotations on IR nodes for analysis-specific metadata
   - Extension points for field sensitivity customization
   - Plugin architecture for modeling library-specific behaviors

3. **Call Site Identification**:
   - Call sites should be uniquely identified using: `{file}:{lineno}:{col}:call`
   - For dynamic call sites (e.g., `eval`), add special handling and markers

4. **Conservative Special Method Handling**:
   - Always assume container special methods might be called
   - Track potential implementations of `__getattr__`, `__getattribute__`, etc.
   - Account for potential descriptor protocol invocation
