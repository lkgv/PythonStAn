"""Malformed source metadata cannot become a falsely covered Native entry."""

import ast
from collections.abc import Iterator
from typing import Self, override

import pytest

from pythonstan.analysis.pointer.kcfa.context import CallStringContext, Scope
from pythonstan.analysis.pointer.kcfa.object import (
    AllocKind,
    AllocSite,
    FunctionObject,
    ModuleObject,
)
from pythonstan.analysis.project.entry_contracts import derive_exported_function_templates
from pythonstan.ir import IRFunc, IRModule
from pythonstan.ir.semantic_residuals import (
    FrontendResidual,
    attach_frontend_residual,
    get_frontend_residuals,
)

_METADATA = "_pythonstan_semantic_residuals"


def test_wrong_runtime_ir_field_remains_an_explicit_uncovered_export() -> None:
    context = CallStringContext((), 1)
    tree = ast.parse("def selected(value): return value\n")
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    module = IRModule("subject", tree, filename="subject.py")
    module_object = ModuleObject(context, AllocSite(module, AllocKind.MODULE), module)
    scope = Scope.new(module_object, None, context, module, None)
    ir = IRFunc("subject.selected", node)
    function = FunctionObject(context, AllocSite(ir, AllocKind.FUNCTION), scope, ir)
    # Mutable external metadata can violate a dataclass field's nominal type.
    object.__setattr__(function, "ir", module)

    plan = derive_exported_function_templates((function,))

    assert plan.templates == ()
    assert plan.represented_values == 1
    assert plan.uncovered[0].reason == "unrecognized_source_function_ir"


def test_attached_obligation_preserves_ast_and_deduplicates_the_actual_record() -> None:
    node = ast.Name(id="generated", ctx=ast.Load())
    origin = ast.parse("value = source\n").body[0]
    before = ast.dump(node, include_attributes=True)
    assert (
        attach_frontend_residual(node, origin, "unsupported", "not modeled", ("may_raise",)) is node
    )
    assert (
        attach_frontend_residual(node, origin, "unsupported", "not modeled", ("may_raise",)) is node
    )

    records = get_frontend_residuals(node)

    assert len(records) == 1
    assert records[0] == FrontendResidual("unsupported", "not modeled", ("may_raise",), "Assign", 1)
    assert ast.dump(node, include_attributes=True) == before
    assert get_frontend_residuals(ast.parse("value = source\n")) == ()


def test_finite_residual_boundary_does_not_silently_discard_invalid_payloads() -> None:
    node = ast.Name(id="generated", ctx=ast.Load())
    setattr(node, _METADATA, (object(),))
    with pytest.raises(TypeError, match="FrontendResidual"):
        get_frontend_residuals(node)


def test_exact_immutable_metadata_tuple_retains_its_existing_observer_identity() -> None:
    record = FrontendResidual("unsupported", "not modeled", (), "Expr", 1)
    records = (record,)
    node = ast.Name(id="generated", ctx=ast.Load())
    setattr(node, _METADATA, records)
    assert get_frontend_residuals(node) is records


def test_residual_metadata_iteration_invokes_the_slot_only_once() -> None:
    record = FrontendResidual("unsupported", "not modeled", (), "Expr", 1)

    class Once(Iterator[FrontendResidual]):
        def __init__(self) -> None:
            self.iterations = 0
            self.available = True

        @override
        def __iter__(self) -> Self:
            self.iterations += 1
            if self.iterations > 1:
                raise RuntimeError("metadata iterator invoked twice")
            return self

        @override
        def __next__(self) -> FrontendResidual:
            if not self.available:
                raise StopIteration
            self.available = False
            return record

    metadata = Once()
    node = ast.Name(id="generated", ctx=ast.Load())
    setattr(node, _METADATA, metadata)
    assert get_frontend_residuals(node) == (record,)
    assert metadata.iterations == 1
