"""A fresh analysis cannot inherit instance-shadowed program services."""
import ast

from pythonstan.ir import IRModule
from pythonstan.world import World
from pythonstan.world.scope_manager import ModuleGraph, ScopeManager


def test_setup_discards_shadowed_program_graph_and_entry():
    world = World()
    world.setup()
    stale = ScopeManager()
    stale.build()
    module = IRModule("stale", ast.parse(""), "stale", "stale.py")
    graph = ModuleGraph()
    graph.add_node(module)
    stale.set_module_graph(graph)
    world.scope_manager = stale
    world.entry_module = module
    world.module2ns[module] = object()

    world.setup()
    world.scope_manager.build()

    assert world.scope_manager is not stale
    assert world.scope_manager.get_scopes() == set()
    assert not hasattr(world.scope_manager, "module_graph")
    assert world.entry_module is None
    assert world.module2ns == {}


def test_setup_replaces_all_instance_service_bindings():
    world = World()
    world.setup()
    names = ("namespace_manager", "class_hierarchy", "import_manager")
    stale = {name: object() for name in names}
    for name, value in stale.items():
        setattr(world, name, value)

    World.setup()

    assert all(getattr(world, name) is not value for name, value in stale.items())
