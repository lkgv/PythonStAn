"""Independent source-plugin controls for the Native argument metadata boundary."""

from __future__ import annotations

import ast

import pytest

from pythonstan.analysis.pointer.kcfa.builtin_api_handler import BuiltinAPIHandler
from pythonstan.analysis.pointer.kcfa.constraints import CallConstraint, ResidualEffectConstraint
from pythonstan.analysis.pointer.kcfa.context import CallSite
from pythonstan.analysis.pointer.kcfa.variable import Variable
from pythonstan.ir.ir_statements import IRFunc


class _TruthCountingReader:
    def __init__(self, becomes_truthy: bool) -> None:
        self.becomes_truthy = becomes_truthy
        self.truth_calls = 0
        self.metadata_calls = 0

    def __bool__(self) -> bool:
        self.truth_calls += 1
        return self.becomes_truthy and self.truth_calls > 1

    def __call__(self) -> tuple[tuple[str, bool], ...]:
        self.metadata_calls += 1
        return ()


class _PluginStatement(IRFunc):
    """A real nominal IR statement whose optional metadata service is a callable."""

    def __init__(self, reader: _TruthCountingReader) -> None:
        function = ast.parse("def plugin():\n    pass\n").body[0]
        assert isinstance(function, ast.FunctionDef)
        super().__init__("plugin", function)
        self.get_args = reader


@pytest.mark.parametrize("becomes_truthy", [False, True])
def test_falsy_metadata_service_preserves_outer_truth_conversion(becomes_truthy: bool) -> None:
    reader = _TruthCountingReader(becomes_truthy)
    statement = _PluginStatement(reader)
    call = CallConstraint(Variable("callee"), (), frozenset(), None, CallSite(statement))

    invalid, constraints = BuiltinAPIHandler._dict_default_argument_status(call, "get")

    # The established source boundary evaluates bool(reader and any(...)).
    # A false first conversion leaves the original reader for the outer bool;
    # no metadata call is permitted before either result is classified.
    assert reader.truth_calls == 2
    assert reader.metadata_calls == 0
    assert invalid is (not becomes_truthy)
    assert len(constraints) == 1
    assert isinstance(constraints[0], ResidualEffectConstraint)
    expected_effect = "argument_expansion" if becomes_truthy else "exception_routing"
    assert expected_effect in constraints[0].effects
