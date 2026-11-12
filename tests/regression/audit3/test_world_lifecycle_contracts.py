"""Real World/Pipeline lifecycle controls at the checked source boundary."""

import ast
from pathlib import Path

import pytest

from pythonstan.ir import IRImport, IRModule, IRPass
from pythonstan.world.pipeline import Pipeline
from pythonstan.world.scope_manager import ScopeManager
from pythonstan.world.world import World


def test_world_entry_is_absent_until_real_source_module_is_loaded(tmp_path: Path) -> None:
    World.setup()
    assert World().get_entry_module() is None
    with pytest.raises(ValueError, match="Entry module has not been loaded"):
        Pipeline._require_entry_module()

    source = tmp_path / "subject.py"
    source.write_text("answer=42\n", encoding="utf-8")
    pipeline = Pipeline(config={"filename": str(source), "mock_libs": False})
    assert pipeline._require_entry_module() is World().get_entry_module()
    assert pipeline._require_entry_module().get_qualname() == "subject"


def test_missing_pipeline_entry_fails_before_publishing_module_graph(tmp_path: Path) -> None:
    missing = tmp_path / "missing.py"
    with pytest.raises(ValueError, match="Entry module could not be loaded"):
        Pipeline(config={"filename": str(missing), "mock_libs": False})
    assert World().get_entry_module() is None


def test_checked_ir_reads_preserve_canonical_rows_and_container_identity() -> None:
    manager = ScopeManager()
    manager.build()
    module = IRModule("subject", ast.parse(""), "subject", "subject.py")
    statement = IRPass(ast.Pass())
    imported = IRImport(ast.Import(names=[ast.alias(name="dependency")]))
    statements = [statement]
    imports = (imported,)
    assert manager.get_statement_ir(module) is None
    assert manager.get_import_ir(module) is None
    manager.set_ir(module, "ir", statements)
    manager.set_ir(module, "imports", imports)
    assert manager.get_statement_ir(module) is statements
    assert manager.get_import_ir(module) is imports


def test_checked_ir_reads_reject_opaque_rows_but_preserve_open_registry() -> None:
    manager = ScopeManager()
    manager.build()
    module = IRModule("subject", ast.parse(""), "subject", "subject.py")
    bad_statements = [object()]
    bad_imports = [IRPass(ast.Pass())]
    manager.set_ir(module, "ir", bad_statements)
    manager.set_ir(module, "imports", bad_imports)
    assert manager.get_ir(module, "ir") is bad_statements
    assert manager.get_ir(module, "imports") is bad_imports
    with pytest.raises(TypeError, match="IRStatement"):
        manager.get_statement_ir(module)
    with pytest.raises(TypeError, match="IRImport"):
        manager.get_import_ir(module)
    manager.scope_ir.pop((module.get_qualname(), "ir"))
    manager.scope_ir.pop((module.get_qualname(), "imports"))
    assert manager.get_statement_ir(module) is None
    assert manager.get_import_ir(module) is None
