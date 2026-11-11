"""Independent multi-file witnesses for Python's application import boundary.

Expected values are source-reviewed. No runtime traces are fed to the solver.
"""
from contextlib import contextmanager

from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.object import ConstantObject, ModuleObject
from pythonstan.world import World
from pythonstan.world.pipeline import Pipeline
from tests.analysis.legacy_ai_support import isolated_world


@contextmanager
def analyze_files(tmp_path, files, entry="entry.py"):
    for relative, source in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    with isolated_world():
        pipeline = Pipeline(config={
            "filename": str(tmp_path / entry), "project_path": str(tmp_path),
            "library_paths": [], "import_level": -1, "mock_libs": False,
            "analysis": [{"name": "pta", "id": "PointerAnalysis", "prev_analysis": ["ir"],
                          "options": Config(entry_points=[], max_iterations=50000, log_level="ERROR").to_dict()}],
        })
        pipeline.run()
        query = pipeline.analysis_manager.get_results("pta").query()
        yield query, pipeline


def constants(query, name, module="entry"):
    ir = World().scope_manager.get_module(module)
    return {obj.value for obj in query.points_to_at(name, ir)
            if isinstance(obj, ConstantObject)}


def edges(query):
    return {(edge.callsite.scope.stmt.get_qualname(), edge.callee.stmt.get_qualname())
            for edge in query.call_graph().edges}


def test_existing_package_attribute_precedes_same_named_submodule(tmp_path):
    files = {
        "entry.py": "from package import item\nresult = item()\n",
        "package/__init__.py": "def selected():\n    return 7\nitem = selected\n",
        "package/item.py": "def decoy():\n    return 99\n",
    }
    with analyze_files(tmp_path, files) as (query, _):
        assert 7 in constants(query, "result")
        assert ("entry", "package.selected") in edges(query)


def test_dotted_import_initializes_parent_and_binds_parent(tmp_path):
    files = {
        "entry.py": "import package.child\nresult = package.child.run()\nparent = package.callback()\n",
        "package/__init__.py": "def callback():\n    return 13\n",
        "package/child.py": "def run():\n    return 17\n",
    }
    with analyze_files(tmp_path, files) as (query, _):
        assert 17 in constants(query, "result")
        assert 13 in constants(query, "parent")
        assert {("entry", "package.child.run"), ("entry", "package.callback")} <= edges(query)


def test_empty_relative_from_import_resolves_sibling(tmp_path):
    files = {
        "entry.py": "from package.service import run\nresult = run()\n",
        "package/__init__.py": "",
        "package/service.py": "from . import callbacks\ndef run():\n    return callbacks.selected()\n",
        "package/callbacks.py": "def selected():\n    return 19\n",
    }
    with analyze_files(tmp_path, files) as (query, _):
        assert 19 in constants(query, "result")
        assert ("package.service.run", "package.callbacks.selected") in edges(query)


def test_same_imported_module_shares_registry_mutation_across_aliases(tmp_path):
    files = {
        "entry.py": "import registry as first\nimport registry as second\nfirst.callback = selected\nresult = second.callback()\n",
        "registry.py": "callback = None\n",
    }
    # The function declaration must precede the actual source assignment.
    files["entry.py"] = "def selected():\n    return 23\n" + files["entry.py"]
    with analyze_files(tmp_path, files) as (query, _):
        assert 23 in constants(query, "result")
        ir = World().scope_manager.get_module("entry")
        first = {obj for obj in query.points_to_at("first", ir) if isinstance(obj, ModuleObject)}
        second = {obj for obj in query.points_to_at("second", ir) if isinstance(obj, ModuleObject)}
        assert first and first == second


def test_cycle_reexports_preserve_callback_identity(tmp_path):
    files = {
        "entry.py": "from package.left import run\nresult = run()\n",
        "package/__init__.py": "",
        "package/left.py": "def selected():\n    return 29\nfrom .right import invoke\ndef run():\n    return invoke()\n",
        "package/right.py": "from .left import selected\ndef invoke():\n    return selected()\n",
    }
    with analyze_files(tmp_path, files) as (query, _):
        assert 29 in constants(query, "result")
        assert ("package.right.invoke", "package.left.selected") in edges(query)


def test_identical_basenames_in_distinct_packages_do_not_overwrite_exports(tmp_path):
    files = {
        "entry.py": "from alpha.service import run as a\nfrom beta.service import run as b\nleft=a()\nright=b()\n",
        "alpha/__init__.py": "", "beta/__init__.py": "",
        "alpha/service.py": "def run():\n    return 31\n",
        "beta/service.py": "def run():\n    return 37\n",
    }
    with analyze_files(tmp_path, files) as (query, _):
        assert constants(query, "left") == {31}
        assert constants(query, "right") == {37}


def test_lowering_later_module_preserves_each_module_import_ledger(tmp_path):
    files = {
        "entry.py": "import left\nimport right\n",
        "left.py": "import leaf\n",
        "right.py": "answer = 41\n",
        "leaf.py": "answer = 43\n",
    }
    with analyze_files(tmp_path, files):
        manager = World().scope_manager
        actual = {name: tuple(stmt.name for stmt in manager.get_ir(manager.get_module(name), "imports"))
                  for name in ("entry", "left", "right", "leaf")}
        assert actual == {"entry": ("left", "right"), "left": ("leaf",), "right": (), "leaf": ()}


def test_dotted_import_alias_denotes_child_module(tmp_path):
    files = {
        "entry.py": "import package.child as selected\nresult=selected.run()\n",
        "package/__init__.py": "",
        "package/child.py": "def run():\n    return 47\n",
    }
    with analyze_files(tmp_path, files) as (query, _):
        assert 47 in constants(query, "result")
        assert ("entry", "package.child.run") in edges(query)


def test_function_local_relative_import_preserves_defining_package(tmp_path):
    files = {
        "entry.py": "from package.service import run\nresult=run()\n",
        "package/__init__.py": "",
        "package/service.py": "def run():\n    from .callbacks import selected\n    return selected()\n",
        "package/callbacks.py": "def selected():\n    return 53\n",
    }
    with analyze_files(tmp_path, files) as (query, _):
        assert 53 in constants(query, "result")
        assert ("package.service.run", "package.callbacks.selected") in edges(query)


def test_literal_star_export_can_include_private_callable(tmp_path):
    files = {
        "entry.py": "from callbacks import *\nresult=_selected()\n",
        "callbacks.py": "__all__=['_selected']\ndef _selected():\n    return 59\n",
    }
    with analyze_files(tmp_path, files) as (query, _):
        assert 59 in constants(query, "result")
        assert ("entry", "callbacks._selected") in edges(query)


def test_reexport_chain_preserves_same_mutable_object(tmp_path):
    files = {
        "entry.py": "from bridge import registry\nfrom package.shared import registry as original\nregistry['callback']=selected\nresult=original['callback']()\n",
        "bridge.py": "from package.shared import registry\n",
        "package/__init__.py": "",
        "package/shared.py": "registry={}\n",
    }
    files["entry.py"] = "def selected():\n    return 61\n" + files["entry.py"]
    with analyze_files(tmp_path, files) as (query, _):
        assert 61 in constants(query, "result")
