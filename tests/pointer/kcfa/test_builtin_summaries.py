"""Tests for builtin handler registration."""

import pytest

from pythonstan.analysis.pointer.kcfa import Config, PointerAnalysisState
from pythonstan.analysis.pointer.kcfa.builtin_api_handler import BuiltinSummaryManager


def test_builtin_summary_manager_has_summaries():
    """BuiltinSummaryManager should report core builtins."""
    config = Config()
    state = PointerAnalysisState()
    manager = BuiltinSummaryManager(config)
    manager.set_state(state)

    expected = [
        "list",
        "dict",
        "tuple",
        "set",
        "frozenset",
        "iter",
        "next",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sorted",
        "reversed",
        "range",
        "sum",
        "min",
        "max",
        "all",
        "any",
        "len",
        "isinstance",
        "issubclass",
        "type",
        "getattr",
        "setattr",
        "hasattr",
        "vars",
        "dir",
        "globals",
        "locals",
        "delattr",
        "callable",
        "super",
        "bytes",
        "bytearray",
        "memoryview",
        "slice",
        "property",
        "classmethod",
        "staticmethod",
        "open",
        "object",
    ]

    for name in expected:
        assert manager.has_summary(name), f"Expected builtin summary for {name}"

    assert not manager.has_summary("not_a_builtin")
