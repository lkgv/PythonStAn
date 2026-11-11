"""Multi-file semantic witnesses using the production Pipeline and one arena."""
from contextlib import contextmanager
from pathlib import Path

import pytest

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.object import ConstantObject, ModuleObject, BuiltinFunctionObject
from pythonstan.world import World
from pythonstan.world.namespace import Namespace, NamespaceManager
from pythonstan.world.pipeline import Pipeline
from tests.analysis.legacy_ai_support import isolated_world


@contextmanager
def analyze(tmp_path, files, entry="entry.py", *, depth=-1, synthetic=True):
    for filename, text in files.items():
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    with isolated_world():
        pipeline = Pipeline(config={
            "filename": str(tmp_path / entry), "project_path": str(tmp_path),
            "mock_libs": False, "import_level": depth,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                          "options": Config(entry_points=[], max_iterations=50000, log_level="ERROR",
                                            enable_synthetic_method_contexts=synthetic).to_dict()}],
        })
        pipeline.run()
        query = pipeline.analysis_manager.get_results("pta").query()
        yield pipeline, query


def consts(query, name, module="entry"):
    return {obj.value for obj in query.points_to_at(name, World().scope_manager.get_module(module))
            if isinstance(obj, ConstantObject)}


def calls(query):
    return {(e.callsite.scope.stmt.get_qualname(), e.callee.stmt.get_qualname())
            for e in query.call_graph().edges}


def test_imported_function_keeps_defining_module_global_environment(tmp_path):
    files = {
        "entry.py": "from app.api import dispatch\nresult=dispatch()\n",
        "app/__init__.py": "",
        "app/api.py": "from .registry import get\ndef dispatch():\n    return get()()\n",
        "app/registry.py": "from .callbacks import selected\ndef get():\n    return selected\n",
        "app/callbacks.py": "def selected():\n    return 41\n",
    }
    with analyze(tmp_path, files) as (_, query):
        assert 41 in consts(query, "result")
        assert {("app.api.dispatch", "app.registry.get"),
                ("app.api.dispatch", "app.callbacks.selected")} <= calls(query)


def test_relative_import_in_function_uses_lexical_module(tmp_path):
    files = {
        "entry.py": "from app.api import dispatch\nresult=dispatch()\n",
        "app/__init__.py": "",
        "app/api.py": "def dispatch():\n    from .callbacks import selected\n    return selected()\n",
        "app/callbacks.py": "def selected():\n    return 43\n",
        "unrelated.py": "value=99\n",
    }
    with analyze(tmp_path, files) as (_, query):
        assert 43 in consts(query, "result")
        assert ("app.api.dispatch", "app.callbacks.selected") in calls(query)


def test_module_singleton_and_builtin_bindings_survive_multiple_imports(tmp_path):
    files = {
        "entry.py": "import shared as left\nfrom relay import right\nresult=left.run()\n",
        "relay.py": "import shared as right\n",
        "shared.py": "def run():\n    return len([])\n",
    }
    with analyze(tmp_path, files) as (pipeline, query):
        entry = World().scope_manager.get_module("entry")
        left = {obj for obj in query.points_to_at("left", entry) if isinstance(obj, ModuleObject)}
        right = {obj for obj in query.points_to_at("right", entry) if isinstance(obj, ModuleObject)}
        assert len(left) == 1 and left == right
        shared = World().scope_manager.get_module("shared")
        assert any(isinstance(obj, BuiltinFunctionObject) and obj.function_name == "len"
                   for obj in query.points_to_at("len", shared))
        assert query.get_statistics()["converged"] == 1


def test_package_initializer_reexport_and_leaf_identity_are_both_preserved(tmp_path):
    files = {
        "entry.py": "import app.api as api\nfrom app import handle\nresult=handle()\nother=api.dispatch()\n",
        "app/__init__.py": "from .api import dispatch as handle\n",
        "app/api.py": "def dispatch():\n    return 47\n",
    }
    with analyze(tmp_path, files) as (_, query):
        assert 47 in consts(query, "result") and 47 in consts(query, "other")
        entry = World().scope_manager.get_module("entry")
        assert {obj.ir.get_qualname() for obj in query.points_to_at("api", entry)
                if isinstance(obj, ModuleObject)} == {"app.api"}


def test_imported_mutable_registry_write_reaches_module_function_global(tmp_path):
    files = {
        "entry.py": "import shared\ndef selected():\n    return 53\nshared.callback=selected\nresult=shared.run()\n",
        "shared.py": "callback=None\ndef run():\n    return callback()\n",
    }
    with analyze(tmp_path, files) as (_, query):
        assert 53 in consts(query, "result")
        assert ("shared.run", "entry.selected") in calls(query)


def test_star_import_keeps_private_explicit_exports(tmp_path):
    files = {
        "entry.py": "from callbacks import *\nresult=_selected()\n",
        "callbacks.py": "__all__=['_selected']\ndef _selected():\n    return 59\n",
    }
    with analyze(tmp_path, files) as (_, query):
        assert 59 in consts(query, "result")
        assert any("star_import_names" in item.get("effects", ()) for item in query.get_residual_effects())


