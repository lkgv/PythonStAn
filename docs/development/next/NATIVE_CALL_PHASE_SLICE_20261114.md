# Native source-call phase continuation

Status: B-PLAN preparation only; new witnesses and implementation below are not_run.
Original parent-card DAG and release requirements remain authoritative.

## Contract and implementation order

1. Reuse `pythonstan/analysis/semantics/callplan.py::call_plan` as the phase authority. Evaluate and retain the callable first. Process positional arguments in AST args order; the sole starred positional stays deferred until final invocation, while a star with any additional positional is expanded during positional construction. Evaluate each contiguous explicit-keyword group before merging it. Evaluate and merge each double-star mapping separately; a definite failure terminates subsequent phases. Source column order does not replace the phase plan.
2. In `pythonstan/analysis/transform/three_address.py::visit_Call`, preserve these success dependencies rather than eagerly lowering every argument effect. Extend `pythonstan/ir/ir_statements.py::IRCall` with ordered phases, star/deferred status, keyword-group boundaries and stable source spans; migrate rename/copy/get_loads together. Retain the callable value at its original phase.
3. In `pythonstan/analysis/pointer/kcfa/ir_translator.py::_translate_call`, preserve positional star flags and keyword order. The current frozenset keyword representation cannot encode ordering or repeated entries. Update `constraints.py::CallConstraint` and `processor/normal_call.py::_match_parameters` with explicit phase-completion dependencies. Only successful phases activate the next phase, binding and callee body.
4. First support exact finite builtin tuple/list/dict operands and explicit keywords. Definite duplicate/noniterable failures preserve prior effects and suppress later effects. Unknown iterable/custom mapping paths retain may-flow, callback effects and an explicit open residual. Reuse CallArguments/bind_arguments after argument construction; this first slice is not a complete custom-protocol or Native soundness qualification.

## Independent CPython controls to register

Use the same synthetic helper for each case, freshly initialized:

```python
log = ""
def mark(tag, value=None):
    global log
    log += tag
    return value
def f(*args, **kwargs):
    global log
    log += "F"
def make():
    global log
    log += "C"
    return f
try:
    # Insert one call from the table.
    pass
except TypeError:
    pass
result = log
```

| Call | Expected log to verify with CPython 3.12 |
| --- | --- |
| `make()(mark("P"), k=mark("K"))` | `CPKF` |
| `f(*42, k=mark("K"))` | `K` |
| `f(*42, mark("P"), k=mark("K"))` | empty string |
| `f(**{"x": 0}, x=mark("A"), y=mark("B"), **mark("M", {}))` | `AB` |

These expected traces have not been executed in this slice. Establish concrete gold independently before production edits. Native public checks must cover the concrete result and suppress definitely unreachable later source-site effects. Distinguish call-target resolution facts from body activation: a resolved target is not by itself evidence that the callee body executed.

## Migration guards

Freeze existing semantic52 gold/caps, call source spans, CallConstraint hash/replay, context tokens, protocol-generated calls and keyword late subscriptions. Preserve query definitions and Native coverage reporting. Add a compatibility adapter only for already-evaluated old callers; do not use it to erase phase dependencies of source calls. Test Native and abstract engines against the same independent sources without requiring identical internal state representations.

Every preparation, expansion, replay and publication remains inside the existing cost contract. Record new checks as not_run until their own actual CI. No live model or external-source request is part of this slice.
