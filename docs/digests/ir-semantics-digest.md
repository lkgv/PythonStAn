# IR Operations Digest for Pointer Analysis

Version: v1

## Allocation Operations

- `IRAssign (Object)` → Reads: class | Writes: target | Alloc: object | Edges: normal | PT: target → new object
- `IRAssign (List)` → Reads: elements | Writes: target | Alloc: list | Edges: normal | PT: target → new list, list.elem → elements
- `IRAssign (Tuple)` → Reads: elements | Writes: target | Alloc: tuple | Edges: normal | PT: target → new tuple, tuple.elem → elements
- `IRAssign (Dict)` → Reads: keys, values | Writes: target | Alloc: dict | Edges: normal | PT: target → new dict, dict.value → values
- `IRAssign (Set)` → Reads: elements | Writes: target | Alloc: set | Edges: normal | PT: target → new set, set.elem → elements
- `IRFunc/IRAssign (Function)` → Reads: free vars | Writes: name | Alloc: function | Edges: normal | PT: name → function object, closure cells → free vars
- `IRClass` → Reads: bases | Writes: name | Alloc: class | Edges: normal | PT: name → class object, class attrs → methods
- `IRRaise` → Reads: exception | Writes: N/A | Alloc: exception | Edges: exception | PT: handler var → exception object
- `IRYield` → Reads: value | Writes: target | Alloc: frame | Edges: yield/resume | PT: caller result → yielded value

## Variable Operations

- `IRCopy` → Reads: source | Writes: target | Alloc: none | Edges: normal | PT: target → source objects

## Attribute Operations

- `IRLoadAttr` → Reads: base | Writes: target | Alloc: none | Edges: normal | PT: target → base.attr objects
- `IRStoreAttr` → Reads: base, value | Writes: base.attr | Alloc: none | Edges: normal | PT: base.attr → value objects

## Container Operations

- `IRLoadSubscr` → Reads: container, index | Writes: target | Alloc: none | Edges: normal | PT: target → container.elem objects
- `IRStoreSubscr` → Reads: container, index, value | Writes: container[index] | Alloc: none | Edges: normal | PT: container.elem → value objects

## Call Operations

- `IRCall (Direct)` → Reads: func, args | Writes: target | Alloc: none | Edges: call/return | PT: target → return value objects
- `IRCall (Indirect)` → Reads: func var, args | Writes: target | Alloc: none | Edges: call/return | PT: target → possible return value objects
- `IRCall (Method)` → Reads: base, method, args | Writes: target | Alloc: bound method | Edges: call/return | PT: target → method return value objects

## Control Flow Operations

- `IRReturn` → Reads: value | Writes: caller result | Alloc: none | Edges: function exit | PT: caller result → returned value
- `Goto` → Reads: none | Writes: none | Alloc: none | Edges: unconditional | PT: none
- `JumpIfTrue/JumpIfFalse` → Reads: condition | Writes: none | Alloc: none | Edges: conditional | PT: none
- `IRCatchException` → Reads: none | Writes: exception var | Alloc: none | Edges: exception | PT: exception var → caught exception

## Module Operations

- `IRImport` → Reads: module name | Writes: target | Alloc: module | Edges: normal | PT: target → module or imported object
- `IRModule` → Reads: none | Writes: module namespace | Alloc: module | Edges: normal | PT: module name → module object

## Gaps and Special Cases

1. Dynamic attribute access (getattr/setattr): Model as function call with conservative handling
2. Method binding: Implicit in attribute load + call; should track bound method objects
3. Generator frames: Implicitly allocated; should track as objects with captured locals
4. Descriptors (__get__, __set__): Handle specially for property/classmethod/staticmethod
5. Missing position info: Fall back to hash-based stable identifiers
6. Container element sensitivity: Unified "elem" field for sequences, "value" field for dicts

## Allocation Site ID Scheme

```
alloc_id := f"{file}:{lineno}:{col}:{kind}"
call_id := f"{file}:{lineno}:{col}:call"
fallback := f"{file_stem}:{op}:{hash(uid)%2**32:x}"
```

Where `kind` is one of: "obj", "list", "tuple", "dict", "set", "func", "class", "exc", "method", "genframe"
