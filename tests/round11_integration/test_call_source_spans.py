"""A discarded call cannot be reassigned to module line one on a TAC revisit."""
import ast

import pytest

from pythonstan.analysis.transform.three_address import ThreeAddressTransformer


@pytest.mark.parametrize("statement", ["mapping.get(key)", "result = mapping.get(key)",
    "result = outer(mapping.get(key))", "mapping.get(key); mapping.setdefault(key)"])
def test_lowered_calls_preserve_each_original_source_span(statement):
    source = "\n\n\n" + statement + "\n"
    original = ast.parse(source)
    expected = sorted((n.lineno, n.col_offset, n.end_lineno, n.end_col_offset)
                      for n in ast.walk(original) if isinstance(n, ast.Call))
    lowered = ThreeAddressTransformer().visit(original)
    observed = sorted((n.lineno, n.col_offset, n.end_lineno, n.end_col_offset)
                      for n in ast.walk(lowered) if isinstance(n, ast.Call))
    assert observed == expected
