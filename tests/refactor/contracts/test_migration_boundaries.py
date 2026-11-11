"""Identity, nonexecution and explicit Native snapshot conversion contracts."""

import ast
from pathlib import Path
import subprocess
import sys
import json

import pytest

from pythonstan.analysis.contracts import SourceModule, EntryScenario, stable_digest
from pythonstan.analysis.composition.native_snapshot import NativeSnapshotOracle
from pythonstan.analysis.engines.abstract.driver import InterproceduralDriver
from pythonstan.frontend import parse_sources, source_node_identity


def source():
    return SourceModule("example", "virtual.py", "def f(x):\n    return x\nresult = f('after')\n")


def native_result(module, values=None):
    return {"source_digest": stable_digest({module.module: module.digest}),
            "profile": "python-core-v1", "model_digest": "pythonstan-builtins-v1",
            "solver_status": "budget_exhausted", "values": values or {},
            "stats": {"native_work": 7}}


def test_frontend_stable_ids_and_source_nonexecution(tmp_path):
    marker = tmp_path / "target-was-executed"
    module = SourceModule("example", "virtual.py", f"open({str(marker)!r}, 'w').write('bad')\n")
    first = parse_sources([module])
    second = parse_sources([module])
    assert not marker.exists()
    assert set(first.nodes.values()) == set(second.nodes.values())
    statement = first.trees["example"].body[0]
    assert first.nodes[id(statement)] == source_node_identity(module.module, module.digest, statement)
    with pytest.raises(TypeError):
        first.modules["other"] = module


def test_frontend_rejects_duplicate_modules_and_invalid_symbol_placement():
    with pytest.raises(ValueError, match="duplicate"):
        parse_sources([source(), source()])
    with pytest.raises(SyntaxError):
        parse_sources([SourceModule("bad", "bad.py", "return 1\n")])
    with pytest.raises(SyntaxError):
        parse_sources([SourceModule("bad", "bad.py", "def f():\n    nonlocal missing\n")])


def test_frontend_type_ignore_without_column_has_stable_identity():
    module = SourceModule("example", "virtual.py", "x = 1  # type: ignore[assignment]\n")
    parsed = parse_sources([module])
    ignore = parsed.trees["example"].type_ignores[0]
    assert isinstance(ignore, ast.TypeIgnore)
    assert parsed.nodes[id(ignore)].endswith(":1:0:TypeIgnore")


def test_frontend_span_points_unchanged_and_shared_markers_are_not_points():
    code = "def f(a):\n    return [a + x for x in (1, 2)]\nresult = f(3)\n"
    modules = [SourceModule(name, name + ".py", code) for name in ("first", "second")]
    parsed = parse_sources(modules)
    markers = (ast.expr_context, ast.operator, ast.unaryop, ast.boolop, ast.cmpop)
    for module in modules:
        tree = parsed.trees[module.module]
        for node in ast.walk(tree):
            if isinstance(node, markers):
                assert id(node) not in parsed.nodes
                assert id(node) not in parsed.node_modules
            elif hasattr(node, "lineno"):
                expected = (f"{module.module}:{module.digest[:16]}:"
                            f"{node.lineno}:{getattr(node, 'col_offset', 0)}:{type(node).__name__}")
                assert parsed.nodes[id(node)] == expected
                assert parsed.node_modules[id(node)] == module.module
        structural = [parsed.nodes[id(node)] for node in ast.walk(tree)
                      if not isinstance(node, markers) and not hasattr(node, "lineno")]
        assert len(structural) == len(set(structural))


def test_frontend_session_parser_cache_is_source_bound_and_statically_checked(monkeypatch):
    module = source()
    tree = ast.parse(module.source, module.filename, type_comments=True, feature_version=(3, 12))
    def unexpected_reparse(*args, **kwargs):
        raise AssertionError("normalizer-owned AST must be reused")
    monkeypatch.setattr(ast, "parse", unexpected_reparse)
    program = parse_sources([module], parsed_trees={module.module:(module.digest, tree)})
    assert program.trees[module.module] is tree
    with pytest.raises(ValueError, match="cache identity mismatch"):
        parse_sources([module], parsed_trees={module.module:("stale", tree)})
    bad = SourceModule("bad", "bad.py", "return 1\n")
    # Parsing allows this shape; the mandatory compiler validity check rejects
    # it even when input normalization already supplied a cached tree.
    invalid_tree = ast.Module(body=[ast.Return(value=ast.Constant(value=1))], type_ignores=[])
    ast.fix_missing_locations(invalid_tree)
    with pytest.raises(SyntaxError):
        parse_sources([bad], parsed_trees={"bad":(bad.digest, invalid_tree)})


def test_native_snapshot_requires_matching_semantic_identity():
    module = source()
    snapshot = native_result(module)
    with pytest.raises(ValueError, match="source"):
        NativeSnapshotOracle(snapshot, source_digest="wrong", profile="python-core-v1")
    with pytest.raises(ValueError, match="profile"):
        NativeSnapshotOracle(snapshot, source_digest=snapshot["source_digest"], profile="other")
    with pytest.raises(ValueError, match="missing"):
        NativeSnapshotOracle({}, source_digest=snapshot["source_digest"], profile="python-core-v1")
    missing_model = dict(snapshot)
    del missing_model["model_digest"]
    with pytest.raises(ValueError, match="model"):
        NativeSnapshotOracle(missing_model, source_digest=snapshot["source_digest"], profile="python-core-v1")
    oracle = NativeSnapshotOracle(snapshot, source_digest=snapshot["source_digest"], profile="python-core-v1")
    with pytest.raises(ValueError, match="different source"):
        oracle.bind(InterproceduralDriver([SourceModule("example", "new.py", "result=2\n")]))


