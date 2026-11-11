"""Completion lowering witnesses read the AI summary, never Native constants."""
import contextlib
import io

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config as NativeConfig
from pythonstan.analysis.pointer.kcfa.object import ConstantObject
from pythonstan.graph.cfg import ExceptionEdge, NormalEdge
from pythonstan.ir import IRCall, IRContextEnter, IRContextExit, IRRaise, IRReturn
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline


def pipeline(source, tmp_path, *, analyze=True):
    path = tmp_path / "subject.py"
    path.write_text(source, encoding="utf-8")
    analyses = []
    if analyze:
        analyses = [
            {"name":"pta", "id":"PointerAnalysis", "prev_analysis":["ir"],
             "options":NativeConfig(max_iterations=50000, log_level="ERROR",
                 enable_ai_intrinsics=False, enable_synthetic_method_contexts=False).to_dict()},
            {"name":"ai", "id":"AIAnalysisDriver", "prev_analysis":["pta"],
             "options":{"type":"ai analysis", "pta_analysis_name":"pta",
                         "plan_scopes":["subject.outer"], "strict_exceptions":True,
                         "enable_narrowing":False, "log_level":"ERROR"}},
        ]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = Pipeline(config={"filename":str(path), "project_path":str(tmp_path),
            "library_paths":[], "import_level":0, "mock_libs":False,
            "time_count":False, "analysis":analyses})
        if analyze:
            result.run()
    return result


def outer_scope():
    return next(scope for scope in World().scope_manager.get_scopes()
                if scope.get_qualname() == "subject.outer")


def ai_ints(result):
    summary = result.analysis_manager.get_results("ai").get_summary("subject.outer")
    # ret belongs to the selected AI engine. The Native module result is never
    # read here, even though the legacy path has a Native predecessor.
    values = {item.value for item in summary.ret if isinstance(item, ConstantObject)
              and type(item.value) is int}
    if summary.ret_value is not None:
        constants = summary.ret_value.get_int_lattice().get_consts()
        if constants is not None:
            values.update(constants)
    return summary, values


@pytest.mark.parametrize("source, expected", [
    ("def outer():\n    try:\n        return 1\n    finally:\n        return 2\nresult=outer()\n", {2}),
    ("def outer():\n    x=1\n    try:\n        return x\n    finally:\n        x=2\nresult=outer()\n", {1}),
    ("def outer():\n    try:\n        try:\n            return 1\n        finally:\n            return 2\n    finally:\n        return 3\nresult=outer()\n", {3}),
    ("def outer():\n    x=1\n    try:\n        try:\n            return x\n        finally:\n            x=2\n    finally:\n        x=3\nresult=outer()\n", {1}),
    ("def outer():\n    x=0\n    while True:\n        try:\n            break\n        finally:\n            x=2\n    return x\nresult=outer()\n", {2}),
    ("def outer():\n    x=0\n    try:\n        while True:\n            break\n        return x\n    finally:\n        x=2\nresult=outer()\n", {0}),
    ("def outer():\n    while True:\n        try:\n            return 1\n        finally:\n            break\n    return 2\nresult=outer()\n", {2}),
])
def test_pending_returns_nested_overrides_and_break_cleanup(source, expected, tmp_path):
    result = pipeline(source, tmp_path)
    summary, values = ai_ints(result)
    assert summary.is_complete, summary.incomplete_reasons
    assert values == expected


def test_exception_edges_start_at_protected_operation_not_metadata(tmp_path):
    pipeline("def outer():\n    try:\n        call()\n    except ValueError:\n        return 2\n    return 1\n", tmp_path, analyze=False)
    graph = World().scope_manager.get_ir(outer_scope(), "cfg")
    calls = [block for block in graph.blks if any(isinstance(s, IRCall) for s in block.stmts)]
    assert calls
    for block in calls:
        assert any(isinstance(edge, ExceptionEdge) for edge in graph.out_edges_of(block))
        assert any(isinstance(edge, NormalEdge) for edge in graph.out_edges_of(block))
        call = next(s for s in block.stmts if isinstance(s, IRCall))
        assert call.exception_handlers[0].exception == ["ValueError"]
        assert all(isinstance(edge.get_name(), str) for edge in graph.out_edges_of(block))


