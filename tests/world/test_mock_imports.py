from pathlib import Path

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config as KCFAConfig
from pythonstan.analysis.pointer.kcfa.object import ClassObject, FunctionObject, ModuleObject
from pythonstan.analysis.pointer.kcfa.variable import VariableKind
from pythonstan.ir import IRImport
from pythonstan.world import World, namespace as namespace_module
from pythonstan.world.pipeline import Pipeline


def _module_scope(state, ir_module):
    for obj, scope in state._internal_scope.items():
        if isinstance(obj, ModuleObject) and obj.ir is ir_module:
            return scope
    raise AssertionError("Module scope not found")


def _points_to_var(state, scope, var_factory, name: str, kind: VariableKind):
    var = var_factory.make_variable(name, kind)
    ctx_var = state.get_variable(scope, scope.context, var)
    return state.get_points_to(ctx_var)


def test_mock_import_resolution_and_analysis(tmp_path):
    entry_path = tmp_path / "main.py"
    entry_path.write_text(
        "import somelib\n"
        "x = somelib.add(1, 2)\n"
        "from somelib import add\n"
        "y = add(1, 2)\n"
    )

    kcfa_config = KCFAConfig(
        context_policy="1-cfa",
        max_iterations=2000,
        verbose=False,
        log_level="WARNING",
        track_unknowns=True,
        build_class_hierarchy=True,
        use_mro_resolution=True,
        index_sensitive=False,
        project_path=str(tmp_path),
    )

    config = {
        "filename": str(entry_path),
        "project_path": str(tmp_path),
        "analysis": [
            {
                "name": "pointer_analysis",
                "id": "PointerAnalysis",
                "description": "Pointer analysis for mock imports",
                "prev_analysis": ["ir"],
                "options": kcfa_config.to_dict(),
            }
        ],
        "library_paths": [],
        "mock_libs": True,
        "prefer_mock_libs": False,
    }

    pipeline = Pipeline(config=config)

    world = World()
    entry_mod = world.get_entry_module()
    ir_stmts = world.scope_manager.get_ir(entry_mod, "ir")
    assert ir_stmts, "Expected IR statements to be captured"
    imports = [stmt for stmt in ir_stmts if isinstance(stmt, IRImport)]
    assert imports, "Expected import statements to be captured"

    module_graph = world.scope_manager.get_module_graph()
    import_somelib = next(
        stmt for stmt in imports if stmt.module is None and stmt.name == "somelib"
    )
    import_add = next(
        stmt
        for stmt in imports
        if stmt.module == "somelib" and stmt.name == "add"
    )

    somelib_mod = module_graph.get_succ_module(entry_mod, import_somelib)
    add_mod = module_graph.get_succ_module(entry_mod, import_add)
    assert somelib_mod is not None
    assert add_mod is not None
    assert somelib_mod is add_mod

    mock_root = (
        Path(namespace_module.__file__).resolve().parents[2]
        / "stubs"
        / "stdlib"
    )
    expected_path = str((mock_root / "somelib.py").resolve())
    assert somelib_mod.get_filename() == expected_path

    pipeline.run()

    analysis = pipeline.analysis_manager.get_analyzer("pointer_analysis")
    state = analysis.state
    entry_scope = _module_scope(state, entry_mod)

    somelib_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "somelib",
        VariableKind.GLOBAL,
    )
    somelib_obj = next(obj for obj in somelib_pts if isinstance(obj, ModuleObject))
    somelib_scope = state.get_internal_scope(somelib_obj)
    assert somelib_scope is not None

    add_pts = _points_to_var(
        state,
        somelib_scope,
        analysis.solver.variable_factory,
        "add",
        VariableKind.GLOBAL,
    )
    assert any(isinstance(obj, FunctionObject) for obj in add_pts)

    entry_add_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "add",
        VariableKind.GLOBAL,
    )
    assert any(isinstance(obj, FunctionObject) for obj in entry_add_pts)


def test_tier2_mock_dataclasses_import(tmp_path):
    """Test that dataclasses mock is importable and has key exports."""
    entry_path = tmp_path / "main.py"
    entry_path.write_text(
        "from dataclasses import dataclass, field, fields, asdict\n"
        "@dataclass\n"
        "class Point:\n"
        "    x: int\n"
        "    y: int\n"
        "p = Point(1, 2)\n"
    )

    kcfa_config = KCFAConfig(
        context_policy="1-cfa",
        max_iterations=2000,
        verbose=False,
        log_level="WARNING",
        track_unknowns=True,
        build_class_hierarchy=True,
        use_mro_resolution=True,
        index_sensitive=False,
        project_path=str(tmp_path),
    )

    config = {
        "filename": str(entry_path),
        "project_path": str(tmp_path),
        "analysis": [
            {
                "name": "pointer_analysis",
                "id": "PointerAnalysis",
                "description": "Pointer analysis for dataclasses mock",
                "prev_analysis": ["ir"],
                "options": kcfa_config.to_dict(),
            }
        ],
        "library_paths": [],
        "mock_libs": True,
        "prefer_mock_libs": True,
    }

    pipeline = Pipeline(config=config)

    world = World()
    entry_mod = world.get_entry_module()
    ir_stmts = world.scope_manager.get_ir(entry_mod, "ir")
    assert ir_stmts, "Expected IR statements"

    imports = [stmt for stmt in ir_stmts if isinstance(stmt, IRImport)]
    assert any(stmt.module == "dataclasses" for stmt in imports)

    pipeline.run()

    analysis = pipeline.analysis_manager.get_analyzer("pointer_analysis")
    state = analysis.state
    entry_scope = _module_scope(state, entry_mod)

    dataclass_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "dataclass",
        VariableKind.GLOBAL,
    )
    assert len(dataclass_pts) > 0, "dataclass should point to something"

    point_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "Point",
        VariableKind.GLOBAL,
    )
    assert any(isinstance(obj, ClassObject) for obj in point_pts), "Point should be a class"


def test_tier2_mock_functools_partial(tmp_path):
    """Test functools.partial preserves dataflow to wrapped function."""
    entry_path = tmp_path / "main.py"
    entry_path.write_text(
        "from functools import partial\n"
        "def add(a, b):\n"
        "    return a + b\n"
        "add5 = partial(add, 5)\n"
        "result = add5(3)\n"
    )

    kcfa_config = KCFAConfig(
        context_policy="1-cfa",
        max_iterations=2000,
        verbose=False,
        log_level="WARNING",
        track_unknowns=True,
        build_class_hierarchy=True,
        use_mro_resolution=True,
        index_sensitive=False,
        project_path=str(tmp_path),
    )

    config = {
        "filename": str(entry_path),
        "project_path": str(tmp_path),
        "analysis": [
            {
                "name": "pointer_analysis",
                "id": "PointerAnalysis",
                "description": "Pointer analysis for functools.partial",
                "prev_analysis": ["ir"],
                "options": kcfa_config.to_dict(),
            }
        ],
        "library_paths": [],
        "mock_libs": True,
        "prefer_mock_libs": True,
    }

    pipeline = Pipeline(config=config)
    pipeline.run()

    world = World()
    entry_mod = world.get_entry_module()
    analysis = pipeline.analysis_manager.get_analyzer("pointer_analysis")
    state = analysis.state
    entry_scope = _module_scope(state, entry_mod)

    add_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "add",
        VariableKind.GLOBAL,
    )
    assert any(isinstance(obj, FunctionObject) for obj in add_pts), "add should be a function"

    add5_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "add5",
        VariableKind.GLOBAL,
    )
    assert len(add5_pts) > 0, "add5 should point to partial instance"