def test_typed_positive_scalar_projection_is_partial_and_never_prunes():
    module = source()
    rows = {"example": {"candidate": {"tagged_constants": [{"type":"bool", "value":True},
                      {"type":"int", "value":1}], "objects":["unmapped-native-id"], "top":True}}}
    snapshot = native_result(module, rows)
    oracle = NativeSnapshotOracle(snapshot, source_digest=snapshot["source_digest"], profile="python-core-v1")
    driver = InterproceduralDriver([module], {"semantic_profile":"python-core-v1"}, oracle=oracle)
    oracle.bind(driver)
    # Mutation after adapter construction cannot change the imported snapshot.
    rows["example"]["candidate"]["tagged_constants"].append({"type":"str", "value":"injected"})
    answer = oracle.candidates("global:example:candidate")
    assert answer.coverage == "partial" and not answer.permits_exclusion()
    assert answer.values.bools == frozenset({True})
    assert answer.values.ints.get_consts() == frozenset({1})
    assert not answer.values.refs and answer.values.strings.is_bottom()
    result = driver.run([EntryScenario("example")])
    assert result["values"]["example"]["result"]["constants"] == ("after",)
    stats = oracle.statistics()
    assert stats["native_work"] == 7
    assert stats["unmapped_reference_candidates"] == 1
    assert stats["unknown_rows_not_used_as_exclusion"] == 1


def test_malformed_scalar_tag_is_rejected_before_use():
    module = source()
    snapshot = native_result(module, {"example":{"x":{"tagged_constants":[{"type":"int", "value":True}]}}})
    oracle = NativeSnapshotOracle(snapshot, source_digest=snapshot["source_digest"], profile="python-core-v1")
    with pytest.raises(ValueError, match="type/value mismatch"):
        oracle.bind(InterproceduralDriver([module]))


def test_closure_aliases_are_one_registered_implementation():
    from pythonstan.analysis.closure import ClosureAnalysis
    from pythonstan.analysis.scope import ClosureAnalysis as scope_alias
    from pythonstan.analysis.scope.closure import ClosureAnalysis as module_alias
    from pythonstan.analysis.class_hierarchy import ClosureAnalysis as old_misnamed_alias
    assert ClosureAnalysis is scope_alias is module_alias is old_misnamed_alias
    from pythonstan.models.builtins import LEGACY_BUILTIN_FUNCTIONS
    from pythonstan.analysis.pointer.kcfa.builtin_api_handler import BUILTIN_FUNCTIONS
    assert LEGACY_BUILTIN_FUNCTIONS == BUILTIN_FUNCTIONS


def test_legacy_budget_is_one_engine_neutral_definition():
    from dataclasses import fields
    from pythonstan.analysis.contracts.budgets import AnalysisBudget, validate_budget_options
    from pythonstan.analysis.pointer.ai.api import AnalysisBudget as legacy_budget
    from pythonstan.analysis.pointer.ai.config import validate_budget_options as legacy_validator
    assert legacy_budget is AnalysisBudget
    assert legacy_validator is validate_budget_options
    assert len(fields(AnalysisBudget)) == 25
    assert validate_budget_options({"max_iterations":0}) == {"max_iterations":0}
    with pytest.raises(ValueError, match="Unknown AI budget"):
        validate_budget_options({"invented_limit":1})


def test_frozen_builtin_exception_hierarchy_keeps_alias_and_baseexception_distinctions():
    from pythonstan.models.exceptions import builtin_exception_ancestors as ancestors
    assert {"UnboundLocalError", "NameError", "Exception", "BaseException"} <= ancestors("UnboundLocalError")
    assert "ValueError" in ancestors("UnicodeDecodeError")
    assert {"OSError", "IOError", "EnvironmentError", "Exception"} <= ancestors("FileNotFoundError")
    assert ancestors("IOError") == ancestors("OSError") == ancestors("EnvironmentError")
    assert "BaseException" in ancestors("SystemExit") and "Exception" not in ancestors("SystemExit")
    assert "StopIteration" not in ancestors("StopAsyncIteration")
    assert {"Exception", "BaseExceptionGroup"} <= ancestors("ExceptionGroup")
    assert not ancestors("custom.UserException")


def test_complete_builtin_namespace_is_separate_from_transfer_capability():
    from pythonstan.models.builtins import PYTHON312_BUILTIN_NAMES, ABSTRACT_BUILTINS
    assert {"memoryview", "anext", "aiter", "NotImplemented", "Ellipsis", "breakpoint",
            "BaseExceptionGroup", "UnicodeDecodeError", "__debug__", "help", "copyright"} <= PYTHON312_BUILTIN_NAMES
    assert ABSTRACT_BUILTINS <= PYTHON312_BUILTIN_NAMES
    assert "PythonFinalizationError" not in PYTHON312_BUILTIN_NAMES  # Python 3.13+


def test_maintained_closure_script_has_real_file_input_and_json(tmp_path):
    path = tmp_path / "closure_subject.py"
    path.write_text("def outer(x):\n    def inner():\n        return x\n    return inner\n")
    root = Path(__file__).resolve().parents[3]
    completed = subprocess.run([sys.executable, "-m", "scripts.do_closure", str(path), "--format", "json"],
                               cwd=root, text=True, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["file"] == str(path)
    assert any(row["qualname"].endswith(".outer.inner") for row in result["scopes"])
    assert any("x" in row["cell_variables"] for row in result["scopes"])
