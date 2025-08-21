# k-CFA2 Soundness Guidelines

Version: v1

This document outlines the soundness principles and conservative modeling rules encoded in the k-CFA2 pointer analysis tests. These guidelines ensure that the analysis produces correct results even in the presence of dynamic Python features.

## Core Soundness Principles

1. **Over-approximation**: The analysis must never miss possible points-to relationships; it may include spurious ones.
2. **Monotonicity**: Adding code or execution paths should never reduce the analysis result set.
3. **Termination**: Context sensitivity and heap abstraction must guarantee termination even for recursive or highly polymorphic code.
4. **Abstraction Safety**: Abstract domains must correctly model the concrete semantics of all operations.

## Context Sensitivity Rules

### Call String k-Limiting

- **Rule CS1**: Context strings must be limited to the k most recent calls to ensure termination.
- **Rule CS2**: Different k values affect precision but not soundness; any k ≥ 0 is sound.
- **Rule CS3**: Recursion must be handled correctly by preserving context precision on recursive paths.

### 2-Object Sensitivity

- **Rule OS1**: Object allocation must depend on the context of up to `obj_depth` receiver objects.
- **Rule OS2**: Receiver context fingerprints must be deterministic and stable.
- **Rule OS3**: Method calls on the same abstract receiver should resolve to the same abstract method object.

## Heap Modeling Rules

### Allocation Site Abstraction

- **Rule HA1**: Different allocation sites must yield different abstract objects.
- **Rule HA2**: Allocation site IDs must include file/line/column/kind for precision.
- **Rule HA3**: Same allocation site in different contexts must yield different abstract objects.

### Field Sensitivity

- **Rule FS1**: Named attributes must be distinguished by name (`obj.x` vs `obj.y`).
- **Rule FS2**: Container elements are treated uniformly using the `elem` field for lists/tuples/sets.
- **Rule FS3**: Dictionary values are treated uniformly using the `value` field.
- **Rule FS4**: Unknown attributes (`getattr` with dynamic names) must be modeled conservatively by joining across all fields.

## Call Handling Rules

- **Rule CH1**: Direct calls with static function references must resolve precisely.
- **Rule CH2**: Indirect calls must consider all possible target functions in points-to set.
- **Rule CH3**: Method calls must handle binding the receiver to `self` correctly.
- **Rule CH4**: Return flow must propagate all possible return values to call site.
- **Rule CH5**: Higher-order functions must be modeled correctly by maintaining distinct contexts.
- **Rule CH6**: Closures must capture free variables from outer scopes correctly.

## Attribute Handling Rules

- **Rule AH1**: Static attribute access (`obj.attr`) must resolve using the concrete attribute name.
- **Rule AH2**: Dynamic attribute access (`getattr(obj, name)`) with unknown name must conservatively join all attributes.
- **Rule AH3**: Dynamic attribute write (`setattr(obj, name, value)`) with unknown name must conservatively update unknown field.
- **Rule AH4**: Special attributes (e.g., `__class__`, `__dict__`) must be modeled with appropriate semantics.

## Descriptor Handling Rules

- **Rule DH1**: Property access must conservatively call `__get__` method on descriptor.
- **Rule DH2**: Class and static methods must be modeled correctly with respect to their binding behavior.
- **Rule DH3**: Access to descriptors must model correct precedence of instance vs class lookup.

## Built-in Function Rules

- **Rule BF1**: Built-in functions without side effects can be modeled with pure summaries.
- **Rule BF2**: Container constructors (`list()`, `dict()`, etc.) must model element relationships correctly.
- **Rule BF3**: Type conversion functions must preserve relationships between input and output.
- **Rule BF4**: Iterator-producing functions must correctly link iterator to original container elements.
- **Rule BF5**: Unknown or complex built-ins must have conservative models that account for all possible behaviors.

## Dynamic Feature Handling Rules

- **Rule DF1**: `eval` and `exec` must be modeled conservatively (TOP/ANY result).
- **Rule DF2**: Reflection operations (`getattr`, `setattr`, `hasattr`) must be modeled conservatively.
- **Rule DF3**: Metaclasses and `__new__` must be handled conservatively.
- **Rule DF4**: Dynamic code loading must be treated as producing any possible value.

## Integration Rules

- **Rule IR1**: Event extraction from IR must capture all semantically relevant operations.
- **Rule IR2**: Missing source location information must be handled with stable fallback IDs.
- **Rule IR3**: IR/TAC node adaptation must preserve operation semantics.

## Interprocedural Analysis Rules

- **Rule IA1**: Points-to analysis must correctly propagate information across function boundaries.
- **Rule IA2**: Call site context must be correctly created for all types of calls.
- **Rule IA3**: Parameter-argument binding must be sound across different calling conventions.
- **Rule IA4**: Return values must flow back to all possible call sites.

## Top/Bottom Values

- **Rule TB1**: A top value (⊤) represents any possible value and must be propagated conservatively.
- **Rule TB2**: Operations on ⊤ must yield ⊤ to preserve soundness.
- **Rule TB3**: Bottom value (⊥) represents no possible values and must be handled correctly in joins.
- **Rule TB4**: Unreachable code must be modeled as producing ⊥ value.

## Error Handling

- **Rule EH1**: Exceptions must be modeled as control flow to appropriate handlers.
- **Rule EH2**: Exception objects must be tracked with allocation-site sensitivity.
- **Rule EH3**: Uncaught exceptions must not lead to unsound analysis results.

## Implementation Invariants

- **Rule II1**: Lattice operations must satisfy standard properties (commutativity, associativity, idempotence).
- **Rule II2**: Equality and hash operations must be consistent for all abstract domains.
- **Rule II3**: Worklist algorithms must guarantee termination and correctness.
- **Rule II4**: Points-to sets must be immutable to prevent aliasing bugs.