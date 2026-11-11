"""Lowering obligations must reach live PTA and AI without erasing call facts."""
import ast
import copy
from dataclasses import FrozenInstanceError

import pytest

from pythonstan.analysis.transform.three_address import ThreeAddressTransformer
from pythonstan.ir import IRCall
from pythonstan.ir.semantic_residuals import get_frontend_residuals
from tests.analysis.legacy_ai_support import analyze_program


FOR_CODE = "frontend_for_builtin_identity_unproved"
ASYNC_CODE = "frontend_async_for_protocol_unmodeled"


@pytest.mark.parametrize("source,code,source_kind", [
    ("for item in (1,):\n    pass\n", FOR_CODE, "For"),
    ("async def consume(items):\n    async for item in items:\n        pass\n", ASYNC_CODE, "AsyncFor"),
])
def test_generated_first_iter_assignment_keeps_immutable_metadata_through_ir(source, code, source_kind):
    tree = ThreeAddressTransformer().visit(ast.parse(source))
    tagged = [node for node in ast.walk(tree) if get_frontend_residuals(node)]
    assert len(tagged) == 1
    assignment, = tagged
    assert isinstance(assignment, ast.Assign)
    expected_lookup = "aiter" if source_kind == "AsyncFor" else "iter"
    assert isinstance(assignment.value, ast.Call) and assignment.value.func.id == expected_lookup
    if source_kind == "AsyncFor":
        assert assignment.value._pythonstan_async_iteration_operation == "aiter"
    residual, = get_frontend_residuals(assignment)
    assert residual.code == code and residual.source_kind == source_kind
    assert residual.source_line >= 1
    with pytest.raises(FrozenInstanceError):
        residual.code = "forged"
    preserved = IRCall(copy.deepcopy(assignment)).get_semantic_residuals()
    assert preserved == (residual,)
    # Diagnostic metadata cannot change source AST fingerprints or executable
    # Python fields. The compiler's protocol marker is separate from this
    # immutable diagnostic metadata and survives its removal.
    before = ast.dump(tree, include_attributes=True)
    del assignment._pythonstan_semantic_residuals
    assert ast.dump(tree, include_attributes=True) == before
    compile(ast.fix_missing_locations(tree), "<metadata-preserves-executable-ast>", "exec")


@pytest.mark.parametrize("track_unknowns", [False, True])
@pytest.mark.parametrize("shadow", ["", "def next(*args):\n    return 99\ndef iter(value):\n    return value\n"])
def test_live_for_residual_reaches_pta_and_ai_and_preserves_body_calls(tmp_path, track_unknowns, shadow):
    source = shadow + '''
def callback():
    return 7
def consume():
    for function in (callback,):
        callback()
    return 1
result = consume()
'''
    with analyze_program(tmp_path, source, pointer_options={"track_unknowns": track_unknowns}) as program:
        residuals = program.query.get_residual_effects()
        matching = [item for item in residuals if FOR_CODE in item["reason"]]
        assert matching and "builtin_binding_identity" in matching[0]["effects"]
        assert program.query.get_semantic_status()["residual_obligation_count"] >= len(matching)
        assert ("subject.consume", "subject.callback") in program.edges
        summary = program.ai.get_summary("subject.consume")
        assert not summary.is_complete
        assert FOR_CODE in summary.incomplete_reasons
        # The ordinary caller also retains the callee's incomplete obligation.
        assert FOR_CODE in program.ai.get_summary("subject").incomplete_reasons


@pytest.mark.parametrize("track_unknowns", [False, True])
def test_live_async_for_cannot_be_completed_by_sync_iter_transfers(tmp_path, track_unknowns):
    source = '''
events = []
def callback():
    events.append(7)
    return 7
async def values():
    yield None
    yield 2
async def consume(items):
    async for item in items:
        callback()
    return 1
pending = consume(values())
'''
    namespace = {}
    exec(source, namespace)
    assert namespace["events"] == []
    try:
        namespace["pending"].send(None)
    except StopIteration:
        pass
    assert namespace["events"] == [7, 7]
    with analyze_program(tmp_path, source, pointer_options={"track_unknowns": track_unknowns}) as dormant:
        assert ("subject.consume", "subject.callback") not in dormant.edges
    source += "try:\n    pending.send(None)\nexcept StopIteration:\n    pass\n"
    with analyze_program(tmp_path, source, pointer_options={"track_unknowns": track_unknowns}) as program:
        matching = [item for item in program.query.get_residual_effects() if ASYNC_CODE in item["reason"]]
        assert matching and "await_suspension" in matching[0]["effects"]
        assert ("subject.consume", "subject.callback") in program.edges
        summary = program.ai.get_summary("subject.consume")
        assert not summary.is_complete
        assert ASYNC_CODE in summary.incomplete_reasons


def test_no_loop_scope_does_not_acquire_unrelated_frontend_obligations(tmp_path):
    with analyze_program(tmp_path, "def clean():\n    return 1\nresult = clean()\n") as program:
        assert not any("frontend_" in effect["reason"] for effect in program.query.get_residual_effects())
        summary = program.ai.get_summary("subject.clean")
        assert summary.is_complete
        assert not any(reason.startswith("frontend_") for reason in summary.incomplete_reasons)


def test_unreachable_nested_function_does_not_taint_clean_caller_scope(tmp_path):
    source = '''
def unused():
    for item in (1,):
        pass
def clean():
    return 1
result = clean()
'''
    with analyze_program(tmp_path, source) as program:
        assert program.ai.get_summary("subject.clean").is_complete
        assert not any(reason.startswith("frontend_") for reason in
                       program.ai.get_summary("subject").incomplete_reasons)
        assert not any("frontend_" in item["reason"] for item in program.query.get_residual_effects())
