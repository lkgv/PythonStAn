"""Legacy lowering retains original source without re-reading mutable files."""
import ast
import contextlib
from dataclasses import FrozenInstanceError
import hashlib
import io

import pytest

from pythonstan.analysis.contracts import SourceModule
from pythonstan.ir import IRModule
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline


def test_pipeline_keeps_original_encoded_bytes_and_nested_scope_ownership(tmp_path):
    path = tmp_path / "subject.py"
    source = ("# coding: latin-1\n# caf\N{LATIN SMALL LETTER E WITH ACUTE}\n"
              "class Holder:\n"
              "    def method(self):\n"
              "        def nested(value):\n"
              "            return (value + 1) * 2\n"
              "        return nested(3)\n"
              "result = (1 + 2) * 3\n")
    raw = source.encode("latin-1")
    path.write_bytes(raw)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        pipeline = Pipeline(config={"filename": str(path), "project_path": str(tmp_path),
            "library_paths": [], "import_level": 0, "mock_libs": False,
            "time_count": False, "analysis": []})
    module = pipeline.get_world().get_entry_module()
    snapshot = module.get_source_snapshot()
    assert isinstance(snapshot, SourceModule)
    assert snapshot.source == source
    assert snapshot.original_bytes_digest == hashlib.sha256(raw).hexdigest()
    assert snapshot.digest == hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert snapshot.encoding == "iso-8859-1"
    lowered = World().scope_manager.get_ir(module, "three address form")
    assert ast.dump(lowered) != ast.dump(ast.parse(source))
    nested = [scope for scope in World().scope_manager.get_scopes()
              if scope.get_qualname().startswith("subject.")]
    assert {scope.get_qualname() for scope in nested} >= {
        "subject.Holder", "subject.Holder.method", "subject.Holder.method.nested"}
    assert all(scope.source_module is module and scope.get_source_snapshot() is snapshot for scope in nested)
    # Once the initial source is captured, removing the file must not remove
    # the original input, and subsequent AST edits must not alter the snapshot.
    path.unlink()
    module.get_ast().body.clear()
    assert all(scope.get_source_snapshot().source == source for scope in nested)
    with pytest.raises(FrozenInstanceError):
        snapshot.source = "changed"
    with pytest.raises(AttributeError):
        module.source_snapshot = None


def test_explicit_decoded_content_needs_no_file_and_ast_only_modules_have_no_source(tmp_path):
    path = tmp_path / "never-created.py"
    source = "def identity(value):\n    return value\n"
    module = IRModule.load_module("example", str(path), content=source)
    assert module.get_qualname() == "example"
    assert module.get_source_snapshot().source == source
    assert module.get_source_snapshot().encoding == "utf-8"
    assert not path.exists()
    synthetic = IRModule("synthetic", ast.parse("value = 1\n"))
    assert synthetic.source_module is synthetic
    assert synthetic.get_source_snapshot() is None
    with pytest.raises(ValueError, match="module identity"):
        IRModule("other", ast.parse(source), source_snapshot=module.source_snapshot)


def test_project_producer_captures_source_before_lowering(tmp_path):
    from pythonstan.analysis.project import ProjectConfig, analyze_project
    directory = tmp_path / "app"
    directory.mkdir()
    path = directory / "__init__.py"
    source = "# coding: latin-1\nname = 'caf\N{LATIN SMALL LETTER E WITH ACUTE}'\nvalue = (1 + 2) * 3\n"
    raw = source.encode("latin-1")
    path.write_bytes(raw)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        report = analyze_project(ProjectConfig(str(tmp_path), "app/__init__.py",
            source_roots=("app",), auto_framework_entries=False))
    assert report["solver_completed"], report.get("analysis_error")
    module = World().scope_manager.get_module("app")
    snapshot = module.get_source_snapshot()
    assert snapshot.source == source and snapshot.is_package
    assert snapshot.original_bytes_digest == hashlib.sha256(raw).hexdigest()
    assert snapshot.encoding == "iso-8859-1"
    lowered = World().scope_manager.get_ir(module, "three address form")
    assert ast.dump(lowered) != ast.dump(ast.parse(source))
