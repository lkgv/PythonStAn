"""The real IR collectors retain observed variable identity and rename ownership."""

import ast

from pythonstan.utils.ast_rename import RenameTransformer
from pythonstan.utils.var_collector import VarCollector


def test_variable_collector_lookup_uses_the_collected_index() -> None:
    collector = VarCollector("load")
    collector.visit(ast.parse("result = first + second + first"))
    assert collector.get_vars() == {"first", "second"}
    assert collector.find("first") == 0
    assert collector.find(ast.Name(id="second", ctx=ast.Load())) == 1
    assert collector.find("missing") is None
    assert collector.find(3) is None


def test_rename_returns_the_original_or_a_separate_matching_leaf() -> None:
    original = ast.Name(id="before", ctx=ast.Load())
    transformer = RenameTransformer("before", "after", (ast.Load,))
    renamed = transformer.visit_Name(original)
    assert renamed is not original
    assert renamed.id == "after"
    assert original.id == "before"
    unrelated = ast.Name(id="other", ctx=ast.Load())
    assert transformer.visit_Name(unrelated) is unrelated
    alias = ast.alias(name="before")
    changed_alias = RenameTransformer("before", "after", (ast.Store,)).visit_alias(alias)
    assert changed_alias is not alias
    assert changed_alias.asname == "after"
    assert alias.asname is None