def test_unresolved_import_retains_mandatory_obligation(tmp_path):
    with analyze(tmp_path, {"entry.py": "import absent_external\n"}) as (_, query):
        assert any("unresolved_import" in item.get("effects", ()) for item in query.get_residual_effects())
        assert not query.get_semantic_status()["certificate_eligible"]


def test_depth_limited_import_is_not_complete_module(tmp_path):
    files = {"entry.py": "import child\n", "child.py": "value=61\n"}
    with analyze(tmp_path, files, depth=0) as (_, query):
        assert any("untranslated_source" in item.get("effects", ()) for item in query.get_residual_effects())
        assert not query.get_semantic_status()["certificate_eligible"]


def test_import_metadata_is_qualified_and_package_specific(tmp_path):
    files = {
        "entry.py": "import app.api\n",
        "app/__init__.py": "name=__name__\npackage=__package__\n",
        "app/api.py": "name=__name__\npackage=__package__\nfilename=__file__\n",
    }
    with analyze(tmp_path, files) as (_, query):
        assert consts(query, "name", "app") == {"app"}
        assert consts(query, "package", "app") == {"app"}
        assert consts(query, "name", "app.api") == {"app.api"}
        assert consts(query, "package", "app.api") == {"app"}
        assert str(tmp_path / "app/api.py") in consts(query, "filename", "app.api")
        assert consts(query, "__name__") == {"entry"}


def test_relative_escape_is_rejected_before_lookup(tmp_path):
    manager = NamespaceManager()
    manager.build(str(tmp_path), [], mock_libs=False)
    module = tmp_path / "app/service.py"
    module.parent.mkdir()
    module.write_text("")
    ns = manager.set_entry_module(str(module), str(tmp_path))
    with pytest.raises(ImportError):
        manager.resolve_rel_importfrom(ns, "", "thing", 2)


