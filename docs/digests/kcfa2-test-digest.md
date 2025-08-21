# k-CFA2 Test Suite Digest

Version: v1

This digest summarizes the test suite for the k-CFA2 pointer analysis implementation in PythonStAn. The table below shows the test scenarios and their expectations.

## Test Matrix: Scenario × Expectation

| Module | Test Scenario | Expected Behavior | Soundness Rule |
|--------|---------------|-------------------|----------------|
| **test_interfaces** |
| | Module imports | All required modules can be imported | - |
| | AbstractLocation equality | Same function/name/context → equal | II2 |
| | AbstractObject equality | Same allocation site/context/fingerprint → equal | II2 |
| | Context equality | Same call string → equal | II2 |
| | FieldKey uniqueness | Different field kinds are distinct | FS1-4 |
| | FieldKey validation | Named attrs require name, containers don't | FS1-4 |
| | PointsToSet operations | Join produces union of sets | II1 |
| **test_contexts** |
| | Empty context | Empty context has length 0 | CS1 |
| | Call site representation | Includes site ID, function, block, index | CS1 |
| | Context push (k=1) | Keeps only the most recent call site | CS1 |
| | Context push (k=2) | Keeps the two most recent call sites | CS1 |
| | Context pop | Removes most recent call site | CS1 |
| | Recursive context | Same call site repeated in context | CS3 |
| | Higher-order context | Proper context chaining through function pointers | CH5 |
| | Context selector | Creates contexts with k-limiting | CS2 |
| | Context manager | Tracks current context through call/return | IA1-4 |
| | Context truncation | Can truncate context to shorter length | CS1 |
| **test_heap_model** |
| | Allocation site abstraction | Different sites → different objects | HA1 |
| | Allocation context sensitivity | Same site, different contexts → different objects | HA3 |
| | Receiver context sensitivity | Different receiver contexts → different objects | OS1-2 |
| | Receiver context depth limit | Respects maximum receiver object depth | OS1-2 |
| | Receiver context fingerprint | Correctly captures allocation chain | OS1-2 |
| | Field addressing | Different field kinds are distinct | FS1-4 |
| | Field string representation | Matches expected format | - |
| | Allocation ID formats | Follows site ID conventions | HA2 |
| **test_calls** |
| | Direct call | Call site and context correctly created | CH1 |
| | Indirect call | Considers all possible callees | CH2 |
| | Bound method call | Correctly binds receiver to self | CH3 |
| | Closure call | Free variables correctly captured | CH6 |
| | Return flow | Return values flow to caller | CH4 |
| **test_attributes** |
| | Direct attribute access | Attribute fields correctly addressed | AH1 |
| | Dynamic attribute access | Unknown names handled conservatively | AH2-3 |
| | Unknown attribute field | Distinct from known attributes | AH2-3 |
| | Container field access | Element and value fields correctly addressed | FS2-3 |
| | Field sensitivity modes | Attribute-name-sensitive, containers element/value | FS1-4 |
| | Descriptor handling | May-call semantics for descriptors | DH1-3 |
| **test_builtins_summaries** |
| | len() function | Returns int type | BF1 |
| | iter() function | Links iterator to container elements | BF4 |
| | list() constructor | Creates list with element field | BF2 |
| | tuple() constructor | Creates tuple with element field | BF2 |
| | dict() constructor | Creates dict with value field | BF2 |
| | Conservative handling | Unknown operations → conservative result | BF5 |
| | Top object handling | TOP inputs → TOP outputs | TB1-2 |
| **test_cfg_integration** |
| | Site ID generation | Extracts IDs from source locations | IR2 |
| | IR event extraction | Correctly processes IR nodes | IR1 |
| | Allocation event extraction | Detects object creation | IR3 |
| | Call event extraction | Distinguishes call types | IR3 |
| | Attribute event extraction | Processes attribute operations | IR3 |

## Test Coverage Summary

The test suite covers the following aspects of the k-CFA2 pointer analysis:

1. **Core data structures**: Abstract locations, objects, contexts, field keys
2. **Context sensitivity**: k-limiting, context chaining, recursive contexts
3. **Object sensitivity**: Receiver object tracking, method binding
4. **Heap abstraction**: Allocation sites, field sensitivity
5. **Call handling**: Direct, indirect, method calls, closures, return flow
6. **Attribute handling**: Named, dynamic, unknown attribute access
7. **Built-in functions**: Container operations, type conversions, iterators
8. **IR integration**: Event extraction, site ID generation
9. **Soundness rules**: Conservative modeling of dynamic features

## Known Limitations

1. Some integration tests are marked as `xfail` since they depend on IR/TAC modules that are still under development
2. Tests focus on core functionality and may not cover all edge cases
3. Complex dynamic features like metaclasses are only modeled conservatively
4. Full interprocedural analysis is tested but not exhaustively
5. Performance characteristics are not tested
6. Top/Bottom values are modeled in tests but not fully implemented

## Test Configuration

Tests are parameterized with:
- k values: 1 and 2
- Object sensitivity depth: 2
- Field sensitivity: attribute-name-sensitive
- Container modeling: element-insensitive for sequences, value-insensitive for dicts