def test_with_cleanup_is_emitted_before_return_and_has_suppression_edge(tmp_path):
    pipeline("def outer(manager):\n    with manager:\n        return 1\n", tmp_path, analyze=False)
    stream = World().scope_manager.get_ir(outer_scope(), "ir")
    returned = next(index for index, statement in enumerate(stream) if isinstance(statement, IRReturn))
    assert isinstance(stream[returned - 1], IRContextExit)
    assert stream[returned - 1].completion_kind == "return"
    exceptional = [statement for statement in stream if isinstance(statement, IRContextExit)
                   and statement.completion_kind == "raise"]
    assert exceptional and all(statement.suppressed_label is not None for statement in exceptional)
    entered = next(statement for statement in stream if isinstance(statement, IRContextEnter))
    assert entered.item.context_expr.id.startswith("$pending_completion_")


def test_continue_crosses_cleanup_before_its_loop_edge(tmp_path):
    pipeline("def outer():\n    while True:\n        try:\n            continue\n        finally:\n            cleanup()\n", tmp_path, analyze=False)
    stream = World().scope_manager.get_ir(outer_scope(), "ir")
    from pythonstan.ir import Goto
    call_index = next(index for index, statement in enumerate(stream) if isinstance(statement, IRCall))
    goto_index = next(index for index, statement in enumerate(stream) if isinstance(statement, Goto))
    # TAC may copy the unused call result, but the continue jump cannot precede
    # the cleanup call. An implementation that emits plain continue first fails.
    assert call_index < goto_index


def test_finally_reraise_excludes_own_catch_all_and_saves_exception(tmp_path):
    pipeline("def outer():\n    try:\n        raise ValueError\n    finally:\n        cleanup()\n", tmp_path, analyze=False)
    stream = World().scope_manager.get_ir(outer_scope(), "ir")
    source_raise = next(statement for statement in stream if isinstance(statement, IRRaise))
    assert source_raise.exception_handlers and source_raise.exception_handlers[0].exception is None
    reraises = [statement for statement in stream if isinstance(statement, IRRaise)
                and getattr(statement, "saved_exception_var", None)]
    assert reraises and all(not statement.exception_handlers for statement in reraises)


def test_return_expression_is_saved_before_finally_write(tmp_path):
    pipeline("def outer():\n    x=1\n    try:\n        return x\n    finally:\n        x=2\n", tmp_path, analyze=False)
    stream = World().scope_manager.get_ir(outer_scope(), "ir")
    returned = next(statement for statement in stream if isinstance(statement, IRReturn))
    assert returned.value.startswith("$pending_completion_")
    assert any(returned.value in statement.get_stores() for statement in stream)


@pytest.mark.parametrize("source, expected", [
    ("def outer():\n    try:\n        raise ValueError\n    except ValueError:\n        return 2\nresult=outer()\n", {2}),
    ("def outer():\n    try:\n        try:\n            return 1\n        finally:\n            raise KeyError\n    except KeyError:\n        return 3\nresult=outer()\n", {3}),
    ("def outer():\n    try:\n        try:\n            raise ValueError\n        finally:\n            try:\n                raise KeyError\n            except KeyError:\n                pass\n    except ValueError:\n        return 2\nresult=outer()\n", {2}),
])
def test_exception_handler_and_pending_exception_through_nested_finally(source, expected, tmp_path):
    result = pipeline(source, tmp_path)
    summary, values = ai_ints(result)
    assert summary.is_complete, summary.incomplete_reasons
    assert values == expected
    assert not summary.may_raise