def test_package_initializer_precedes_same_named_file(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app/__init__.py").write_text("")
    (tmp_path / "app.py").write_text("")
    manager = NamespaceManager()
    manager.build(str(tmp_path), [], mock_libs=False)
    _, path = manager.resolve_import("app")
    assert Path(path) == tmp_path / "app/__init__.py"


def test_script_metadata_requires_explicit_entry_contract(tmp_path):
    path = tmp_path / 'entry.py'
    path.write_text('runtime_name=__name__\nruntime_package=__package__\n')
    with isolated_world():
        pipeline = Pipeline(config={
            'filename': str(path), 'project_path': str(tmp_path), 'mock_libs': False,
            'analysis': [{'name': 'pta', 'id': 'PointerAnalysis', 'prev_analysis': ['ir'],
                          'options': Config(entry_points=[], log_level='ERROR').to_dict()}],
        })
        World().get_entry_module()._project_runtime_name = '__main__'
        World().get_entry_module()._project_runtime_package = None
        pipeline.run()
        query = pipeline.analysis_manager.get_results('pta').query()
        assert consts(query, 'runtime_name') == {'__main__'}
        assert consts(query, 'runtime_package') == {None}


@pytest.mark.parametrize('invoke', [False, True])
def test_synthetic_method_globals_stay_in_each_lexical_module(tmp_path, invoke):
    files = {
        'entry.py': 'def callback():\n    return 999\nimport alpha\nimport beta\n',
        'alpha.py': 'def callback():\n    return 67\nclass Worker:\n    def run(self):\n        return callback()\n',
        'beta.py': 'def callback():\n    return 71\nclass Worker:\n    def run(self):\n        return callback()\n',
    }
    if invoke:
        files['entry.py'] += 'left=alpha.Worker().run()\nright=beta.Worker().run()\n'
    with analyze(tmp_path, files) as (_, query):
        actual = calls(query)
        assert ('alpha.Worker.run', 'alpha.callback') in actual
        assert ('beta.Worker.run', 'beta.callback') in actual
        assert ('alpha.Worker.run', 'entry.callback') not in actual
        assert ('beta.Worker.run', 'entry.callback') not in actual
        assert ('alpha.Worker.run', 'beta.callback') not in actual
        assert ('beta.Worker.run', 'alpha.callback') not in actual
        if invoke:
            assert consts(query, 'left') == {67}
            assert consts(query, 'right') == {71}


def test_unknown_module_attribute_keeps_all_source_callbacks(tmp_path):
    files = {
        'entry.py': 'import plugin\ndef invoke(name):\n    return getattr(plugin, name)()\nresult=invoke(input())\n',
        'plugin.py': 'def first():\n    return 73\ndef second():\n    return 79\n',
    }
    with analyze(tmp_path, files) as (_, query):
        actual = calls(query)
        assert ('entry.invoke', 'plugin.first') in actual
        assert ('entry.invoke', 'plugin.second') in actual
        assert any('module_dynamic_exports' in r.get('effects', ()) for r in query.get_residual_effects())


def test_unknown_module_attribute_subscribes_to_late_alias_writes(tmp_path):
    files = {
        'entry.py': 'import plugin\ndef selected():\n    return 83\ndef install():\n    plugin.late=selected\ndef invoke(name):\n    return getattr(plugin,name)()\ninstall()\nresult=invoke(input())\n',
        'plugin.py': 'def first():\n    return 89\n',
    }
    with analyze(tmp_path, files) as (_, query):
        assert ('entry.invoke', 'entry.selected') in calls(query)


def test_unknown_module_field_watches_bindings_created_after_lookup(tmp_path):
    from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
    from pythonstan.analysis.pointer.kcfa.context import Scope, CallStringContext
    from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind
    from pythonstan.analysis.pointer.kcfa.variable import Variable, VariableKind
    from pythonstan.analysis.pointer.kcfa.heap_model import unknown
    from pythonstan.analysis.pointer.kcfa.points_to_set import PointsToSet
    from pythonstan.analysis.pointer.kcfa.pointer_flow_graph import NormalNode
    from pythonstan.ir import IRModule
    import ast
    with isolated_world():
        module = IRModule('plugin', ast.parse(''), 'plugin', str(tmp_path / 'plugin.py'))
        context = CallStringContext()
        obj = ModuleObject(context, AllocSite.from_ir_node(module, AllocKind.MODULE), module)
        scope = Scope.new(obj, None, context, module, None)
        state = PointerAnalysisState()
        state.set_internal_scope(obj, scope)
        aggregate = state.get_field(scope, context, obj, unknown())
        state.register_module_export(scope, 'late')
        late = state.get_variable(scope, context, Variable('late', VariableKind.GLOBAL))
        value = ConstantObject(context, AllocSite(('late-export', module), AllocKind.CONSTANT), 97)
        points = PointsToSet.singleton(value)
        state.set_points_to(late, points)
        for successor, incoming in state.pointer_flow_graph.propagate(NormalNode(late), points):
            state.set_points_to(successor.var, incoming)
        assert value in state.get_points_to(aggregate)


def test_unknown_module_reads_exclude_unowned_builtin_lookup_fallback(tmp_path):
    files = {
        'entry.py': 'import plugin\nselected=getattr(plugin,input())\n',
        'plugin.py': 'def actual():\n    return 101\n',
    }
    with analyze(tmp_path, files) as (_, query):
        objects = tuple(query.points_to_at('selected', World().scope_manager.get_module('entry')))
        assert not any(isinstance(obj, BuiltinFunctionObject) for obj in objects)
        from pythonstan.analysis.pointer.kcfa.object import FunctionObject
        assert any(isinstance(obj, FunctionObject) and obj.ir.get_qualname() == 'plugin.actual' for obj in objects)


def test_known_module_read_excludes_unowned_builtin_name(tmp_path):
    files = {
        'entry.py': "import plugin\nselected=getattr(plugin,'len',None)\n",
        'plugin.py': 'def actual():\n    return 103\n',
    }
    with analyze(tmp_path, files) as (_, query):
        objects = tuple(query.points_to_at('selected', World().scope_manager.get_module('entry')))
        assert not any(isinstance(obj, BuiltinFunctionObject) for obj in objects)
        assert any(isinstance(obj, ConstantObject) and obj.value is None for obj in objects)


def test_explicit_builtin_reexport_remains_owned_module_attribute(tmp_path):
    files = {
        'entry.py': "import plugin\nnamed=getattr(plugin,'len')\nselected=getattr(plugin,input())\n",
        'plugin.py': 'len=len\n',
    }
    with analyze(tmp_path, files) as (_, query):
        for name in ('named', 'selected'):
            objects = tuple(query.points_to_at(name, World().scope_manager.get_module('entry')))
            assert any(isinstance(obj, BuiltinFunctionObject) and obj.function_name == 'len' for obj in objects)


@pytest.mark.parametrize('synthetic', [False, True])
def test_method_entry_policy_retains_actual_calls_and_controls_extra_roots(tmp_path, synthetic):
    files = {
        'entry.py': 'from plugin import Worker\nresult=Worker().run()\n',
        'plugin.py': 'def selected():\n    return 107\ndef unused_callback():\n    return 109\nclass Worker:\n    def run(self):\n        return selected()\n    def unused(self):\n        return unused_callback()\n',
    }
    with analyze(tmp_path, files, synthetic=synthetic) as (_, query):
        actual = calls(query)
        assert ('plugin.Worker.run', 'plugin.selected') in actual
        assert 107 in consts(query, 'result')
        assert (('plugin.Worker.unused', 'plugin.unused_callback') in actual) == synthetic


def test_synthetic_method_entry_policy_is_validated_and_roundtrips():
    assert Config().enable_synthetic_method_contexts is True
    configuration = Config(enable_synthetic_method_contexts=False)
    assert Config.from_dict(configuration.to_dict()).enable_synthetic_method_contexts is False
    with pytest.raises(ValueError):
        Config(enable_synthetic_method_contexts=0)
