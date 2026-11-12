"""The Native tuple metadata boundary retains existing getter/call timing."""

from __future__ import annotations

import pytest

from pythonstan.analysis.pointer.kcfa.native_mapping import _source_ast


def test_source_metadata_uses_the_second_getter_value_without_testing_truth() -> None:
    payload = object()

    class Reader:
        def __init__(self) -> None:
            self.calls = 0

        def __bool__(self) -> bool:
            raise AssertionError("The existing source getter call does not test truth")

        def __call__(self) -> object:
            self.calls += 1
            return payload

    reader = Reader()

    class Source:
        def __init__(self) -> None:
            self.reads = 0

        @property
        def get_ast(self) -> object:
            self.reads += 1
            # The first access is the historical hasattr probe and must not
            # supply the reader that is actually called on the second access.
            return object() if self.reads == 1 else reader

    source = Source()
    assert _source_ast(source) is payload
    assert (source.reads, reader.calls) == (2, 1)


def test_absent_source_metadata_performs_one_failed_probe() -> None:
    class Source:
        def __init__(self) -> None:
            self.reads = 0

        def __getattr__(self, name: str) -> object:
            assert name == "get_ast"
            self.reads += 1
            raise AttributeError(name)

    source = Source()
    assert _source_ast(source) is None
    assert source.reads == 1


def test_second_getter_failure_is_not_reclassified_as_absent_metadata() -> None:
    failure = AttributeError("second lookup")

    class Source:
        def __init__(self) -> None:
            self.reads = 0

        @property
        def get_ast(self) -> object:
            self.reads += 1
            if self.reads == 2:
                raise failure
            return object()

    source = Source()
    with pytest.raises(AttributeError) as caught:
        _source_ast(source)
    assert caught.value is failure
    assert source.reads == 2


def test_reader_failure_preserves_the_original_exception_object() -> None:
    failure = RuntimeError("source metadata failure")

    class Source:
        def get_ast(self) -> object:
            raise failure

    with pytest.raises(RuntimeError) as caught:
        _source_ast(Source())
    assert caught.value is failure
