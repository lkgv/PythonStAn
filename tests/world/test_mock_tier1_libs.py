from pathlib import Path

from pythonstan.analysis.pointer.kcfa.config import Config as KCFAConfig
from pythonstan.analysis.pointer.kcfa.object import FunctionObject, ModuleObject
from pythonstan.analysis.pointer.kcfa.variable import VariableKind
from pythonstan.world import World
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


def test_functools_partial_preserves_flow(tmp_path):
    entry_path = tmp_path / "main.py"
    entry_path.write_text(
        "from functools import partial\n"
        "def add(x, y):\n"
        "    return x + y\n"
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
                "description": "Pointer analysis for functools",
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
    
    pipeline.run()

    analysis = pipeline.analysis_manager.get_analyzer("pointer_analysis")
    state = analysis.state
    entry_scope = _module_scope(state, entry_mod)

    add5_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "add5",
        VariableKind.GLOBAL,
    )
    assert len(add5_pts) > 0


def test_json_loads_preserves_input(tmp_path):
    entry_path = tmp_path / "main.py"
    entry_path.write_text(
        "import json\n"
        "data = '{\"key\": \"value\"}'\n"
        "parsed = json.loads(data)\n"
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
                "description": "Pointer analysis for json",
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
    
    pipeline.run()

    analysis = pipeline.analysis_manager.get_analyzer("pointer_analysis")
    state = analysis.state
    entry_scope = _module_scope(state, entry_mod)

    parsed_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "parsed",
        VariableKind.GLOBAL,
    )
    assert len(parsed_pts) > 0


def test_re_pattern_preserves_flow(tmp_path):
    entry_path = tmp_path / "main.py"
    entry_path.write_text(
        "import re\n"
        "pattern = r'[a-z]+'\n"
        "regex = re.compile(pattern)\n"
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
                "description": "Pointer analysis for re",
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
    
    pipeline.run()

    analysis = pipeline.analysis_manager.get_analyzer("pointer_analysis")
    state = analysis.state
    entry_scope = _module_scope(state, entry_mod)

    regex_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "regex",
        VariableKind.GLOBAL,
    )
    assert len(regex_pts) > 0


def test_contextmanager_preserves_wrapper(tmp_path):
    entry_path = tmp_path / "main.py"
    entry_path.write_text(
        "from contextlib import contextmanager\n"
        "@contextmanager\n"
        "def my_context():\n"
        "    yield 42\n"
        "with my_context() as value:\n"
        "    result = value\n"
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
                "description": "Pointer analysis for contextlib",
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
    
    pipeline.run()

    analysis = pipeline.analysis_manager.get_analyzer("pointer_analysis")
    state = analysis.state
    entry_scope = _module_scope(state, entry_mod)

    my_context_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "my_context",
        VariableKind.GLOBAL,
    )
    assert any(isinstance(obj, FunctionObject) for obj in my_context_pts)


def test_pathlib_path_operations(tmp_path):
    entry_path = tmp_path / "main.py"
    entry_path.write_text(
        "from pathlib import Path\n"
        "p = Path('/tmp/test.txt')\n"
        "parent = p.parent\n"
        "name = p.name\n"
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
                "description": "Pointer analysis for pathlib",
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
    
    pipeline.run()

    analysis = pipeline.analysis_manager.get_analyzer("pointer_analysis")
    state = analysis.state
    entry_scope = _module_scope(state, entry_mod)

    p_pts = _points_to_var(
        state,
        entry_scope,
        analysis.solver.variable_factory,
        "p",
        VariableKind.GLOBAL,
    )
    assert len(p_pts) > 0
