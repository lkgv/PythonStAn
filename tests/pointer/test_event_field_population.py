"""Allocation metadata survives the production IR-to-constraint boundary.

The removed prototype emitted dictionaries called AllocEvent; current analysis
keeps its IR statement in AllocConstraint and emits explicit heap stores.
"""
import ast
import pytest
from pythonstan.analysis.pointer.kcfa import Config, AllocKind
from pythonstan.analysis.pointer.kcfa.constraints import AllocConstraint, StoreConstraint
from pythonstan.analysis.pointer.kcfa.heap_model import elem, key
from pythonstan.analysis.pointer.kcfa.ir_translator import IRTranslator
from pythonstan.ir.ir_statements import IRAssign, IRClass, IRFunc, IRModule
from pythonstan.world import World


@pytest.fixture
def translate():
    world = World()
    world.setup()
    world.scope_manager.build()
    module = IRModule("events", ast.parse(""), name="events", filename="events.py")
    translator = IRTranslator(Config(index_sensitive=True))
    def run(statement):
        world.scope_manager.set_ir(module, "ir", [statement])
        return translator.translate_module(module)
    return run


def check_container(translate, source, kind, expected):
    statement = IRAssign(ast.parse(source).body[0])
    constraints = translate(statement)
    allocations = [c for c in constraints if isinstance(c, AllocConstraint)]
    assert len(allocations) == 1
    assert allocations[0].alloc_site.stmt is statement
    assert allocations[0].alloc_site.kind == kind
    assert allocations[0].target.name == "tmp_1"
    stores = [c for c in constraints if isinstance(c, StoreConstraint) and c.field == elem()]
    assert [c.source.name for c in stores] == expected
    return constraints


def test_container_elements_extraction(translate):
    constraints = check_container(translate, "tmp_1 = [x, y, z]", AllocKind.LIST, ["x", "y", "z"])
    assert {(c.field, c.source.name) for c in constraints if isinstance(c, StoreConstraint) and c.field != elem()} == {(key(i), name) for i, name in enumerate(["x", "y", "z"])}


def test_dict_values_extraction(translate):
    constraints = check_container(translate, 'tmp_1 = {"a": x, "b": y}', AllocKind.DICT, ["x", "y"])
    assert {(c.field, c.source.name) for c in constraints if isinstance(c, StoreConstraint) and c.field != elem()} == {(key("a"), "x"), (key("b"), "y")}


def test_class_bases_extraction(translate):
    statement = IRClass("events.Dog", ast.parse("class Dog(Animal, Mammal): pass").body[0])
    constraints = translate(statement)
    allocation = next(c for c in constraints if isinstance(c, AllocConstraint))
    assert allocation.target.name == "Dog"
    assert allocation.alloc_site.kind == AllocKind.CLASS
    assert allocation.alloc_site.stmt is statement
    assert [base.name for base in statement._kcfa_base_variables] == ["Animal", "Mammal"]


def test_closure_vars_extraction(translate):
    statement = IRFunc("events.inner", ast.parse("def inner(): return x + y").body[0], cell_vars={"x", "y"})
    constraints = translate(statement)
    allocation = next(c for c in constraints if isinstance(c, AllocConstraint))
    assert allocation.target.name == "inner"
    assert allocation.alloc_site.kind == AllocKind.FUNCTION
    assert allocation.alloc_site.stmt.get_cell_vars() == {"x", "y"}


def test_tuple_elements_extraction(translate):
    constraints = check_container(translate, "tmp_1 = (a, b, c)", AllocKind.TUPLE, ["a", "b", "c"])
    assert {(c.field, c.source.name) for c in constraints if isinstance(c, StoreConstraint) and c.field != elem()} == {(key(i), name) for i, name in enumerate(["a", "b", "c"])}


def test_set_elements_extraction(translate):
    check_container(translate, "tmp_1 = {x, y}", AllocKind.SET, ["x", "y"])
