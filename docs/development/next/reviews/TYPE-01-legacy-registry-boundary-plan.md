# Read-only legacy registry boundary plan

Reviewer: requirements_audit. This is an implementation plan, not acceptance or a
request to clean every pre-existing Native diagnostic. No implementation was edited.

The recovery quality snapshot identifies thirteen new diagnostics in the four
legacy consumers listed below. Canonical DomainRegistry.get deliberately returns
RegisteredDomain | None, whose element results are object. Runtime registration can
replace a provider under the names bool or int; the registry correctly cannot infer
a builtin representation from that name. Its genuine adapter validates provider
inputs against the provider's declared finite element classes.

The minimal consumer boundary is to look up an actual provider once, handle absence
explicitly, invoke its real operation, and validate the returned representation
where the legacy consumer requires a concrete lattice record.

| Consumer | Required change |
| --- | --- |
| value_domain.py with_component, join, meet and widen | Check every actual bool operation result with isinstance(result, BoolLattice) before assigning boolean_values. Check the direct supplied component in with_component as well. |
| value_domain.py is_bottom and leq | Bind the lookup result once and narrow None explicitly, preserving the existing absence policy. |
| heap.py ContainerSlots.widen | Execute the registered integer widening and check the result is IntLattice. Retain the historical builtin fallback only if the provider is absent. |
| domains/type_domain.py and domains/callable.py on_reduce_value | Accept the base contract frozenset[object], then guard real AbstractObject instances before building ClassRef or FunctionRef. A narrowed legacy AddrSet parameter violates the override. |

Do not add name-based get overloads, cast a provider to Domain[BoolLattice], replace
a custom operation with the builtin operation, or infer all variants from bottom
and top. A provider can declare additional finite intermediate variants. Compatible
custom providers remain usable through their actual operations; an incompatible
representation must fail explicitly at this fixed-representation consumer or use
an already-defined honest open-result policy. It cannot silently become a builtin
value or receive an unsupported generic type assertion. The erased generic registry
continues to support other representations outside these legacy fixed projections.

TypeDomain and CallableDomain already inherit their real Domain[TypeVal] and
Domain[CallableVal] specializations. Their two new override errors are address-input
variance errors, separate from the object result errors. Broad changes to generic
Domain parameters are unnecessary.

Meaningful controls should cover a compatible replacement provider whose operation
actually executes, an incompatible declared intermediate variant, absent provider
handling, foreign address rejection at the token boundary, and builtin output
parity. Ordinary imports must remain enabled; pre-existing diagnostics in untouched
Native code stay visible rather than being hidden by a new module exclusion.

Exact new diagnostics from artifacts/next/evidence/recovery-quality/report.json:

- New diagnostic: pythonstan/compat/legacy_ai/value_domain.py:286: mypy/assignment
- New diagnostic: pythonstan/compat/legacy_ai/value_domain.py:287: mypy/union-attr
- New diagnostic: pythonstan/compat/legacy_ai/value_domain.py:376: mypy/union-attr
- New diagnostic: pythonstan/compat/legacy_ai/value_domain.py:448: mypy/union-attr
- New diagnostic: pythonstan/compat/legacy_ai/value_domain.py:448: mypy/arg-type
- New diagnostic: pythonstan/compat/legacy_ai/value_domain.py:478: mypy/union-attr
- New diagnostic: pythonstan/compat/legacy_ai/value_domain.py:478: mypy/arg-type
- New diagnostic: pythonstan/compat/legacy_ai/value_domain.py:488: mypy/union-attr
- New diagnostic: pythonstan/compat/legacy_ai/value_domain.py:580: mypy/union-attr
- New diagnostic: pythonstan/compat/legacy_ai/value_domain.py:580: mypy/arg-type
- New diagnostic: pythonstan/compat/legacy_ai/domains/type_domain.py:141: mypy/override
- New diagnostic: pythonstan/compat/legacy_ai/domains/callable.py:196: mypy/override
- New diagnostic: pythonstan/compat/legacy_ai/heap.py:453: mypy/assignment
