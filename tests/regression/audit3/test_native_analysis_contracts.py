"""Actual Native translator boundaries retain literal and import relations."""

from __future__ import annotations

import ast
from typing import Literal, override

import pytest

from pythonstan.analysis.pointer.kcfa.analysis import PointerAnalysis, _source_scope_key
from pythonstan.analysis.pointer.kcfa.config import Config
from pythonstan.analysis.pointer.kcfa.constraints import (
    AllocConstraint,
    LoadConstraint,
    ResidualEffectConstraint,
    StoreConstraint,
)
from pythonstan.analysis.pointer.kcfa.heap_model import NativeLiteralKey, key
from pythonstan.analysis.pointer.kcfa.ir_translator import IRTranslator
from pythonstan.ir.ir_statements import IRAssign, IRClass, IRFunc, IRImport, IRModule, IRScope
from pythonstan.world.scope_manager import ModuleGraph
from pythonstan.world.world import World


def _translator() -> tuple[IRTranslator, IRModule]:
    World.setup()
    world = World()
    world.scope_manager.build()
    world.scope_manager.set_module_graph(ModuleGraph())
    module = IRModule("entry", ast.parse(""), "entry", "entry.py")
    translator = IRTranslator(Config(index_sensitive=True))
    translator._current_scope = module
    return translator, module


class _ExtendedSourceScope(IRScope):
    filename: str = "plugin.py"

    def __init__(self) -> None:
        super().__init__("plugin.scope")
        self.node = ast.parse("pass").body[0]

    @override
    def get_ast(self) -> ast.AST:
        return self.node


def test_source_key_retains_valid_nominal_scope_extension() -> None:
    scope = _ExtendedSourceScope()
    assert _source_scope_key(scope) == ("plugin.scope", "plugin.py", 1, 0, "_ExtendedSourceScope")


def test_source_sort_key_uses_real_scope_coordinates_and_module_descent_is_optional() -> None:
    _, module = _translator()
    nodes = ast.parse("def callback():\n    pass\ndef callback():\n    pass\n").body
    assert isinstance(nodes[0], ast.FunctionDef)
    assert isinstance(nodes[1], ast.FunctionDef)
    first = IRFunc("entry.callback", nodes[0])
    second = IRFunc("entry.callback", nodes[1])
    assert _source_scope_key(first) == ("entry.callback", "", 1, 0, "IRFunc")
    assert sorted([second, first], key=_source_scope_key) == [first, second]
    world = World()
    pointer = PointerAnalysis.__new__(PointerAnalysis)
    pointer.world = world
    assert pointer._get_module_for_scope(first) is None
    world.scope_manager.father[first] = module
    assert pointer._get_module_for_scope(first) is module


@pytest.mark.parametrize("seed_kind", ["module", "import", "none"])
def test_module_translation_accepts_actual_seed_argument_and_preserves_cache(
    seed_kind: Literal["module", "import", "none"],
) -> None:
    translator, module = _translator()
    statement_node = ast.parse("value = None").body[0]
    assert isinstance(statement_node, ast.Assign)
    statement = IRAssign(statement_node)
    World().scope_manager.set_ir(module, "ir", [statement])
    seed: IRModule | IRImport | None
    if seed_kind == "module":
        seed = module
    elif seed_kind == "import":
        seed = IRImport(ast.Import(names=[ast.alias(name="entry")]))
    else:
        seed = None
    result = translator.translate_module(module, seed)
    assert len(result) == 1
    assert isinstance(result[0], AllocConstraint)
    assert result[0].target.name == "value"
    assert result[0].alloc_site.stmt is statement
    assert translator.translate_module(module, seed) is result


@pytest.mark.parametrize(
    "literal",
    [None, True, 7, 2.5, 1 + 2j, "name", b"name", Ellipsis, ("x", Ellipsis), frozenset({1, 2})],
)
def test_native_dictionary_constant_fields_preserve_full_source_literal_profile(
    literal: NativeLiteralKey,
) -> None:
    translator, _ = _translator()
    constant = ast.Constant(value=None)
    # CPython admits tuple/frozenset optimized constants at runtime although
    # typeshed's constructor intentionally lists only parser scalar constants.
    object.__setattr__(constant, "value", literal)
    assignment = ast.Assign(
        targets=[ast.Name(id="mapping", ctx=ast.Store())],
        value=ast.Dict(
            keys=[constant],
            values=[ast.Name(id="payload", ctx=ast.Load())],
        ),
    )
    constraints = translator._translate_assign(IRAssign(assignment))
    stores = [item for item in constraints if isinstance(item, StoreConstraint)]
    assert len(stores) == 2
    assert stores[0].field == key(literal)
    assert stores[0].source.name == "payload"
    assert stores[1].summary_only


def test_unresolved_import_without_a_module_owner_retains_open_effects() -> None:
    translator, _ = _translator()
    node = ast.parse("class Detached:\n    pass\n").body[0]
    assert isinstance(node, ast.ClassDef)
    translator._current_scope = IRClass("Detached", node)
    translator.used_variables = []
    statement = IRImport(ast.Import(names=[ast.alias(name="outside")]))
    constraints = translator._translate_import(statement)
    assert len(constraints) == 2
    assert isinstance(constraints[0], AllocConstraint)
    residual = constraints[1]
    assert isinstance(residual, ResidualEffectConstraint)
    assert residual.effects == (
        "unresolved_import",
        "may_mutate_heap",
        "may_invoke_callbacks",
        "may_raise",
    )


def test_star_import_keeps_private_lexical_exports_and_lazy_scope_metadata() -> None:
    translator, entry = _translator()
    scope_manager = World().scope_manager
    target = IRModule("package", ast.parse(""), "package", "package.py")
    statement = IRImport(ast.ImportFrom(module="package", names=[ast.alias(name="*")], level=0))
    graph = scope_manager.get_module_graph()
    graph.add_edge(entry, statement, target)
    assignment = IRAssign(
        ast.Assign(
            targets=[ast.Name(id="_private_callback", ctx=ast.Store())],
            value=ast.Constant(value=None),
        )
    )
    scope_manager.set_ir(target, "ir", [assignment])
    assert entry.peek_source_extra_exports() is None
    constraints = translator._translate_import(statement)
    assert entry.peek_source_extra_exports() == {"_private_callback"}
    loads = [item for item in constraints if isinstance(item, LoadConstraint)]
    assert [item.target.name for item in loads] == ["_private_callback"]
    assert any(
        isinstance(item, ResidualEffectConstraint) and "star_import_names" in item.effects
        for item in constraints
    )


@pytest.mark.parametrize("operator", [ast.Add(), ast.USub()])
def test_malformed_non_tac_operand_preserves_attribute_error(operator: ast.AST) -> None:
    translator, _ = _translator()
    if isinstance(operator, ast.operator):
        expression: ast.expr = ast.BinOp(
            left=ast.Constant(value=1), op=operator, right=ast.Name(id="x", ctx=ast.Load())
        )
    else:
        assert isinstance(operator, ast.unaryop)
        expression = ast.UnaryOp(op=operator, operand=ast.Constant(value=1))
    statement = IRAssign(
        ast.Assign(targets=[ast.Name(id="target", ctx=ast.Store())], value=expression)
    )
    with pytest.raises(AttributeError, match="'Constant' object has no attribute 'id'"):
        translator._translate_assign(statement)
