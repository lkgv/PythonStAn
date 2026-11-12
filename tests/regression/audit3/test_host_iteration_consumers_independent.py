"""Actual consumers must enter each host iterator exactly as direct Python does."""

from __future__ import annotations

from typing import Self

from pythonstan.analysis.pointer.kcfa.builtin_api_handler import _has_expanded_positionals
from pythonstan.compat.legacy_ai.value_domain import AbsVal


class _SingleEntryIterator:
    def __init__(self, items: tuple[object, ...]) -> None:
        self.items = items
        self.index = 0
        self.iter_calls = 0

    def __iter__(self) -> Self:
        self.iter_calls += 1
        if self.iter_calls != 1:
            raise RuntimeError("iterator was re-entered")
        return self

    def __next__(self) -> object:
        if self.index >= len(self.items):
            raise StopIteration
        item = self.items[self.index]
        self.index += 1
        return item


class _Statement:
    def __init__(self, arguments: object) -> None:
        self.arguments = arguments

    def get_args(self) -> object:
        return self.arguments


def test_expanded_metadata_enters_outer_iterator_once() -> None:
    baseline = _SingleEntryIterator((("value", True),))
    assert any(starred for _, starred in baseline)
    actual = _SingleEntryIterator((("value", True),))
    assert _has_expanded_positionals(_Statement(actual))
    assert actual.iter_calls == 1


def test_expanded_metadata_unpacks_each_pair_with_one_iterator_entry() -> None:
    baseline = _SingleEntryIterator(("value", True))
    _, starred = baseline
    assert starred is True
    actual = _SingleEntryIterator(("value", True))
    assert _has_expanded_positionals(_Statement([actual]))
    assert actual.iter_calls == 1


def test_real_legacy_string_consumer_enters_self_iterator_once() -> None:
    baseline = _SingleEntryIterator(("a", "b"))
    assert frozenset(baseline) == frozenset({"a", "b"})
    actual = _SingleEntryIterator(("a", "b"))
    assert AbsVal(str_const=actual).get_string_lattice().get_consts() == frozenset({"a", "b"})
    assert actual.iter_calls == 1
